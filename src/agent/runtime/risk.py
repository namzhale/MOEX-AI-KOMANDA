from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

from agent.runtime.hours import MSK, now_msk
from agent.runtime.sizing import position_for, quantity_for_buy
from agent.runtime.universe import LOT_SIZE_BY_TICKER, SECTOR_MAP
from agent.schemas import Decision, RiskGateResult

if TYPE_CHECKING:
    from agent.config import Settings
    from agent.runtime.journal import JsonlJournal

log = structlog.get_logger()

# z-score для 95% односторонней нормали — используется в параметрическом VaR.
Z_95 = 1.645

# Версия формулы расчёта NAV. Журнал помечает каждый tick этим маркером.
# peak/drawdown считаются ТОЛЬКО по записям текущей версии — старые tick'и
# с багнутым NAV (например, фантомный 1.8M от лотовой ошибки) игнорируются.
NAV_CALC_VERSION = "collateral_v1"


@dataclass
class RiskContext:
    cash: float
    positions: list[dict]
    nav: float
    last_price: float
    returns_window: list[float]  # log-returns по торгуемому тикеру
    nav_history: list[tuple[datetime, float]]
    settings: "Settings"
    lot_sizes: dict[str, int] = field(default_factory=dict)
    sigma_by_symbol: dict[str, float] = field(default_factory=dict)
    # Сколько cash уже потрачено на BUY в текущем тике. Используется
    # tick_allocation-гейтом, чтобы не сливать весь кошелёк за один тик.
    tick_buy_spent: float = 0.0
    tick_open_nav: float = 0.0
    flow_features: dict[str, float] = field(default_factory=dict)
    profit_steps_done: set[str] = field(default_factory=set)


PROFIT_LOCK_STEP_ID = "profit_lock"
PROFIT_PARTIAL_STEP_ID = "profit_partial"


def load_nav_history(
    journal: "JsonlJournal", lookback_days: int = 5, tail: int = 2000
) -> list[tuple[datetime, float]]:
    """Из jsonl-журнала достаём пары (ts, nav) за последние N дней.

    Считаем ТОЛЬКО tick'и с маркером текущей версии формулы NAV
    (`nav_calc == NAV_CALC_VERSION`). Старые записи (без маркера или со старой
    версией) игнорируются — их NAV считался по багнутой формуле (фантомный
    1.8M от лотовой ошибки) и отравил бы peak/drawdown.
    """
    records = journal.tail(tail)
    cutoff = datetime.now(MSK).timestamp() - lookback_days * 86400
    out: list[tuple[datetime, float]] = []
    for rec in records:
        if rec.get("event") != "tick":
            continue
        if rec.get("nav_calc") != NAV_CALC_VERSION:
            continue  # старая/багнутая формула NAV → не доверяем
        ts_raw = rec.get("ts")
        nav = rec.get("nav")
        if ts_raw is None or nav is None:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.timestamp() < cutoff:
            continue
        out.append((ts, float(nav)))
    return out


def session_open_nav(
    history: list[tuple[datetime, float]], at: datetime | None = None
) -> float | None:
    """Первый NAV сегодняшнего торгового дня MSK; None если истории нет."""
    today = (at or now_msk()).astimezone(MSK).date()
    for ts, nav in history:
        if ts.astimezone(MSK).date() == today:
            return nav
    return None


def peak_nav(
    history: list[tuple[datetime, float]],
    current_nav: float,
    positions: list[dict] | None = None,
) -> float:
    """Peak NAV — исторический максимум, **но только пока позиции открыты**.

    Если бот стоит флэт (все позиции закрыты, всё в кэше) — peak сбрасывается
    на current_nav. Логика: при пустом портфеле исторический peak не
    релевантен, предыдущие просадки уже зафиксированы как cash. Без этого
    одно неудачное «открытие→закрытие» парализует kill_switch_mdd навсегда.
    """
    if positions is not None:
        has_open = any(float(p.get("position", 0) or 0) != 0 for p in positions)
        if not has_open:
            return current_nav
    if not history:
        return current_nav
    return max(max(n for _, n in history), current_nav)


