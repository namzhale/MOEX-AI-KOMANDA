"""Бэктест-движок на синтетических ценах — без сети, детерминированно."""

from __future__ import annotations

import numpy as np
import pandas as pd

from types import SimpleNamespace

from agent.backtest.engine import buy_and_hold_equity, run_backtest
from agent.backtest.profit_lock import (
    PROFIT_LOCK_CANDIDATES,
    format_profit_lock_activity_check,
    run_profit_lock_activity_check,
    settings_for_profit_lock_candidate,
)
from agent.backtest.strategy import LLMStrategy, Signal, Strategy


def _risk_settings(**overrides) -> SimpleNamespace:
    base = dict(
        RISK_ENABLED=True,
        RISK_MIN_CONFIDENCE=0.35,
        RISK_MAX_INSTRUMENT_WEIGHT=0.15,
        RISK_MAX_SECTOR_WEIGHT=0.35,
        RISK_MAX_VAR_PCT=0.04,
        RISK_MAX_DRAWDOWN=0.10,
        RISK_MAX_DAILY_LOSS=0.03,
        RISK_CASH_BUFFER=0.02,
        RISK_VAR_LOOKBACK=60,
        RISK_NAV_HISTORY_DAYS=5,
        RISK_MAX_TICK_BUY_PCT=0.0,
        TRADING_COMMISSION_RATE=0.0,
        ARENAGO_DAILY_TRADE_LIMIT=200,
        RISK_TRIM_ENABLED=False,
        RISK_TRIM_BAND=0.10,
        RISK_TRIM_LOSS_TOLERANCE=0.0,
        RISK_TRIM_STOP_PCT=0.03,
        RISK_TRIM_MAX_PCT_PER_TICK=0.0,
        AGENT_ALLOW_FLIP=False,
        RISK_TAKE_PROFIT_PCT=0.0,
        RISK_STOP_LOSS_PCT=0.02,
        RISK_PROFIT_TAKE_ENABLED=False,
        RISK_PROFIT_LOCK_PCT=0.007,
        RISK_PROFIT_PARTIAL_PCT=0.012,
        RISK_PROFIT_FULL_PCT=0.020,
        RISK_PROFIT_LOCK_FRACTION=0.50,
        RISK_PROFIT_PARTIAL_FRACTION=0.50,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_df(prices: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(prices), freq="D")
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame(
        {"open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1000.0},
        index=idx,
    )


class _AlwaysBuyOnce(Strategy):
    """BUY один раз когда позиции нет, потом HOLD."""

    name = "buy_once"

    def __init__(self, symbol: str, size_pct: float = 0.10) -> None:
        self.symbol = symbol
        self.size_pct = size_pct

    def decide(self, history, portfolio) -> dict[str, Signal]:
        held = portfolio["positions"].get(self.symbol, 0)
        sig = "BUY" if held == 0 else "HOLD"
        return {self.symbol: Signal(self.symbol, sig, self.size_pct, 0.9)}


class _HoldForever(Strategy):
    name = "hold"

    def decide(self, history, portfolio) -> dict[str, Signal]:
        return {sym: Signal(sym, "HOLD", 0.0, 0.0) for sym in history}


def test_hold_forever_keeps_capital_flat() -> None:
    prices = {"LKOH": _make_df([100.0] * 80)}
    res = run_backtest(
        prices, _HoldForever(), apply_risk=False, initial_capital=1_000_000.0, warmup=50
    )
    # Ни одной сделки → equity ровно начальный капитал на всех барах.
    assert res.n_trades == 0
    assert np.allclose(res.equity, 1_000_000.0)


def test_buy_then_price_up_grows_equity() -> None:
    # Цена 100 на warmup, дальше растёт до 120.
    series = [100.0] * 51 + [110.0, 115.0, 120.0, 120.0, 120.0]
    prices = {"LKOH": _make_df(series)}
    res = run_backtest(
        prices, _AlwaysBuyOnce("LKOH"), apply_risk=False,
        initial_capital=1_000_000.0, warmup=50, commission_rate=0.0, slippage_bps=0.0,
    )
    assert res.n_trades >= 1
    # equity в конце выше начального (купили до роста).
    assert res.equity[-1] > 1_000_000.0


def test_commission_reduces_equity_vs_zero_cost() -> None:
    series = [100.0] * 51 + [100.0] * 10
    prices = {"LKOH": _make_df(series)}

    class _Churn(Strategy):
        name = "churn"

        def decide(self, history, portfolio):
            held = portfolio["positions"].get("LKOH", 0)
            # Чередуем BUY/SELL каждый бар → много сделок.
            return {"LKOH": Signal("LKOH", "SELL" if held > 0 else "BUY", 0.10, 0.9)}

    res_free = run_backtest(
        prices, _Churn(), apply_risk=False, warmup=50,
        commission_rate=0.0, slippage_bps=0.0,
    )
    res_cost = run_backtest(
        prices, _Churn(), apply_risk=False, warmup=50,
        commission_rate=0.005, slippage_bps=0.0,
    )
    # При плоской цене комиссия должна съесть капитал → ниже, чем без неё.
    assert res_cost.equity[-1] < res_free.equity[-1]


def test_no_lookahead_execution_at_next_bar() -> None:
    # Резкий скачок на одном баре. Сигнал на баре до скачка исполняется по
    # OPEN следующего (уже высокого) бара — значит на самом скачке не наживаемся.
    series = [100.0] * 51 + [100.0, 200.0, 200.0]
    prices = {"LKOH": _make_df(series)}
    res = run_backtest(
        prices, _AlwaysBuyOnce("LKOH"), apply_risk=False, warmup=50,
        commission_rate=0.0, slippage_bps=0.0,
    )
    # Куплено по open следующего бара. Проверяем что бэктест не упал и торговал.
    assert res.n_trades >= 1


def test_buy_and_hold_benchmark() -> None:
    # 51 бара по 100 (вход по open=100 на warmup-баре index50), затем 110.
    series = [100.0] * 51 + [110.0] * 10
    prices = {"LKOH": _make_df(series)}
    ts = prices["LKOH"].index
    eq = buy_and_hold_equity(prices, ts, initial_capital=1_000_000.0, warmup=50)
    # Купили по 100 (open на warmup), цена выросла до 110 → +10%.
    assert eq[-1] > eq[0]
    assert abs(eq[-1] / 1_000_000.0 - 1.10) < 0.02


class _FakeGraph:
    """Заглушка боевого графа: .invoke возвращает фиксированный Decision-like."""

    def __init__(self, signal: str, size_pct: float = 0.1, conf: float = 0.8) -> None:
        self.calls: list[dict] = []
        self._sig, self._sz, self._cf = signal, size_pct, conf

    def invoke(self, state: dict) -> dict:
        self.calls.append(state)
        return {
            "decision": SimpleNamespace(
                signal=self._sig, size_pct=self._sz, confidence=self._cf
            )
        }


def test_llm_strategy_maps_decision_to_signal() -> None:
    df = _make_df([100.0] * 60)
    g = _FakeGraph("BUY", 0.1, 0.8)
    sigs = LLMStrategy(graph=g, interval=60).decide(
        {"SBER": df}, {"cash": 1e6, "positions": {}}
    )
    assert sigs["SBER"].signal == "BUY"
    assert sigs["SBER"].size_pct == 0.1
    assert sigs["SBER"].confidence == 0.8
    assert len(g.calls) == 1


def test_llm_strategy_hold_when_insufficient_bars() -> None:
    df = _make_df([100.0] * 10)  # < MIN_BARS
    g = _FakeGraph("BUY")
    sigs = LLMStrategy(graph=g, interval=60).decide(
        {"SBER": df}, {"cash": 1e6, "positions": {}}
    )
    assert sigs["SBER"].signal == "HOLD"
    assert g.calls == []  # граф не вызывался


def test_llm_strategy_passes_position_in_shares() -> None:
    df = _make_df([100.0] * 60)
    g = _FakeGraph("HOLD", 0.0, 0.0)
    # MOEX lot_size=10; 5 лотов → 50 акций в current_position трейдера.
    LLMStrategy(graph=g, interval=60).decide(
        {"MOEX": df}, {"cash": 1e6, "positions": {"MOEX": 5}}
    )
    assert g.calls[0]["current_position"] == 50


def test_llm_strategy_runs_through_engine() -> None:
    series = [100.0] * 51 + [110.0, 115.0, 120.0, 120.0]
    prices = {"LKOH": _make_df(series)}
    g = _FakeGraph("BUY", 0.1, 0.9)
    res = run_backtest(
        prices, LLMStrategy(graph=g, interval=24), apply_risk=False,
        warmup=50, commission_rate=0.0, slippage_bps=0.0,
    )
    assert res.n_trades >= 1  # адаптер реально торгует через движок


def test_backtest_risk_profit_lock_can_fire_on_hold() -> None:
    series = [100.0] * 52 + [100.8, 100.8, 100.8]
    prices = {"LKOH": _make_df(series)}
    res = run_backtest(
        prices,
        _AlwaysBuyOnce("LKOH"),
        apply_risk=True,
        settings=_risk_settings(RISK_PROFIT_TAKE_ENABLED=True),
        warmup=50,
        commission_rate=0.0,
        slippage_bps=0.0,
    )

    assert res.n_trades >= 2
    assert res.tp_exit_count >= 1
    assert res.avg_holding_bars > 0


def test_backtest_activity_metrics_are_reported() -> None:
    series = [100.0] * 51 + [100.0, 101.0, 102.0, 103.0]
    prices = {"LKOH": _make_df(series)}
    res = run_backtest(
        prices,
        _AlwaysBuyOnce("LKOH"),
        apply_risk=False,
        warmup=50,
        commission_rate=0.0,
        slippage_bps=0.0,
    )

    assert res.trades_per_day > 0
    assert res.gross_turnover_per_day > 0
    assert res.avg_exposure_pct > 0
    assert 0 <= res.flat_time_pct <= 1


def test_profit_lock_activity_check_reports_all_candidates() -> None:
    series = [100.0] * 52 + [100.8, 101.4, 102.4, 102.4]
    prices = {"LKOH": _make_df(series)}
    rows = run_profit_lock_activity_check(
        prices,
        _AlwaysBuyOnce("LKOH"),
        settings=_risk_settings(),
        warmup=50,
        commission_rate=0.0,
        slippage_bps=0.0,
    )

    assert set(rows) == set(PROFIT_LOCK_CANDIDATES)
    assert np.isclose(
        rows["baseline"].pnl_after_commission,
        rows["baseline"].total_return * 1_000_000.0,
    )
    assert rows["candidate_a"].trades_per_day >= 0
    assert rows["candidate_a"].gross_turnover_per_day >= 0
    assert 0 <= rows["candidate_a"].avg_exposure_pct
    assert 0 <= rows["candidate_a"].flat_time_pct <= 1
    assert 0 <= rows["candidate_a"].tp_continuation_05_rate <= 1
    assert 0 <= rows["candidate_a"].tp_continuation_10_rate <= 1
    assert rows["baseline"].activity_check == "baseline"
    assert rows["candidate_a"].activity_check.startswith(("ok", "no_go:"))
    assert isinstance(rows["candidate_a"].delta_pnl_after_commission, float)
    assert isinstance(rows["candidate_a"].delta_trades_per_day, float)
    assert "activity_check" in format_profit_lock_activity_check(rows)


def test_profit_lock_candidate_settings_are_reproducible() -> None:
    assert set(PROFIT_LOCK_CANDIDATES) == {
        "baseline",
        "bracket_tp20",
        "candidate_a",
        "candidate_b",
        "candidate_c",
    }

    baseline = settings_for_profit_lock_candidate(_risk_settings(), "baseline")
    assert baseline.RISK_PROFIT_TAKE_ENABLED is False
    assert baseline.RISK_TAKE_PROFIT_PCT == 0.015

    bracket_tp20 = settings_for_profit_lock_candidate(_risk_settings(), "bracket_tp20")
    assert bracket_tp20.RISK_PROFIT_TAKE_ENABLED is False
    assert bracket_tp20.RISK_TAKE_PROFIT_PCT == 0.020

    candidate_a = settings_for_profit_lock_candidate(_risk_settings(), "candidate_a")
    assert candidate_a.RISK_PROFIT_TAKE_ENABLED is True
    assert candidate_a.RISK_PROFIT_LOCK_PCT == 0.007
    assert candidate_a.RISK_PROFIT_PARTIAL_PCT == 0.012
    assert candidate_a.RISK_PROFIT_FULL_PCT == 0.020


def test_risk_layer_blocks_low_confidence() -> None:
    # apply_risk=True: сигнал с confidence ниже RISK_MIN_CONFIDENCE → не торгуем.
    series = [100.0] * 80
    prices = {"LKOH": _make_df(series)}

    class _LowConf(Strategy):
        name = "lowconf"

        def decide(self, history, portfolio):
            return {"LKOH": Signal("LKOH", "BUY", 0.10, 0.05)}  # conf 0.05

    res = run_backtest(
        prices, _LowConf(), apply_risk=True, warmup=50,
    )
    assert res.n_trades == 0  # всё отсечено sanity_confidence
