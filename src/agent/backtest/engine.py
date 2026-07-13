"""Event-driven бэктест-движок.

Принципы:
  * No-lookahead: сигнал считается по данным ДО бара t включительно, сделка
    исполняется по OPEN следующего бара t+1.
  * Cost-model: комиссия + слиппедж (адверсный к направлению).
  * Учёт в ЛОТАХ (как в проде), equity по стандартной модели
    cash + Σ signed_lots × lot_size × price.
  * Опционально прогоняет боевой Risk Officer (agent.runtime.risk.evaluate),
    чтобы валидировать риск-слой и калибровать пороги.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import structlog

from agent.backtest import metrics as metrics_mod
from agent.backtest.strategy import Signal, Strategy
from agent.config import settings as global_settings
from agent.runtime import risk as risk_mod
from agent.runtime.hours import MSK
from agent.runtime.sizing import quantity_for_buy
from agent.runtime.universe import LOT_SIZE_BY_TICKER


def _to_msk(t) -> pd.Timestamp:
    """MOEX-свечи tz-naive (московское время). risk.evaluate сравнивает даты
    через .astimezone(MSK) → нужен tz-aware timestamp."""
    ts = pd.Timestamp(t)
    return ts.tz_localize(MSK) if ts.tzinfo is None else ts.tz_convert(MSK)

log = structlog.get_logger()


@dataclass
class _Position:
    lots: float = 0.0
    avg_price: float = 0.0


@dataclass
class BacktestResult:
    equity: np.ndarray
    timestamps: list
    report: metrics_mod.BacktestReport
    trade_pnls: list[float] = field(default_factory=list)
    n_trades: int = 0
    gross_traded: float = 0.0  # Σ|trade notional| в рублях (для floor оборота)
    trades_per_day: float = 0.0
    gross_turnover_per_day: float = 0.0
    avg_exposure_pct: float = 0.0
    flat_time_pct: float = 1.0
    avg_holding_bars: float = 0.0
    tp_exit_count: int = 0
    tp_continuation_05_rate: float = 0.0
    tp_continuation_10_rate: float = 0.0


def buy_and_hold_equity(
    prices: dict[str, pd.DataFrame],
    timestamps: pd.DatetimeIndex,
    initial_capital: float = 1_000_000.0,
    warmup: int = 50,
) -> np.ndarray:
    """Эталон: равный вес по всем тикерам на старте (warmup), держим до конца.
    Без комиссий за удержание — чистый рыночный бенчмарк."""
    tickers = list(prices.keys())
    if not tickers or len(timestamps) <= warmup:
        return np.array([initial_capital], dtype=float)
    start_t = timestamps[warmup]
    alloc = initial_capital / len(tickers)
    shares: dict[str, float] = {}
    for tkr in tickers:
        df = prices[tkr]
        px = _price_at(df, start_t, "open") or _price_at(df, start_t, "close")
        shares[tkr] = (alloc / px) if px else 0.0
    out: list[float] = []
    for t in timestamps[warmup:]:
        nav = 0.0
        for tkr in tickers:
            px = _price_at(prices[tkr], t, "close")
            if px:
                nav += shares[tkr] * px
        out.append(nav)
    return np.asarray(out, dtype=float)


def _price_at(df: pd.DataFrame, t, col: str) -> float | None:
    try:
        if t in df.index:
            v = float(df.loc[t, col])
            return v if v > 0 else None
    except (KeyError, TypeError, ValueError):
        return None
    return None


def run_backtest(
    prices: dict[str, pd.DataFrame],
    strategy: Strategy,
    *,
    settings=None,
    initial_capital: float = 1_000_000.0,
    periods_per_year: int = metrics_mod.TRADING_DAYS,
    commission_rate: float = 0.0005,
    slippage_bps: float = 2.0,
    apply_risk: bool = True,
    warmup: int = 50,
    lot_sizes: dict[str, int] | None = None,
) -> BacktestResult:
    s = settings or global_settings
    lot_sizes = lot_sizes or dict(LOT_SIZE_BY_TICKER)
    slippage = slippage_bps / 10_000.0

    timestamps = _aligned(prices)
    if len(timestamps) <= warmup + 1:
        raise ValueError(f"not enough bars: {len(timestamps)} (warmup={warmup})")

    cash = float(initial_capital)
    positions: dict[str, _Position] = {}
    equity_curve: list[float] = []
    eq_history: list[tuple] = []  # (ts, equity) для risk.nav_history
    trade_pnls: list[float] = []
    n_trades = 0
    gross_traded = 0.0
    exposure_points: list[float] = []
    flat_points = 0
    profit_steps_done: dict[tuple[str, str, float], set[str]] = {}
    opened_at: dict[str, int] = {}
    holding_durations: list[int] = []
    tp_exit_events: list[tuple[str, str, int, float]] = []

    # i: индекс «решения» (по данным до timestamps[i]); исполнение на i+1.
    for i in range(warmup, len(timestamps) - 1):
        t = timestamps[i]
        t_next = timestamps[i + 1]

        # История строго до t включительно (no-lookahead).
        history = {tkr: df.loc[:t] for tkr, df in prices.items() if df.index[0] <= t}
        portfolio = {
            "cash": cash,
            "positions": {k: v.lots for k, v in positions.items()},
        }
        signals = strategy.decide(history, portfolio)

        nav_now = _nav(cash, positions, prices, t, lot_sizes)
        tick_open_nav = nav_now
        tick_buy_spent = 0.0

        for tkr, sig in signals.items():
            cur_lots = positions.get(tkr, _Position()).lots
            if sig.signal == "HOLD" and not (apply_risk and cur_lots != 0):
                continue
            if sig.signal != "HOLD" and sig.size_pct <= 0:
                continue
            exec_px_raw = _price_at(prices[tkr], t_next, "open") or _price_at(
                prices[tkr], t_next, "close"
            )
            if not exec_px_raw:
                continue
            lot = int(risk_mod.lot_size_for(tkr, lot_sizes))

            total_lots, op_kind, op_type, trade_meta = _resolve_qty(
                sig=sig,
                tkr=tkr,
                positions=positions,
                cash=cash,
                last_price=exec_px_raw,
                lot=lot,
                s=s,
                apply_risk=apply_risk,
                nav=nav_now,
                eq_history=eq_history,
                history=history,
                lot_sizes=lot_sizes,
                tick_open_nav=tick_open_nav,
                tick_buy_spent=tick_buy_spent,
                profit_steps_done=profit_steps_done,
            )
            if total_lots <= 0:
                continue

            # reduce-override (risk_trim/take_profit/stop_loss) задаёт направление
            # сам по суффиксу op_type; иначе направление по сигналу стратегии.
            if op_type.endswith("_cover"):
                sign = 1
            elif op_type.endswith("_sell"):
                sign = -1
            else:
                sign = 1 if sig.signal == "BUY" else -1
            delta_lots = sign * total_lots
            # Адверсный слиппедж: покупка дороже, продажа дешевле.
            exec_px = exec_px_raw * (1 + slippage) if sign > 0 else exec_px_raw * (1 - slippage)

            old_lots = positions.get(tkr, _Position()).lots
            cash, realized = _apply_fill(
                positions, tkr, delta_lots, lot, exec_px, commission_rate, cash
            )
            exec_i = i + 1
            new_lots = positions.get(tkr, _Position()).lots
            _record_holding_duration(
                opened_at, holding_durations, tkr, old_lots, new_lots, exec_i
            )
            n_trades += 1
            gross_traded += total_lots * lot * exec_px_raw
            if realized is not None:
                trade_pnls.append(realized)
            if op_type in ("take_profit_sell", "take_profit_cover"):
                closed_side = "short" if op_type.endswith("_cover") else "long"
                tp_exit_events.append((tkr, closed_side, exec_i, exec_px_raw))
            if op_kind == "open":
                tick_buy_spent += total_lots * lot * exec_px_raw
            step_id = trade_meta.get("profit_step_id")
            step_key = trade_meta.get("profit_step_key")
            if step_id and step_key:
                profit_steps_done.setdefault(step_key, set()).add(step_id)

        equity = _nav(cash, positions, prices, t_next, lot_sizes)
        exposure = _gross_exposure(positions, prices, t_next, lot_sizes) / equity if equity > 0 else 0.0
        exposure_points.append(exposure)
        if not any(pos.lots != 0 for pos in positions.values()):
            flat_points += 1
        equity_curve.append(equity)
        eq_history.append((_to_msk(t_next), equity))

    equity_arr = np.asarray(equity_curve, dtype=float)
    report = metrics_mod.build_report(
        equity_arr,
        periods_per_year=periods_per_year,
        trade_pnls=trade_pnls,
        n_trades=n_trades,
    )
    result_timestamps = list(timestamps[warmup + 1:])
    elapsed_days = max(_elapsed_trading_days(result_timestamps), 1e-9)
    tp_05 = _tp_continuation_rate(tp_exit_events, prices, timestamps, 0.005)
    tp_10 = _tp_continuation_rate(tp_exit_events, prices, timestamps, 0.010)
    return BacktestResult(
        equity=equity_arr,
        timestamps=result_timestamps,
        report=report,
        trade_pnls=trade_pnls,
        n_trades=n_trades,
        gross_traded=gross_traded,
        trades_per_day=n_trades / elapsed_days,
        gross_turnover_per_day=gross_traded / elapsed_days,
        avg_exposure_pct=float(np.mean(exposure_points)) if exposure_points else 0.0,
        flat_time_pct=flat_points / len(exposure_points) if exposure_points else 1.0,
        avg_holding_bars=float(np.mean(holding_durations)) if holding_durations else 0.0,
        tp_exit_count=len(tp_exit_events),
        tp_continuation_05_rate=tp_05,
        tp_continuation_10_rate=tp_10,
    )


def _resolve_qty(
    *, sig: Signal, tkr, positions, cash, last_price, lot, s, apply_risk,
    nav, eq_history, history, lot_sizes, tick_open_nav, tick_buy_spent,
    profit_steps_done,
) -> tuple[int, str, str, dict]:
    """Возвращает (total_lots, op_kind, op_type). op_kind: 'open'|'close' (для
    tick budget); op_type — тип операции Risk Officer (для определения
    направления, в т.ч. risk_trim_*)."""
    cur_lots = int(positions.get(tkr, _Position()).lots)

    if not apply_risk:
        # Сайзим от NAV, а не cash: при шортах cash растёт от выручки и сайзинг
        # от cash даёт экспоненциальный разгон позиции (без риск-кэпов).
        qty = quantity_for_buy(max(nav, 0.0), sig.size_pct, last_price, lot)
        # close/cover если знак сделки против позиции
        opening = (sig.signal == "BUY" and cur_lots >= 0) or (
            sig.signal == "SELL" and cur_lots <= 0
        )
        return qty, ("open" if opening else "close"), "", {}

    # Боевой Risk Officer.
    pos_list = [
        {"secid": k, "position": v.lots, "average_price": v.avg_price}
        for k, v in positions.items()
        if v.lots != 0
    ]
    pos = positions.get(tkr, _Position())
    step_key = _profit_step_key(tkr, pos.lots, pos.avg_price)
    returns_window = _returns_window(history.get(tkr), s)
    ctx = risk_mod.RiskContext(
        cash=cash,
        positions=pos_list,
        nav=nav,
        last_price=last_price,
        returns_window=returns_window,
        nav_history=list(eq_history),
        settings=s,
        lot_sizes=lot_sizes,
        tick_buy_spent=tick_buy_spent,
        tick_open_nav=tick_open_nav,
        profit_steps_done=profit_steps_done.get(step_key, set()),
    )
    gate = risk_mod.evaluate(sig, ctx)
    if not gate.allowed:
        return 0, "open", "", {}
    if gate.flip_close_qty is not None and gate.flip_open_qty is not None:
        return int(gate.flip_close_qty) + int(gate.flip_open_qty), "open", gate.op_type or "", {}
    op = gate.op_type or ""
    # close/cover (вкл. reduce-override *_cover/*_sell) не съедают tick-бюджет.
    op_kind = "close" if (
        op in ("close_long", "cover_short")
        or op.endswith("_cover") or op.endswith("_sell")
    ) else "open"
    meta: dict = {}
    step_id = risk_mod.profit_step_id_from_pct(
        float(gate.metrics.get("profit_step") or 0.0), s
    )
    if step_id and step_key:
        meta = {"profit_step_id": step_id, "profit_step_key": step_key}
    return int(gate.qty or 0), op_kind, op, meta


def _apply_fill(
    positions: dict[str, _Position],
    tkr: str,
    delta_lots: int,
    lot: int,
    exec_px: float,
    commission_rate: float,
    cash: float,
) -> tuple[float, float | None]:
    """Применяет сделку к позиции/кэшу (стандартная модель). Возвращает
    (new_cash, realized_pnl|None). realized считается при сокращении позиции."""
    pos = positions.setdefault(tkr, _Position())
    delta_shares = delta_lots * lot
    trade_value = abs(delta_shares) * exec_px
    commission = trade_value * commission_rate
    cash -= delta_shares * exec_px  # покупка: cash↓; продажа: cash↑
    cash -= commission

    old_lots = pos.lots
    new_lots = old_lots + delta_lots
    realized: float | None = None

    if old_lots != 0 and (old_lots > 0) != (delta_lots > 0):
        # Сокращение/закрытие/переворот — фиксируем PnL по закрытой части.
        closed = min(abs(old_lots), abs(delta_lots))
        direction = 1 if old_lots > 0 else -1
        realized = closed * lot * (exec_px - pos.avg_price) * direction - commission

    if new_lots == 0:
        pos.lots = 0.0
        pos.avg_price = 0.0
    elif old_lots == 0 or (old_lots > 0) == (new_lots > 0) and (old_lots > 0) == (delta_lots > 0):
        # Та же сторона, расширение → усреднение.
        pos.avg_price = (
            (abs(old_lots) * pos.avg_price + abs(delta_lots) * exec_px) / abs(new_lots)
        )
        pos.lots = new_lots
    else:
        # Пересекли ноль (переворот) или просто уменьшили.
        if (old_lots > 0) != (new_lots > 0):
            pos.avg_price = exec_px  # новая сторона
        pos.lots = new_lots

    return cash, realized


def _profit_step_key(tkr: str, lots: float, avg_price: float) -> tuple[str, str, float] | None:
    if lots == 0 or avg_price <= 0:
        return None
    side = "long" if lots > 0 else "short"
    return (tkr, side, round(float(avg_price), 6))


def _record_holding_duration(
    opened_at: dict[str, int],
    holding_durations: list[int],
    tkr: str,
    old_lots: float,
    new_lots: float,
    exec_i: int,
) -> None:
    if old_lots == 0 and new_lots != 0:
        opened_at[tkr] = exec_i
        return
    if old_lots == 0:
        return
    opened_i = opened_at.get(tkr, exec_i)
    if new_lots == 0:
        holding_durations.append(max(exec_i - opened_i, 0))
        opened_at.pop(tkr, None)
    elif (old_lots > 0) != (new_lots > 0):
        holding_durations.append(max(exec_i - opened_i, 0))
        opened_at[tkr] = exec_i
    elif abs(new_lots) < abs(old_lots):
        holding_durations.append(max(exec_i - opened_i, 0))


def _elapsed_trading_days(timestamps: list) -> float:
    dates = {pd.Timestamp(t).date() for t in timestamps}
    return float(len(dates))


def _tp_continuation_rate(
    events: list[tuple[str, str, int, float]],
    prices: dict[str, pd.DataFrame],
    timestamps: pd.DatetimeIndex,
    threshold: float,
    lookahead_bars: int = 3,
) -> float:
    if not events:
        return 0.0
    continued = 0
    considered = 0
    for tkr, closed_side, exit_i, exit_price in events:
        end_i = min(exit_i + lookahead_bars + 1, len(timestamps))
        if exit_i + 1 >= end_i:
            continue
        future: list[float] = []
        for j in range(exit_i + 1, end_i):
            px = _price_at(prices[tkr], timestamps[j], "close")
            if px:
                future.append(px)
        if not future:
            continue
        considered += 1
        if closed_side == "long":
            continued += int(max(future) >= exit_price * (1 + threshold))
        else:
            continued += int(min(future) <= exit_price * (1 - threshold))
    return continued / considered if considered else 0.0


def _gross_exposure(positions, prices, t, lot_sizes) -> float:
    gross = 0.0
    for tkr, pos in positions.items():
        if pos.lots == 0:
            continue
        px = _price_at(prices[tkr], t, "close") or pos.avg_price
        lot = int(risk_mod.lot_size_for(tkr, lot_sizes))
        gross += abs(pos.lots) * lot * px
    return gross


def _nav(cash, positions, prices, t, lot_sizes) -> float:
    nav = float(cash)
    for tkr, pos in positions.items():
        if pos.lots == 0:
            continue
        px = _price_at(prices[tkr], t, "close") or pos.avg_price
        lot = int(risk_mod.lot_size_for(tkr, lot_sizes))
        nav += pos.lots * lot * px
    return nav


def _returns_window(df: pd.DataFrame | None, s) -> list[float]:
    if df is None or "close" not in df.columns:
        return []
    lookback = int(getattr(s, "RISK_VAR_LOOKBACK", 60))
    closes = [float(c) for c in df["close"].tail(lookback + 1).tolist()]
    return risk_mod.log_returns(closes)


def _aligned(prices: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    idx = None
    for df in prices.values():
        idx = df.index if idx is None else idx.union(df.index)
    if idx is None:
        return pd.DatetimeIndex([])
    return idx.sort_values()