def log_returns(closes: list[float]) -> list[float]:
    """Логарифмические доходности по последовательности close-цен."""
    out: list[float] = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def sector_weights(
    positions: list[dict],
    nav: float,
    fallback_price: dict[str, float] | None = None,
    lot_sizes: dict[str, int] | None = None,
) -> dict[str, float]:
    """Сумма |весов| позиций по секторам. Шорты и лонги одинаково считаются
    в нагрузку на сектор (по абсолютному модулю).
    `position` в /api/positions — signed число ЛОТОВ; реальная стоимость =
    |position| × lot_size × price."""
    fallback_price = fallback_price or {}
    if nav <= 0:
        return {}
    sums: dict[str, float] = {}
    for p in positions:
        secid = p.get("secid", "")
        qty = float(p.get("position", 0))
        if qty == 0:
            continue
        lot_size = lot_size_for(secid, lot_sizes)
        price = fallback_price.get(secid) or float(p.get("average_price", 0))
        sector = SECTOR_MAP.get(secid, "OTHER")
        sums[sector] = sums.get(sector, 0.0) + abs(qty) * lot_size * price / nav
    return sums


def lot_size_for(secid: str, lot_sizes: dict[str, int] | None = None) -> int:
    """Размер лота для тикера. Используется только для раунд-дауна quantity
    под требования MOEX (lot-кратность). Для неизвестных тикеров возвращаем 1,
    чтобы не блокировать торговлю — ArenaGo вернёт ошибку, если её не примет."""
    raw = (lot_sizes or {}).get(secid, LOT_SIZE_BY_TICKER.get(secid, 1))
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return 1


def commission_rate_for(settings: "Settings") -> float:
    try:
        return max(float(getattr(settings, "TRADING_COMMISSION_RATE", 0.0) or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def portfolio_sigma(
    weights: dict[str, float], sigma_by_symbol: dict[str, float]
) -> float:
    """Консервативная оценка σ портфеля при ρ=1: σ_p = Σ |wᵢ|·σᵢ."""
    return sum(abs(w) * sigma_by_symbol.get(s, 0.0) for s, w in weights.items())


def _avg_price_for(positions: list[dict], symbol: str) -> float:
    for p in positions:
        if p.get("secid") == symbol:
            try:
                return float(p.get("average_price") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _risk_trim(decision: Decision, ctx: RiskContext) -> RiskGateResult | None:
    """Risk-initiated trim: если |вес| позиции > cap×(1+band), принудительно
    сокращаем к cap ВЫШЕ сигнала LLM (срабатывает даже на HOLD).

    Политика profit-gated + стоп: режем, если позиция в плюсе/безубытке
    (pnl ≥ −tol) ЛИБО просадка достигла стопа (pnl ≤ −stop). В «мелком минусе»
    между ними — ждём восстановления. Возвращает заявку на сокращение или None
    (тогда обычная логика evaluate)."""
    s = ctx.settings
    if not getattr(s, "RISK_TRIM_ENABLED", True):
        return None
    if ctx.last_price <= 0 or ctx.nav <= 0:
        return None

    symbol = decision.symbol
    current_qty = position_for(ctx.positions, symbol)
    if current_qty == 0:
        return None

    lot_size = lot_size_for(symbol, ctx.lot_sizes)
    abs_qty = abs(current_qty)
    weight = abs_qty * lot_size * ctx.last_price / ctx.nav
    cap = float(s.RISK_MAX_INSTRUMENT_WEIGHT)
    band = float(getattr(s, "RISK_TRIM_BAND", 0.10))
    if cap <= 0 or weight <= cap * (1.0 + band):
        return None

    avg_price = _avg_price_for(ctx.positions, symbol)
    if avg_price > 0:
        # short в плюсе когда цена ниже входа; long — когда выше.
        pnl_pct = (
            (avg_price - ctx.last_price) / avg_price
            if current_qty < 0
            else (ctx.last_price - avg_price) / avg_price
        )
    else:
        pnl_pct = 0.0  # avg неизвестен → считаем безубытком, режем

    tol = float(getattr(s, "RISK_TRIM_LOSS_TOLERANCE", 0.0))
    stop = float(getattr(s, "RISK_TRIM_STOP_PCT", 0.03))
    in_profit = pnl_pct >= -tol
    hit_stop = pnl_pct <= -stop
    if not (in_profit or hit_stop):
        return None  # мелкий минус — ждём

    cap_qty = int(cap * ctx.nav / (lot_size * ctx.last_price))
    trim_lots = abs_qty - cap_qty
    if trim_lots <= 0:
        return None
    max_pct = float(getattr(s, "RISK_TRIM_MAX_PCT_PER_TICK", 0.0))
    if max_pct > 0:
        max_lots = int(max_pct * ctx.nav / (lot_size * ctx.last_price))
        if max_lots > 0:
            trim_lots = min(trim_lots, max_lots)
    if trim_lots <= 0:
        return None

    op_type = "risk_trim_cover" if current_qty < 0 else "risk_trim_sell"
    kind = "profit" if in_profit else "stop"
    metrics = {
        "current_weight": weight,
        "lot_size": float(lot_size),
        "trim_pnl_pct": pnl_pct,
        "qty": float(trim_lots),
        "notional": trim_lots * lot_size * ctx.last_price,
    }
    return _allow(
        gate="risk_trim",
        reason=(
            f"|weight| {weight:.1%} > cap {cap:.0%} ({kind}, pnl {pnl_pct:+.2%}) "
            f"-> trim {trim_lots} lots to cap"
        ),
        effective_size=None,
        qty=trim_lots,
        metrics=metrics,
        op_type=op_type,
    )


def profit_step_id_from_pct(step_pct: float, settings: "Settings") -> str | None:
    lock = float(getattr(settings, "RISK_PROFIT_LOCK_PCT", 0.007) or 0.0)
    partial = float(getattr(settings, "RISK_PROFIT_PARTIAL_PCT", 0.012) or 0.0)
    if lock > 0 and math.isclose(step_pct, lock, rel_tol=0.0, abs_tol=1e-9):
        return PROFIT_LOCK_STEP_ID
    if partial > 0 and math.isclose(step_pct, partial, rel_tol=0.0, abs_tol=1e-9):
        return PROFIT_PARTIAL_STEP_ID
    return None


def _profit_lock_signal_allows_exit(signal: str, current_qty: int) -> bool:
    if signal == "HOLD":
        return True
    if current_qty > 0:
        return signal == "SELL"
    if current_qty < 0:
        return signal == "BUY"
    return False


def _partial_qty(abs_qty: int, fraction: float) -> int:
    fraction = min(max(float(fraction), 0.0), 1.0)
    if abs_qty <= 0 or fraction <= 0.0:
        return 0
    if fraction >= 1.0:
        return abs_qty
    return min(abs_qty, max(int(math.ceil(abs_qty * fraction)), 1))


def _risk_pnl_exit(decision: Decision, ctx: RiskContext) -> RiskGateResult | None:
    """Fixed TP/SL bracket: автозакрытие позиции по нереализованному P&L
    относительно avg_price, ВЫШЕ сигнала LLM (срабатывает даже на HOLD).

    Stateless: pnl_pct считается от avg_price (с брокера) каждый тик — переживает
    рестарты. Фиксируем прибыль на +TP%, режем убыток на -SL%. Закрытие всегда
    разрешено (де-рискинг), поэтому стоит до opening-гейтов и до HOLD-return.
    Возвращает заявку на полное закрытие или None (тогда обычная логика)."""
    s = ctx.settings
    profit_enabled = bool(getattr(s, "RISK_PROFIT_TAKE_ENABLED", False))
    tp = float(getattr(s, "RISK_TAKE_PROFIT_PCT", 0.0) or 0.0)
    sl = float(getattr(s, "RISK_STOP_LOSS_PCT", 0.0) or 0.0)
    if not profit_enabled and tp <= 0 and sl <= 0:
        return None
    if ctx.last_price <= 0:
        return None

    symbol = decision.symbol
    current_qty = position_for(ctx.positions, symbol)
    if current_qty == 0:
        return None
    avg_price = _avg_price_for(ctx.positions, symbol)
    if avg_price <= 0:
        return None  # без средней P&L не посчитать

    # short в плюсе когда цена ниже входа; long — когда выше.
    pnl_pct = (
        (avg_price - ctx.last_price) / avg_price
        if current_qty < 0
        else (ctx.last_price - avg_price) / avg_price
    )

    if profit_enabled:
        abs_qty = abs(current_qty)
        lot_size = lot_size_for(symbol, ctx.lot_sizes)
        op_type_take = "take_profit_cover" if current_qty < 0 else "take_profit_sell"
        op_type_stop = "stop_loss_cover" if current_qty < 0 else "stop_loss_sell"

        kind = ""
        qty = 0
        step_pct = 0.0
        close_fraction = 0.0
        op_type = op_type_take

        if sl > 0 and pnl_pct <= -sl:
            kind = "stop_loss"
            qty = abs_qty
            close_fraction = 1.0
            op_type = op_type_stop
        elif pnl_pct > 0:
            full_pct = float(getattr(s, "RISK_PROFIT_FULL_PCT", 0.020) or 0.0)
            partial_pct = float(getattr(s, "RISK_PROFIT_PARTIAL_PCT", 0.012) or 0.0)
            lock_pct = float(getattr(s, "RISK_PROFIT_LOCK_PCT", 0.007) or 0.0)
            partial_fraction = float(getattr(s, "RISK_PROFIT_PARTIAL_FRACTION", 0.50) or 0.0)
            lock_fraction = float(
                getattr(s, "RISK_PROFIT_LOCK_FRACTION", partial_fraction) or 0.0
            )
            done = ctx.profit_steps_done
            if full_pct > 0 and pnl_pct >= full_pct:
                kind = "take_profit"
                qty = abs_qty
                step_pct = full_pct
                close_fraction = 1.0
            elif (
                partial_pct > 0
                and pnl_pct >= partial_pct
                and PROFIT_PARTIAL_STEP_ID not in done
            ):
                step_pct = partial_pct
                close_fraction = partial_fraction
                qty = _partial_qty(abs_qty, close_fraction)
                if qty > 0:
                    kind = "take_profit"
            elif (
                lock_pct > 0
                and pnl_pct >= lock_pct
                and PROFIT_LOCK_STEP_ID not in done
                and _profit_lock_signal_allows_exit(decision.signal, current_qty)
            ):
                step_pct = lock_pct
                close_fraction = lock_fraction
                qty = _partial_qty(abs_qty, close_fraction)
                if qty > 0:
                    kind = "take_profit"

        if not kind:
            return None

        metrics = {
            "lot_size": float(lot_size),
            "pnl_pct": pnl_pct,
            "avg_price": avg_price,
            "profit_step": step_pct,
            "close_fraction": close_fraction,
            "qty": float(qty),
            "notional": qty * lot_size * ctx.last_price,
        }
        return _allow(
            gate=kind,
            reason=f"{kind}: pnl {pnl_pct:+.2%} -> close {qty} lots",
            effective_size=None,
            qty=qty,
            metrics=metrics,
            op_type=op_type,
        )

    if tp > 0 and pnl_pct >= tp:
        kind = "take_profit"
    elif sl > 0 and pnl_pct <= -sl:
        kind = "stop_loss"
    else:
        return None  # внутри диапазона — решает обычная логика / LLM

    abs_qty = abs(current_qty)
    lot_size = lot_size_for(symbol, ctx.lot_sizes)
    op_type = f"{kind}_cover" if current_qty < 0 else f"{kind}_sell"
    metrics = {
        "lot_size": float(lot_size),
        "pnl_pct": pnl_pct,
        "qty": float(abs_qty),
        "notional": abs_qty * lot_size * ctx.last_price,
    }
    return _allow(
        gate=kind,
        reason=f"{kind}: pnl {pnl_pct:+.2%} -> close {abs_qty} lots",
        effective_size=None,
        qty=abs_qty,
        metrics=metrics,
        op_type=op_type,
    )


def evaluate(decision: Decision, ctx: RiskContext) -> RiskGateResult:
    """Sign-aware гейты. Поддерживаются 6 операций: open/add/close/cover long+short
    плюс single-tick flip (close + open в одном тике).

    Семантика по знаку текущей позиции:
      * BUY,  current_qty >= 0 → open / add long
      * BUY,  current_qty <  0, desired <= |pos| → cover short (bypass)
      * BUY,  current_qty <  0, desired >  |pos| → flip short→long
      * SELL, current_qty >  0, desired <= pos → close long (bypass)
      * SELL, current_qty >  0, desired >  pos → flip long→short
      * SELL, current_qty <= 0 → open / add short

    Гейты концентрации/VaR работают по **абсолютному** весу (|position|*price/NAV)
    — лонг и шорт одинаково ограничены RISK_MAX_INSTRUMENT_WEIGHT.
    """
    s = ctx.settings

    if not s.RISK_ENABLED:
        return _pass(gate="disabled", reason="risk layer disabled")

    # Risk-initiated trim перехватывает ВЫШЕ сигнала LLM (в т.ч. на HOLD):
    # раздутую позицию сокращаем к кэпу независимо от того, что решил трейдер.
    trim = _risk_trim(decision, ctx)
    if trim is not None:
        return trim

    # Fixed TP/SL bracket — фиксация прибыли / стоп-лосс по P&L, тоже выше
    # сигнала LLM (закрываем позицию независимо от того, что решил трейдер).
    pnl_exit = _risk_pnl_exit(decision, ctx)
    if pnl_exit is not None:
        return pnl_exit

    if decision.signal == "HOLD":
        return _pass(gate="hold", reason="HOLD — no order")

    metrics: dict[str, float] = {}

    # killswitch metrics — вычисляем здесь, проверка только для opening операций
    # (closing и cover всегда разрешены; де-рискинг важнее лимитов).
    #
    # drawdown считается по NAV (collateral-модель, корректна для шортов).
    # peak_nav берёт историю только текущей версии формулы (load_nav_history
    # фильтрует по nav_calc) + self-healing при пустом портфеле — поэтому
    # фантомные NAV из старого багнутого журнала не влияют.
    daily_pnl_pct = 0.0
    session_active = False
    peak = peak_nav(ctx.nav_history, ctx.nav, ctx.positions)
    dd_pct = (peak - ctx.nav) / peak if peak > 0 else 0.0
    metrics["dd_pct"] = dd_pct
    metrics["peak_nav"] = peak
    session_nav = session_open_nav(ctx.nav_history)
    if session_nav and session_nav > 0:
        daily_pnl_pct = (ctx.nav - session_nav) / session_nav
        session_active = True
        metrics["daily_pnl_pct"] = daily_pnl_pct
        metrics["session_open_nav"] = session_nav

    symbol = decision.symbol
    current_qty = position_for(ctx.positions, symbol)  # signed число ЛОТОВ
    current_abs_qty = abs(current_qty)
    lot_size = lot_size_for(symbol, ctx.lot_sizes)
    # Все денежные расчёты: qty(лоты) × lot_size × price.
    current_value = current_abs_qty * lot_size * ctx.last_price
    current_weight = current_value / ctx.nav if ctx.nav > 0 else 0.0
    metrics["current_weight"] = current_weight
    metrics["lot_size"] = float(lot_size)

    desired_qty = quantity_for_buy(ctx.cash, decision.size_pct, ctx.last_price, lot_size)

    # ── Closing / covering: bypass concentration/VaR/sanity/tick_allocation ──
    # No-flip дисциплина: при выключенном AGENT_ALLOW_FLIP противоположный сигнал
    # ВСЕГДА закрывает только до флэта (qty кэпнут на |позиции|), обратная сторона
    # не открывается. При allow_flip — старое поведение (close если desired<=|pos|,
    # иначе flip ниже).
    allow_flip = bool(getattr(s, "AGENT_ALLOW_FLIP", False))
    if decision.signal == "SELL" and current_qty > 0 and (
        desired_qty <= 0 or desired_qty <= current_qty or not allow_flip
    ):
        # Partial/full close. desired>current при no-flip → кэпим на current (флэт).
        qty = min(desired_qty, current_qty) if desired_qty > 0 else current_qty
        metrics["qty"] = float(qty)
        metrics["notional"] = qty * lot_size * ctx.last_price
        return _allow(
            gate="all_passed", reason="SELL — close long (bypass)",
            effective_size=None, qty=qty, metrics=metrics, op_type="close_long",
        )
    if decision.signal == "BUY" and current_qty < 0 and (
        desired_qty <= 0 or desired_qty <= current_abs_qty or not allow_flip
    ):
        qty = min(desired_qty, current_abs_qty) if desired_qty > 0 else current_abs_qty
        metrics["qty"] = float(qty)
        metrics["notional"] = qty * lot_size * ctx.last_price
        return _allow(
            gate="all_passed", reason="BUY — cover short (bypass)",
            effective_size=None, qty=qty, metrics=metrics, op_type="cover_short",
        )

    # killswitches проверяются внутри _evaluate_opening — там же flip-fallback,
    # чтобы при активном killswitch'е flip деградировал в close-only (де-рискинг).

    # Определяем тип операции и параметры для opening-гейтов.
    is_flip = False
    flip_close_qty: int | None = None
    base_abs_qty = current_abs_qty
    direction: str = "long"

    if decision.signal == "SELL":
        if current_qty > 0:
            # FLIP long → short: close + open
            is_flip = True
            flip_close_qty = current_qty
            base_abs_qty = 0  # после close
            direction = "short"
            op_type_open = "flip_long_to_short"
            op_type_close_only = "close_long"
            opening_qty_target = desired_qty - current_qty
        else:
            # open / add short
            direction = "short"
            op_type_open = "open_short" if current_qty == 0 else "add_short"
            op_type_close_only = None
            opening_qty_target = desired_qty
    else:  # BUY
        if current_qty < 0:
            is_flip = True
            flip_close_qty = current_abs_qty
            base_abs_qty = 0
            direction = "long"
            op_type_open = "flip_short_to_long"
            op_type_close_only = "cover_short"
            opening_qty_target = desired_qty - current_abs_qty
        else:
            direction = "long"
            op_type_open = "open_long" if current_qty == 0 else "add_long"
            op_type_close_only = None
            opening_qty_target = desired_qty

    return _evaluate_opening(
        decision=decision,
        ctx=ctx,
        metrics=metrics,
        lot_size=lot_size,
        base_abs_qty=base_abs_qty,
        direction=direction,
        opening_qty_target=opening_qty_target,
        op_type_open=op_type_open,
        flip_close_qty=flip_close_qty if is_flip else None,
        op_type_close_only=op_type_close_only,
        dd_pct=dd_pct,
        daily_pnl_pct=daily_pnl_pct,
        session_nav_active=session_active,
    )


def _evaluate_opening(
    *,
    decision: Decision,
    ctx: RiskContext,
    metrics: dict[str, float],
    lot_size: int,
    base_abs_qty: int,
    direction: str,
    opening_qty_target: int,
    op_type_open: str,
    flip_close_qty: int | None = None,
    op_type_close_only: str | None = None,
    dd_pct: float = 0.0,
    daily_pnl_pct: float = 0.0,
    session_nav_active: bool = False,
) -> RiskGateResult:
    """Прогон killswitch + concentration/VaR/sanity/tick_allocation для
    opening-части заявки.

    Концентрация считается по |вес|. Для flip'а если open-часть зарезана любым
    гейтом — возвращаем close-only (отправит только close-заявку).
    """
    s = ctx.settings
    symbol = decision.symbol

    # min_edge — отсекаем сделки с ожидаемым движением ниже round-trip cost.
    # Защита от over-trading (FINSABER: LLM-стратегии проигрывают buy-and-hold
    # из-за высокого оборота). Ожидаемый edge привязан к РЕАЛЬНОЙ волатильности
    # инструмента: edge ≈ confidence × σ(per-bar) × mult, а не к фиксированной
    # эвристике. На тихих именах σ мал → сделка не отбивает комиссию → блок.
    # При нехватке истории (< 5 точек) гейт пропускаем (не блокируем на пустоте).
    if ctx.last_price <= 0:
        return _flip_fallback_or_block(
            metrics,
            flip_close_qty,
            op_type_close_only,
            ctx.last_price,
            gate="sanity_price",
            reason="last_price <= 0",
        )

    if decision.confidence < s.RISK_MIN_CONFIDENCE:
        return _flip_fallback_or_block(
            metrics,
            flip_close_qty,
            op_type_close_only,
            ctx.last_price,
            gate="sanity_confidence",
            reason=(
                f"confidence {decision.confidence:.2f} < "
                f"{s.RISK_MIN_CONFIDENCE:.2f}"
            ),
        )

    spread_cap_bps = float(getattr(s, "ALGOPACK_RISK_SPREAD_1MIO_MAX_BPS", 0.0) or 0.0)
    if spread_cap_bps > 0 and ctx.flow_features:
        spread_bps = float(ctx.flow_features.get("ob_spread_1mio_bps") or 0.0)
        if spread_bps <= 0:
            spread_bps = float(ctx.flow_features.get("ob_spread_bbo_bps") or 0.0)
        metrics["flow_spread_1mio_bps"] = spread_bps
        if spread_bps > spread_cap_bps:
            return _flip_fallback_or_block(
                metrics,
                flip_close_qty,
                op_type_close_only,
                ctx.last_price,
                gate="illiquid_spread",
                reason=f"spread_1mio {spread_bps:.1f}bps > cap {spread_cap_bps:.1f}bps",
            )

    min_edge_pct = float(getattr(s, "RISK_MIN_EDGE_PCT", 0.0) or 0.0)
    returns = ctx.returns_window or []
    if min_edge_pct > 0 and len(returns) >= 5:
        commission_rate = commission_rate_for(s)
        round_trip = 2.0 * commission_rate
        required_edge = max(min_edge_pct, round_trip)
        sigma = stddev(returns)  # per-bar волатильность инструмента (доля)
        vol_mult = float(getattr(s, "RISK_EDGE_VOL_MULT", 1.0))
        estimated_edge = decision.confidence * sigma * vol_mult
        metrics["estimated_edge"] = estimated_edge
        metrics["required_edge"] = required_edge
        metrics["edge_sigma"] = sigma
        if estimated_edge < required_edge:
            return _flip_fallback_or_block(
                metrics, flip_close_qty, op_type_close_only, ctx.last_price,
                gate="min_edge",
                reason=(
                    f"estimated edge {estimated_edge:.3%} (conf×σ×{vol_mult:g}) < "
                    f"required {required_edge:.3%} (commission {commission_rate:.4%})"
                ),
            )

    # 3. kill_switch_mdd — для opening новой экспозиции (или open-части flip'а).
    if dd_pct >= s.RISK_MAX_DRAWDOWN:
        return _flip_fallback_or_block(
            metrics, flip_close_qty, op_type_close_only, ctx.last_price,
            gate="kill_switch_mdd",
            reason=f"drawdown {dd_pct:.2%} >= {s.RISK_MAX_DRAWDOWN:.2%}",
        )

    # 4. kill_switch_daily_loss
    if session_nav_active and daily_pnl_pct <= -s.RISK_MAX_DAILY_LOSS:
        return _flip_fallback_or_block(
            metrics, flip_close_qty, op_type_close_only, ctx.last_price,
            gate="kill_switch_daily_loss",
            reason=f"daily PnL {daily_pnl_pct:.2%} <= -{s.RISK_MAX_DAILY_LOSS:.2%}",
        )

    # base_abs_qty в ЛОТАХ, переводим в RUB через lot_size × price.
    base_weight = (
        (base_abs_qty * lot_size * ctx.last_price) / ctx.nav if ctx.nav > 0 else 0.0
    )
    metrics["base_abs_weight"] = base_weight

    # 5. instrument_concentration
    if base_weight >= s.RISK_MAX_INSTRUMENT_WEIGHT:
        return _flip_fallback_or_block(
            metrics, flip_close_qty, op_type_close_only, ctx.last_price,
            gate="instrument_concentration",
            reason=(
                f"abs weight {base_weight:.2%} already at cap "
                f"{s.RISK_MAX_INSTRUMENT_WEIGHT:.2%}"
            ),
        )
    room_instrument = s.RISK_MAX_INSTRUMENT_WEIGHT - base_weight
    effective_size = min(decision.size_pct, room_instrument)
    clipped_by = (
        "instrument_concentration" if effective_size < decision.size_pct else None
    )

    # 6. sector_concentration — по абсолютным весам
    fallback_prices = {symbol: ctx.last_price}
    sec_w = sector_weights(ctx.positions, ctx.nav, fallback_prices, ctx.lot_sizes)
    sector = SECTOR_MAP.get(symbol, "OTHER")
    sector_current = sec_w.get(sector, 0.0)
    # Для flip'а после close: вычесть вклад текущей позиции по этому тикеру.
    if flip_close_qty is not None:
        sector_current = max(
            sector_current - (flip_close_qty * lot_size * ctx.last_price) / ctx.nav,
            0.0,
        )
    metrics["sector_weight"] = sector_current
    if sector_current >= s.RISK_MAX_SECTOR_WEIGHT:
        return _flip_fallback_or_block(
            metrics, flip_close_qty, op_type_close_only, ctx.last_price,
            gate="sector_concentration",
            reason=(
                f"sector {sector} weight {sector_current:.2%} at cap "
                f"{s.RISK_MAX_SECTOR_WEIGHT:.2%}"
            ),
        )
    room_sector = s.RISK_MAX_SECTOR_WEIGHT - sector_current
    if effective_size > room_sector:
        effective_size = room_sector
        clipped_by = "sector_concentration"

    # 7. var_gate — projected weights по |qty|
    sigma_ticker = stddev(ctx.returns_window) if ctx.returns_window else 0.0
    sigma_map = dict(ctx.sigma_by_symbol)
    sigma_map[symbol] = sigma_ticker
    projected_weights: dict[str, float] = {}
    for p in ctx.positions:
        sid = p.get("secid", "")
        qty = float(p.get("position", 0))
        if qty == 0 or ctx.nav <= 0 or sid == symbol:
            continue
        price = float(p.get("average_price", 0))
        sid_lot = lot_size_for(sid, ctx.lot_sizes)
        projected_weights[sid] = abs(qty) * sid_lot * price / ctx.nav
    projected_weights[symbol] = base_weight + effective_size

    sigma_port = portfolio_sigma(projected_weights, sigma_map)
    var_pct = Z_95 * sigma_port
    metrics["var_pct"] = var_pct
    metrics["sigma_ticker"] = sigma_ticker
    if var_pct > s.RISK_MAX_VAR_PCT:
        return _flip_fallback_or_block(
            metrics, flip_close_qty, op_type_close_only, ctx.last_price,
            gate="var_gate",
            reason=f"projected 95% VaR {var_pct:.2%} > cap {s.RISK_MAX_VAR_PCT:.2%}",
        )

    # 8. qty calculation — qty в ЛОТАХ. Минимум от concentration-clip и желания.
    qty_by_size = quantity_for_buy(ctx.cash, effective_size, ctx.last_price, lot_size)
    qty = min(qty_by_size, max(opening_qty_target, 0))
    # Все RUB-расчёты: qty × lot_size × price.
    notional = qty * lot_size * ctx.last_price
    commission_rate = commission_rate_for(s)
    commission = notional * commission_rate
    total_cost = notional + commission
    metrics["qty"] = float(qty)
    metrics["notional"] = notional
    metrics["commission_rate"] = commission_rate
    metrics["commission"] = commission
    metrics["total_cost"] = total_cost

    if qty <= 0:
        return _flip_fallback_or_block(
            metrics, flip_close_qty, op_type_close_only, ctx.last_price,
            gate="sanity_qty_cash",
            reason=f"qty=0 after clip (effective_size={effective_size:.4f})",
        )

    # sanity_qty_cash: только для лонга (cash тратится). Для шорта cash прирастает.
    if direction == "long":
        cash_cap = ctx.cash * (1.0 - s.RISK_CASH_BUFFER)
        if total_cost > cash_cap:
            return _flip_fallback_or_block(
                metrics, flip_close_qty, op_type_close_only, ctx.last_price,
                gate="sanity_qty_cash",
                reason=f"total cost {total_cost:.0f} > cash buffer cap {cash_cap:.0f}",
            )

    # 9. tick_allocation — считаем gross spend (longs + shorts).
    max_tick_pct = float(getattr(s, "RISK_MAX_TICK_BUY_PCT", 0.0) or 0.0)
    if max_tick_pct > 0:
        budget_base = ctx.tick_open_nav if ctx.tick_open_nav > 0 else ctx.nav
        tick_budget = budget_base * max_tick_pct
        projected_spent = ctx.tick_buy_spent + total_cost
        metrics["tick_buy_spent"] = ctx.tick_buy_spent
        metrics["tick_buy_budget"] = tick_budget
        if projected_spent > tick_budget:
            return _flip_fallback_or_block(
                metrics, flip_close_qty, op_type_close_only, ctx.last_price,
                gate="tick_allocation",
                reason=(
                    f"tick gross spend {projected_spent:.0f} > "
                    f"budget {tick_budget:.0f} ({max_tick_pct:.0%} of NAV)"
                ),
            )

    if clipped_by is not None:
        metrics["clipping_gate_id"] = 1.0

    # Flip success → две заявки (close + open).
    if flip_close_qty is not None:
        metrics["flip_close_qty"] = float(flip_close_qty)
        metrics["flip_open_qty"] = float(qty)
        return RiskGateResult(
            allowed=True,
            gate="all_passed",
            reason=f"flip: close {flip_close_qty} + open {qty}",
            effective_size=effective_size,
            qty=None,
            metrics=metrics,
            flip_close_qty=flip_close_qty,
            flip_open_qty=qty,
            op_type=op_type_open,
        )

    return RiskGateResult(
        allowed=True,
        gate="all_passed",
        reason=("ok" if clipped_by is None else f"size clipped by {clipped_by}"),
        effective_size=effective_size,
        qty=qty,
        metrics=metrics,
        op_type=op_type_open,
    )


def _flip_fallback_or_block(
    metrics: dict[str, float],
    flip_close_qty: int | None,
    op_type_close_only: str | None,
    last_price: float,
    *,
    gate: str,
    reason: str,
) -> RiskGateResult:
    """Если это flip и open-часть зарезана — отправляем только close-часть.
    Иначе обычный block."""
    if flip_close_qty is not None and op_type_close_only is not None:
        metrics["qty"] = float(flip_close_qty)
        metrics["notional"] = flip_close_qty * last_price
        metrics["flip_blocked_open_by"] = 1.0
        return RiskGateResult(
            allowed=True,
            gate=gate,
            reason=f"flip open-part blocked by {gate} — close-only fallback",
            effective_size=None,
            qty=flip_close_qty,
            metrics=metrics,
            op_type=op_type_close_only,
        )
    return _block(gate, reason, metrics)


def _block(gate: str, reason: str, metrics: dict[str, float]) -> RiskGateResult:
    return RiskGateResult(allowed=False, gate=gate, reason=reason, metrics=metrics)


def _allow(
    gate: str,
    reason: str,
    effective_size: float | None,
    qty: int | None,
    metrics: dict[str, float],
    op_type: str | None = None,
) -> RiskGateResult:
    return RiskGateResult(
        allowed=True,
        gate=gate,
        reason=reason,
        effective_size=effective_size,
        qty=qty,
        metrics=metrics,
        op_type=op_type,
    )


def _pass(gate: str, reason: str) -> RiskGateResult:
    return RiskGateResult(allowed=True, gate=gate, reason=reason)
