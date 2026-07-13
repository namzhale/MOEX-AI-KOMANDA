"""Тесты автономного цикла. Без сети: график, ArenaGo и расписание замоканы."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agent.runtime import scheduler as scheduler_mod
from agent.runtime.journal import JsonlJournal
from agent.runtime.scheduler import TradingScheduler
from agent.schemas import AnalystOutput, Decision


def _settings(tmp_path: Path, respect_moex_hours: bool = False, **overrides) -> SimpleNamespace:
    defaults = dict(
        AGENT_TICKERS="SBER",
        AGENT_TICK_MINUTES=30,
        AGENT_INTERVAL=60,
        AGENT_RESPECT_MOEX_HOURS=respect_moex_hours,
        MARKET_CONTEXT_ENABLED=False,
        MARKET_CONTEXT_FAST_MINUTES=60,
        MARKET_CONTEXT_MID_MINUTES=240,
        MARKET_CONTEXT_RETURN_THRESHOLD=0.0025,
        MARKET_CONTEXT_REVERSAL_THRESHOLD=0.002,
        MARKET_CONTEXT_BULLISH_BREADTH=0.55,
        MARKET_CONTEXT_BEARISH_BREADTH=0.45,
        AGENT_MAX_CONCURRENT_TICKERS=4,
        DRY_RUN=True,
        DATA_DIR=str(tmp_path),
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
        RISK_STOP_LOSS_PCT=0.0,
        RISK_PROFIT_TAKE_ENABLED=False,
        RISK_PROFIT_LOCK_PCT=0.007,
        RISK_PROFIT_PARTIAL_PCT=0.012,
        RISK_PROFIT_FULL_PCT=0.020,
        RISK_PROFIT_LOCK_FRACTION=0.50,
        RISK_PROFIT_PARTIAL_FRACTION=0.50,
        META_REFLECTION_ENABLED=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _decision(symbol: str, signal: str, size_pct: float = 0.08) -> Decision:
    return Decision(
        symbol=symbol,
        signal=signal,
        size_pct=size_pct if signal != "HOLD" else 0.0,
        confidence=0.7,
        reasoning="test",
        analyst_output=AnalystOutput(
            trend="up", momentum="weak_up", volatility="normal",
            summary="ok", confidence=0.7,
        ),
        timestamp=datetime.now(UTC),
    )


def _make_state(symbol: str, signal: str, last_price: float = 300.0, size_pct: float = 0.08):
    candle = SimpleNamespace(close=last_price)
    snapshot = SimpleNamespace(candles=[candle])
    return {"decision": _decision(symbol, signal, size_pct), "snapshot": snapshot}


class _Graph:
    def __init__(self, state: dict) -> None:
        self._state = state

    def invoke(self, _input):
        return self._state


class _Arenago:
    def __init__(
        self,
        cash: float,
        positions: list[dict],
        trades: list[dict] | None = None,
    ) -> None:
        self._cash = cash
        self._positions = positions
        self._trades = trades or []
        self.submitted: list[dict] = []
        self.bot = "t24"

    def get_portfolio(self) -> dict:
        return {"bot": self.bot, "cash": self._cash, "positions": self._positions}

    def get_trades(self) -> list[dict]:
        return self._trades

    def submit_order(self, secid, direction, quantity):
        payload = {"secid": secid, "direction": direction, "quantity": quantity}
        self.submitted.append(payload)
        return {"success": True, "status": "DRY_RUN", "order": payload}

    def close(self) -> None:
        pass


class _GraphShouldNotRun:
    def __init__(self) -> None:
        self.invocations = 0

    def invoke(self, _input):
        self.invocations += 1
        raise AssertionError("LLM graph should not run during Polza balance failsafe")


class _Billing:
    def __init__(self, *balances: float) -> None:
        self.balances = list(balances)
        self.calls = 0

    def get_balance_amount(self) -> float:
        self.calls += 1
        if not self.balances:
            return 0.0
        return self.balances.pop(0)


@pytest.mark.asyncio
async def test_skips_when_arenago_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: False)
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "BUY")),
        arenago=_Arenago(100_000, []),
        settings=_settings(tmp_path),
    )
    rec = await s.run_once()
    assert rec.skipped_reason == "outside_arenago_window"


def test_scheduler_waits_from_tick_start(monkeypatch, tmp_path) -> None:
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "BUY")),
        arenago=_Arenago(100_000, []),
        settings=_settings(tmp_path, AGENT_TICK_MINUTES=1),
    )
    monkeypatch.setattr(scheduler_mod.time, "monotonic", lambda: 125.0)

    assert s._seconds_until_next_tick(100.0) == 35.0


@pytest.mark.asyncio
async def test_skips_with_moex_strict_reason(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: False)
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "BUY")),
        arenago=_Arenago(100_000, []),
        settings=_settings(tmp_path, respect_moex_hours=True),
    )
    rec = await s.run_once()
    assert rec.skipped_reason == "outside_moex_main_session"


@pytest.mark.asyncio
async def test_intick_cash_simulation_blocks_overspend(monkeypatch, tmp_path) -> None:
    """В одном тике 3 BUY-сигнала. Каждый просит 50% NAV, но кэша хватает
    только на один — последующие должны быть зажаты sanity_qty_cash."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)

    # Граф, который возвращает одно и то же решение, но меняет тикер по запросу.
    class _GraphMulti:
        def invoke(self, input_):
            sym = input_["symbol"]
            candle = SimpleNamespace(close=100.0)
            snapshot = SimpleNamespace(candles=[candle])
            decision = _decision(sym, "BUY", size_pct=0.50)
            return {"decision": decision, "snapshot": snapshot}

    arenago = _Arenago(cash=10_000, positions=[])
    # Универс из 3 тикеров; cash=10k, цена=100, размер=50% NAV
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "SBER,GAZP,LKOH"
    s = TradingScheduler(
        graph=_GraphMulti(),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    # После первого BUY local_cash должен снизиться, последующие — не должны
    # тратить больше, чем уже доступно.
    statuses = [rec.decisions[t]["action"]["status"] for t in ("SBER", "GAZP", "LKOH")]
    submitted = [s for s in statuses if s == "buy_submitted"]
    # На 10k cash при notional ~5k за ордер реально можно сделать только 1-2 BUY.
    # Главное — суммарный notional всех BUY ≤ исходного cash * (1 - buffer).
    total_notional = sum(
        a["qty"] * 100.0
        for a in (rec.decisions[t]["action"] for t in ("SBER", "GAZP", "LKOH"))
        if a["status"] == "buy_submitted"
    )
    assert total_notional <= 10_000 * 0.99, (
        f"In-tick simulation failed: total notional {total_notional} > cash buffer"
    )
    assert len(submitted) >= 1  # хотя бы один проходит


@pytest.mark.asyncio
async def test_force_bypasses_session_check(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: False)
    arenago = _Arenago(cash=100_000, positions=[])
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "BUY", last_price=300.0, size_pct=0.05)),
        arenago=arenago,
        settings=_settings(tmp_path),
    )
    rec = await s.run_once(force=True)
    assert rec.skipped_reason is None
    assert "SBER" in rec.decisions
    assert arenago.submitted  # ордер дошёл (в DRY_RUN-режиме)


@pytest.mark.asyncio
async def test_buy_submits_correct_quantity(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    # Используем MOEX (lot=10) для проверки лотовой математики.
    arenago = _Arenago(cash=100_000, positions=[])
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "MOEX"
    s = TradingScheduler(
        graph=_Graph(_make_state("MOEX", "BUY", last_price=300.0, size_pct=0.10)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    # 10% × 100k / (300 × 10) = 3 ЛОТА. quantity = лоты.
    assert arenago.submitted == [{"secid": "MOEX", "direction": "B", "quantity": 3}]
    assert rec.decisions["MOEX"]["action"]["status"] == "buy_submitted"


@pytest.mark.asyncio
async def test_buy_quantity_for_gazp_in_lots(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    arenago = _Arenago(cash=929_944, positions=[])
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "GAZP"
    s = TradingScheduler(
        graph=_Graph(_make_state("GAZP", "BUY", last_price=122.62, size_pct=0.10)),
        arenago=arenago,
        settings=settings_obj,
    )

    rec = await s.run_once()

    # 929_944 × 0.10 / (122.62 × 10) ≈ 75.8 → 75 ЛОТОВ. Реальный объём 750 акций.
    # notional = 75 × 10 × 122.62 = 91_965 ₽.
    assert arenago.submitted == [{"secid": "GAZP", "direction": "B", "quantity": 75}]
    assert rec.decisions["GAZP"]["action"]["notional"] == 91_965.0


@pytest.mark.asyncio
async def test_graph_receives_current_position(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)

    class _GraphCapture:
        def __init__(self) -> None:
            self.inputs: list[dict] = []

        def invoke(self, input_):
            self.inputs.append(dict(input_))
            return _make_state(input_["symbol"], "HOLD", last_price=310.0)

    graph = _GraphCapture()
    # MOEX lot=10: 5 лотов на счёте → трейдер должен увидеть 50 АКЦИЙ.
    positions = [{"secid": "MOEX", "position": 5, "average_price": 290.0, "bot": "t24"}]
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "MOEX"
    s = TradingScheduler(
        graph=graph,
        arenago=_Arenago(cash=100_000, positions=positions),
        settings=settings_obj,
    )

    await s.run_once()

    # 5 лотов × lot_size 10 = 50 акций (конверсия лоты→акции для трейдера).
    assert graph.inputs[0]["current_position"] == 50


@pytest.mark.asyncio
async def test_graph_receives_commission_rate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)

    class _GraphCapture:
        def __init__(self) -> None:
            self.inputs: list[dict] = []

        def invoke(self, input_):
            self.inputs.append(dict(input_))
            return _make_state(input_["symbol"], "HOLD", last_price=310.0)

    graph = _GraphCapture()
    s = TradingScheduler(
        graph=graph,
        arenago=_Arenago(cash=100_000, positions=[]),
        settings=_settings(tmp_path, TRADING_COMMISSION_RATE=0.0005),
    )

    await s.run_once()

    assert graph.inputs[0]["commission_rate"] == 0.0005


@pytest.mark.asyncio
async def test_graph_receives_live_portfolio_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)

    class _GraphCapture:
        def __init__(self) -> None:
            self.inputs: list[dict] = []

        def invoke(self, input_):
            self.inputs.append(dict(input_))
            return _make_state(input_["symbol"], "HOLD", last_price=200.0)

    # MOEX lot=10: current position value = 5 lots * 10 * 300 = 15k.
    # NAV = cash 85k + cost_basis 14.5k + PnL 0.5k = 100k.
    positions = [
        {
            "secid": "MOEX",
            "position": 5,
            "average_price": 290.0,
            "last_price": 300.0,
            "lot_size": 10,
            "bot": "t24",
        }
    ]
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "MOEX"
    graph = _GraphCapture()
    s = TradingScheduler(
        graph=graph,
        arenago=_Arenago(cash=85_000, positions=positions),
        settings=settings_obj,
    )

    await s.run_once()

    ctx = graph.inputs[0]["portfolio_context"]
    assert ctx["nav"] == 100_000.0
    assert ctx["cash"] == 85_000.0
    assert ctx["cash_pct"] == 0.85
    assert ctx["gross_exposure_pct"] == 0.15
    assert ctx["net_exposure_pct"] == 0.15
    assert ctx["current_weight_pct"] == 0.15
    assert ctx["current_value"] == 15_000.0
    assert ctx["positions_count"] == 1


def _candles_from_closes(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-05-20 10:00", periods=len(closes), freq="10min")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000.0,
            "value": close * 1000.0,
        }
    )


def test_market_context_uses_fast_mid_breadth_without_slow(monkeypatch, tmp_path) -> None:
    closes_by_symbol = {
        "SBER": [100.0] * 18 + [100.0, 100.2, 100.4, 100.6, 100.8, 101.0, 101.2],
        "GAZP": [100.0] * 18 + [100.0, 100.3, 100.5, 100.7, 100.9, 101.1, 101.3],
        "LKOH": [100.0] * 18 + [100.0, 99.9, 100.1, 100.2, 100.3, 100.4, 100.5],
        "ROSN": [100.0] * 18 + [100.0, 99.9, 99.8, 99.9, 99.8, 99.9, 99.8],
    }

    def fake_get_candles(symbol, interval, days):
        assert interval == 10
        return _candles_from_closes(closes_by_symbol[symbol])

    monkeypatch.setattr(scheduler_mod.market_data, "get_candles", fake_get_candles)
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "SBER,GAZP,LKOH,ROSN"
    settings_obj.AGENT_INTERVAL = 10
    settings_obj.MARKET_CONTEXT_ENABLED = True
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "HOLD")),
        arenago=_Arenago(cash=100_000, positions=[]),
        settings=settings_obj,
    )

    ctx = s._market_context()

    assert ctx["regime"] == "bullish"
    assert ctx["fast_window_minutes"] == 60
    assert ctx["mid_window_minutes"] == 240
    assert "slow_return" not in ctx
    assert ctx["breadth_up_pct"] == 0.75
    assert ctx["symbols"] == 4
    assert ctx["mid_return"] > 0.0025


@pytest.mark.asyncio
async def test_graph_receives_market_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)

    class _GraphCapture:
        def __init__(self) -> None:
            self.inputs: list[dict] = []

        def invoke(self, input_):
            self.inputs.append(dict(input_))
            return _make_state(input_["symbol"], "HOLD", last_price=310.0)

    graph = _GraphCapture()
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "SBER"
    s = TradingScheduler(
        graph=graph,
        arenago=_Arenago(cash=100_000, positions=[]),
        settings=settings_obj,
    )
    monkeypatch.setattr(
        s,
        "_market_context",
        lambda: {
            "regime": "rebound",
            "fast_return": 0.004,
            "mid_return": -0.006,
            "breadth_up_pct": 0.70,
            "symbols": 1,
        },
    )

    await s.run_once()

    assert graph.inputs[0]["market_context"]["regime"] == "rebound"
    assert graph.inputs[0]["market_context"]["fast_return"] == 0.004


@pytest.mark.asyncio
async def test_nav_uses_latest_price_from_graph_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    # 10 ЛОТОВ MOEX × lot=10 × price=200 = 20k. + cash=1000 = 21k.
    positions = [{"secid": "MOEX", "position": 10, "average_price": 100.0, "bot": "t24"}]
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "MOEX"
    s = TradingScheduler(
        graph=_Graph(_make_state("MOEX", "HOLD", last_price=200.0)),
        arenago=_Arenago(cash=1_000, positions=positions),
        settings=settings_obj,
    )

    rec = await s.run_once()

    assert rec.nav == 21_000.0


@pytest.mark.asyncio
async def test_buy_clamped_by_concentration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    # 50 лотов MOEX × lot=10 × 300 = 150k позиция; cash=100k → NAV=250k; вес=60%>15%
    # → blocked by instrument_concentration (room = 0).
    positions = [{"secid": "MOEX", "position": 50, "average_price": 300.0, "bot": "t24"}]
    nav = 100_000 + 50 * 10 * 300
    arenago = _Arenago(cash=100_000, positions=positions)
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "MOEX"
    s = TradingScheduler(
        graph=_Graph(_make_state("MOEX", "BUY", last_price=300.0, size_pct=0.10)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    assert arenago.submitted == []
    action = rec.decisions["MOEX"]["action"]
    assert action["status"] == "risk_block"
    assert action["gate"] == "instrument_concentration"
    _ = rec, nav


@pytest.mark.asyncio
async def test_parallel_graph_invokes_with_intick_simulation(monkeypatch, tmp_path) -> None:
    """5 тикеров параллельно прогоняются через граф (LLM-фаза), потом
    последовательно через Risk Officer с in-tick cash simulation.

    Цель: убедиться что (1) граф был вызван по разу на каждый тикер,
    (2) кэш правильно убывает между тикерами, (3) когда кэш кончается,
    последующие BUY режутся sanity_qty_cash.
    """
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)

    class _GraphMulti:
        def __init__(self) -> None:
            self.invocations: list[str] = []

        def invoke(self, input_):
            sym = input_["symbol"]
            self.invocations.append(sym)
            candle = SimpleNamespace(close=100.0)
            snapshot = SimpleNamespace(candles=[candle])
            decision = _decision(sym, "BUY", size_pct=0.50)
            return {"decision": decision, "snapshot": snapshot}

    graph = _GraphMulti()
    arenago = _Arenago(cash=10_000, positions=[])
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "SBER,GAZP,LKOH,VTBR,MOEX"
    s = TradingScheduler(
        graph=graph,
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()

    # Граф вызван по разу на каждый тикер
    assert sorted(graph.invocations) == sorted(("SBER", "GAZP", "LKOH", "VTBR", "MOEX"))
    # Кэш не превысили: ни один реальный submit не превышает изначальные 10k
    total_notional = sum(
        a["qty"] * 100.0
        for a in (rec.decisions[t]["action"] for t in ("SBER", "GAZP", "LKOH", "VTBR", "MOEX"))
        if a["status"] == "buy_submitted"
    )
    assert total_notional <= 10_000 * 0.99
    # Хоть один BUY должен пройти (по 5к notional умещается в 10к)
    assert any(
        rec.decisions[t]["action"]["status"] == "buy_submitted"
        for t in ("SBER", "GAZP", "LKOH", "VTBR", "MOEX")
    )


@pytest.mark.asyncio
async def test_compute_decision_failure_does_not_break_other_tickers(monkeypatch, tmp_path) -> None:
    """Если граф взорвался на одном тикере — остальные продолжают работать."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)

    class _GraphFlaky:
        def invoke(self, input_):
            sym = input_["symbol"]
            if sym == "GAZP":
                raise RuntimeError("graph blew up on GAZP")
            candle = SimpleNamespace(close=100.0)
            snapshot = SimpleNamespace(candles=[candle])
            return {"decision": _decision(sym, "HOLD"), "snapshot": snapshot}

    arenago = _Arenago(cash=100_000, positions=[])
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "SBER,GAZP,LKOH"
    s = TradingScheduler(
        graph=_GraphFlaky(),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()

    # GAZP — в errors, остальные — в decisions
    assert "GAZP" in rec.errors
    assert "SBER" in rec.decisions
    assert "LKOH" in rec.decisions


@pytest.mark.asyncio
async def test_buy_skipped_when_already_at_cap(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    # Позиция уже >15% от NAV — новый BUY не должен отправляться
    positions = [{"secid": "SBER", "position": 100, "average_price": 300.0, "bot": "t24"}]
    arenago = _Arenago(cash=10_000, positions=positions)  # NAV=40k; SBER=75%
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "BUY", last_price=300.0, size_pct=0.10)),
        arenago=arenago,
        settings=_settings(tmp_path),
    )
    rec = await s.run_once()
    assert arenago.submitted == []
    action = rec.decisions["SBER"]["action"]
    assert action["status"] == "risk_block"
    assert action["gate"] == "instrument_concentration"


@pytest.mark.asyncio
async def test_sell_partial_close_by_size_pct(monkeypatch, tmp_path) -> None:
    """SELL на лонге трактуется как «уменьшить позицию по size_pct».
    size_pct=0.08 × 100k / (305 × 10) ≈ 2.62 → 2 ЛОТА → partial close (current=50)."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    positions = [{"secid": "MOEX", "position": 50, "average_price": 290.0, "bot": "t24"}]
    arenago = _Arenago(cash=100_000, positions=positions)
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "MOEX"
    s = TradingScheduler(
        graph=_Graph(_make_state("MOEX", "SELL", last_price=305.0)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    assert arenago.submitted == [{"secid": "MOEX", "direction": "S", "quantity": 2}]
    action = rec.decisions["MOEX"]["action"]
    assert action["status"] == "sell_submitted"
    assert action["op_type"] == "close_long"


@pytest.mark.asyncio
async def test_sell_with_no_position_opens_short(monkeypatch, tmp_path) -> None:
    """SELL без позиции теперь открывает шорт (sign-aware semantics)."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    arenago = _Arenago(cash=100_000, positions=[])
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "SELL")),
        arenago=arenago,
        settings=_settings(tmp_path),
    )
    rec = await s.run_once()
    action = rec.decisions["SBER"]["action"]
    assert action["status"] == "sell_submitted"
    assert action["op_type"] == "open_short"
    assert arenago.submitted == [{"secid": "SBER", "direction": "S", "quantity": action["qty"]}]
    assert action["qty"] > 0


@pytest.mark.asyncio
async def test_hold_does_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    arenago = _Arenago(cash=100_000, positions=[])
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "HOLD")),
        arenago=arenago,
        settings=_settings(tmp_path),
    )
    rec = await s.run_once()
    assert arenago.submitted == []
    assert rec.decisions["SBER"]["action"]["status"] == "hold"


@pytest.mark.asyncio
async def test_daily_trade_limit_skips_tick_at_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    trades = [{"secid": "SBER"} for _ in range(200)]
    arenago = _Arenago(cash=100_000, positions=[], trades=trades)
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "BUY", last_price=300.0, size_pct=0.05)),
        arenago=arenago,
        settings=_settings(tmp_path, ARENAGO_DAILY_TRADE_LIMIT=200),
    )

    rec = await s.run_once()

    assert rec.skipped_reason == "daily_trade_limit_reached: 200/200"
    assert arenago.submitted == []


@pytest.mark.asyncio
async def test_daily_trade_limit_blocks_orders_inside_tick(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)

    class _GraphMulti:
        def invoke(self, input_):
            return _make_state(input_["symbol"], "BUY", last_price=100.0, size_pct=0.02)

    trades = [{"secid": "OLD"} for _ in range(199)]
    arenago = _Arenago(cash=100_000, positions=[], trades=trades)
    settings_obj = _settings(tmp_path, ARENAGO_DAILY_TRADE_LIMIT=200)
    settings_obj.AGENT_TICKERS = "SBER,GAZP"
    s = TradingScheduler(
        graph=_GraphMulti(),
        arenago=arenago,
        settings=settings_obj,
    )

    rec = await s.run_once()

    assert len(arenago.submitted) == 1
    statuses = [rec.decisions[t]["action"]["status"] for t in ("SBER", "GAZP")]
    assert "daily_trade_limit" in statuses


@pytest.mark.asyncio
async def test_polza_balance_depleted_starts_grace_without_llm_or_orders(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    graph = _GraphShouldNotRun()
    arenago = _Arenago(
        cash=50_000,
        positions=[{"secid": "SBER", "position": 2, "average_price": 300.0}],
    )
    s = TradingScheduler(
        graph=graph,
        arenago=arenago,
        settings=_settings(
            tmp_path,
            POLZA_BALANCE_FAILSAFE_ENABLED=True,
            POLZA_BALANCE_MIN_RUB=0.01,
            POLZA_BALANCE_GRACE_MINUTES=30,
        ),
        billing_client=_Billing(0.0),
    )

    rec = await s.run_once()

    assert rec.skipped_reason == "polza_balance_depleted_grace"
    assert graph.invocations == 0
    assert arenago.submitted == []
    assert any(r.get("event") == "polza_balance_depleted" for r in s.journal.tail(20))


@pytest.mark.asyncio
async def test_polza_balance_failsafe_closes_all_positions_after_grace(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    graph = _GraphShouldNotRun()
    positions = [
        {"secid": "SBER", "position": 2, "average_price": 300.0},
        {"secid": "LKOH", "position": -3, "average_price": 5100.0},
    ]
    arenago = _Arenago(cash=50_000, positions=positions)
    s = TradingScheduler(
        graph=graph,
        arenago=arenago,
        settings=_settings(
            tmp_path,
            AGENT_TICKERS="SBER,LKOH",
            POLZA_BALANCE_FAILSAFE_ENABLED=True,
            POLZA_BALANCE_MIN_RUB=0.01,
            POLZA_BALANCE_GRACE_MINUTES=30,
        ),
        billing_client=_Billing(0.0),
    )
    s._polza_balance_depleted_at = datetime.now(UTC) - timedelta(minutes=31)

    rec = await s.run_once()

    assert rec.skipped_reason == "polza_balance_failsafe_closed_positions"
    assert graph.invocations == 0
    assert arenago.submitted == [
        {"secid": "SBER", "direction": "S", "quantity": 2},
        {"secid": "LKOH", "direction": "B", "quantity": 3},
    ]
    assert rec.decisions["SBER"]["action"]["op_type"] == "polza_failsafe_close_long"
    assert rec.decisions["LKOH"]["action"]["op_type"] == "polza_failsafe_cover_short"
    assert any(
        r.get("event") == "polza_balance_failsafe_close_all"
        for r in s.journal.tail(20)
    )


@pytest.mark.asyncio
async def test_journal_records_tick(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "HOLD")),
        arenago=_Arenago(cash=50_000, positions=[]),
        settings=_settings(tmp_path),
    )
    await s.run_once()
    records = s.journal.tail(10)
    assert any(r["event"] == "tick" for r in records)


def test_journal_tail_empty_file(tmp_path) -> None:
    j = JsonlJournal(tmp_path / "missing.jsonl")
    assert j.tail(10) == []


# ── Short-selling: scheduler integration ─────────────────────────────────────


@pytest.mark.asyncio
async def test_nav_with_short_position_drops_when_price_rises(
    monkeypatch, tmp_path
) -> None:
    """Collateral-модель: шорт -100 лотов LKOH (lot=1) @ avg 200. Залог 20k был
    списан → available cash=80k. Цена выросла до 220 →
    NAV = 80k + cost_basis(20k) + pnl_short(100×(200−220)=−2k) = 98k."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    positions = [{"secid": "LKOH", "position": -100, "average_price": 200.0, "bot": "t24"}]
    arenago = _Arenago(cash=80_000, positions=positions)
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "LKOH"
    s = TradingScheduler(
        graph=_Graph(_make_state("LKOH", "HOLD", last_price=220.0)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    assert rec.nav == 98_000.0


def test_nav_collateral_model_matches_real_arenago() -> None:
    """Реальный портфель из ArenaGo /api/positions (4 позиции, 3 шорта + 1 лонг).
    NAV должен быть ≈ стартовому 1M (цены почти не двигались)."""
    positions = [
        {"secid": "GMKN", "position": -592, "lot_size": 10.0,
         "average_price": 128.96, "last_price": 128.96},
        {"secid": "MGNT", "position": -6, "lot_size": 1.0,
         "average_price": 2447.5, "last_price": 2442.5},
        {"secid": "MTSS", "position": 60, "lot_size": 10.0,
         "average_price": 226.1, "last_price": 225.9},
        {"secid": "T", "position": -159, "lot_size": 1.0,
         "average_price": 314.52, "last_price": 314.76},
    ]
    s = TradingScheduler.__new__(TradingScheduler)  # без __init__ (без сети)
    nav = TradingScheduler._portfolio_nav(s, cash=40_470.0, positions=positions)
    # 40470 + 763443 + 14715 + 135540 + 49970 ≈ 1_004_138
    assert abs(nav - 1_004_138) < 5.0


def test_apply_summary_delta_opens_short_when_no_position() -> None:
    """SELL без позиции (open_short) → position = -qty, cash УМЕНЬШАЕТСЯ на залог."""
    summary = {
        "last_price": 200.0,
        "action": {
            "status": "sell_submitted",
            "op_type": "open_short",
            "qty": 100,
            "notional": 20_000.0,
            "response": {"success": True, "status": "DRY_RUN"},
        },
    }
    cash, positions = TradingScheduler._apply_summary_delta(
        summary, "LKOH",
        live_cash=100_000.0, live_positions=[],
        lot_sizes={"LKOH": 1},
    )
    assert cash == 80_000.0  # 100k − 100 × 1 × 200 (collateral locked)
    assert len(positions) == 1
    assert positions[0]["position"] == -100.0
    assert positions[0]["average_price"] == 200.0


def test_apply_summary_delta_covers_short_partially() -> None:
    """BUY 30 лотов LKOH (cover_short) когда position=-100 → -70, cash += залог."""
    summary = {
        "last_price": 200.0,
        "action": {
            "status": "buy_submitted",
            "op_type": "cover_short",
            "qty": 30,
            "notional": 6_000.0,
            "response": {"success": True, "status": "DRY_RUN"},
        },
    }
    positions = [{"secid": "LKOH", "position": -100, "average_price": 200.0, "bot": "t24"}]
    cash, new_positions = TradingScheduler._apply_summary_delta(
        summary, "LKOH",
        live_cash=80_000.0, live_positions=positions,
        lot_sizes={"LKOH": 1},
    )
    assert cash == 86_000.0  # 80k + 30 × 1 × 200 (collateral returned on cover)
    assert new_positions[0]["position"] == -70.0


def test_apply_summary_delta_flip_long_to_short() -> None:
    """FLIP long→short: close 30 (cash↑6k) + open 20 short (cash↓4k) → net +2k."""
    summary = {
        "last_price": 200.0,
        "action": {
            "status": "flip_executed",
            "op_type": "flip_long_to_short",
            "close_qty": 30,
            "open_qty": 20,
            "notional": 10_000.0,
            "response": {"success": True},
        },
    }
    positions = [{"secid": "LKOH", "position": 30, "average_price": 180.0, "bot": "t24"}]
    cash, new_positions = TradingScheduler._apply_summary_delta(
        summary, "LKOH",
        live_cash=100_000.0, live_positions=positions,
        lot_sizes={"LKOH": 1},
    )
    # close 30 @ 200 (закрытие лонга, cash += 6k); open 20 шорт (cash −= 4k). = 102k.
    assert cash == 102_000.0
    assert new_positions[0]["position"] == -20.0


@pytest.mark.asyncio
async def test_flip_long_to_short_sends_two_orders(monkeypatch, tmp_path) -> None:
    """pos=+10 лотов LKOH (lot=1), SELL size_pct=0.10 cash=100k @ 300 →
    desired = 33 лота > current=10 → flip (close 10 + open 23)."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    positions = [{"secid": "LKOH", "position": 10, "average_price": 300.0, "bot": "t24"}]
    arenago = _Arenago(cash=100_000, positions=positions)
    settings_obj = _settings(tmp_path, AGENT_ALLOW_FLIP=True)
    settings_obj.AGENT_TICKERS = "LKOH"
    s = TradingScheduler(
        graph=_Graph(_make_state("LKOH", "SELL", last_price=300.0, size_pct=0.10)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    assert arenago.submitted == [
        {"secid": "LKOH", "direction": "S", "quantity": 10},
        {"secid": "LKOH", "direction": "S", "quantity": 23},
    ]
    action = rec.decisions["LKOH"]["action"]
    assert action["status"] == "flip_executed"
    assert action["op_type"] == "flip_long_to_short"
    assert action["close_qty"] == 10
    assert action["open_qty"] == 23


@pytest.mark.asyncio
async def test_flip_short_to_long_sends_two_orders(monkeypatch, tmp_path) -> None:
    """pos=-10 лотов LKOH (lot=1), BUY size_pct=0.10 → desired=33 > |pos|=10 → flip."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    positions = [{"secid": "LKOH", "position": -10, "average_price": 300.0, "bot": "t24"}]
    arenago = _Arenago(cash=100_000, positions=positions)
    settings_obj = _settings(tmp_path, AGENT_ALLOW_FLIP=True)
    settings_obj.AGENT_TICKERS = "LKOH"
    s = TradingScheduler(
        graph=_Graph(_make_state("LKOH", "BUY", last_price=300.0, size_pct=0.10)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    assert arenago.submitted == [
        {"secid": "LKOH", "direction": "B", "quantity": 10},
        {"secid": "LKOH", "direction": "B", "quantity": 23},
    ]
    action = rec.decisions["LKOH"]["action"]
    assert action["status"] == "flip_executed"
    assert action["op_type"] == "flip_short_to_long"


@pytest.mark.asyncio
async def test_risk_trim_covers_oversized_short_on_hold(monkeypatch, tmp_path) -> None:
    """Кейс GMKN: LLM сказал HOLD, но шорт раздут (>cap) и в плюсе → risk_trim
    откупает к кэпу одной заявкой BUY, перехватывая HOLD."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    # MOEX lot=10, шорт −50 лотов @ avg 300, цена 270 → в плюсе.
    positions = [{"secid": "MOEX", "position": -50, "average_price": 300.0, "bot": "t24"}]
    arenago = _Arenago(cash=100_000, positions=positions)
    settings_obj = _settings(tmp_path, RISK_TRIM_ENABLED=True)
    settings_obj.AGENT_TICKERS = "MOEX"
    s = TradingScheduler(
        graph=_Graph(_make_state("MOEX", "HOLD", last_price=270.0)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    action = rec.decisions["MOEX"]["action"]
    assert action["status"] == "buy_submitted"
    assert action["op_type"] == "risk_trim_cover"
    cap_qty = int(0.15 * rec.nav / (10 * 270.0))
    expected_qty = 50 - cap_qty
    assert arenago.submitted == [
        {"secid": "MOEX", "direction": "B", "quantity": expected_qty}
    ]


@pytest.mark.asyncio
async def test_take_profit_submits_reduce_order_on_hold(monkeypatch, tmp_path) -> None:
    """HOLD + прибыльный шорт (≥TP%) → take_profit_cover закрывает всю позицию BUY."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    # MOEX lot=10, шорт -50 @ avg 300, цена 290 → pnl +3.33% ≥ TP 2%.
    positions = [{"secid": "MOEX", "position": -50, "average_price": 300.0, "bot": "t24"}]
    arenago = _Arenago(cash=100_000, positions=positions)
    settings_obj = _settings(tmp_path, RISK_TAKE_PROFIT_PCT=0.02)
    settings_obj.AGENT_TICKERS = "MOEX"
    s = TradingScheduler(
        graph=_Graph(_make_state("MOEX", "HOLD", last_price=290.0)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    action = rec.decisions["MOEX"]["action"]
    assert action["status"] == "buy_submitted"
    assert action["op_type"] == "take_profit_cover"
    assert arenago.submitted == [{"secid": "MOEX", "direction": "B", "quantity": 50}]


@pytest.mark.asyncio
async def test_profit_lock_partial_step_is_not_repeated_from_journal(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    positions = [{"secid": "SBER", "position": 10, "average_price": 100.0, "bot": "t24"}]
    arenago = _Arenago(cash=100_000, positions=positions)
    settings_obj = _settings(
        tmp_path,
        RISK_PROFIT_TAKE_ENABLED=True,
        RISK_PROFIT_LOCK_PCT=0.007,
        RISK_PROFIT_PARTIAL_PCT=0.012,
        RISK_PROFIT_FULL_PCT=0.020,
        RISK_PROFIT_LOCK_FRACTION=0.50,
        RISK_PROFIT_PARTIAL_FRACTION=0.50,
    )
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "HOLD", last_price=100.8)),
        arenago=arenago,
        settings=settings_obj,
        journal=JsonlJournal(tmp_path / "decisions.jsonl"),
    )

    first = await s.run_once()
    second = await s.run_once()

    first_action = first.decisions["SBER"]["action"]
    assert first_action["status"] == "sell_submitted"
    assert first_action["op_type"] == "take_profit_sell"
    assert first_action["profit_step"] == 0.007
    assert arenago.submitted == [{"secid": "SBER", "direction": "S", "quantity": 5}]
    assert second.decisions["SBER"]["action"]["status"] == "hold"


@pytest.mark.asyncio
async def test_noflip_single_close_order(monkeypatch, tmp_path) -> None:
    """No-flip: SELL на лонге, desired > позиции → одна close-заявка до флэта."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    # LKOH lot=1, лонг +10, SELL size_pct=0.10 cash 100k @300 → desired=33 > 10.
    positions = [{"secid": "LKOH", "position": 10, "average_price": 300.0, "bot": "t24"}]
    arenago = _Arenago(cash=100_000, positions=positions)
    settings_obj = _settings(tmp_path)  # AGENT_ALLOW_FLIP=False по умолчанию
    settings_obj.AGENT_TICKERS = "LKOH"
    s = TradingScheduler(
        graph=_Graph(_make_state("LKOH", "SELL", last_price=300.0, size_pct=0.10)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    assert arenago.submitted == [{"secid": "LKOH", "direction": "S", "quantity": 10}]
    action = rec.decisions["LKOH"]["action"]
    assert action["status"] == "sell_submitted"
    assert action["op_type"] == "close_long"


# ── turnover-pace monitor (observability) ────────────────────────────────────


@pytest.mark.asyncio
async def test_turnover_tick_gross_in_journal(monkeypatch, tmp_path) -> None:
    """tick_gross в журнале = ноционал исполненных сделок (HOLD не считается)."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    arenago = _Arenago(cash=100_000, positions=[])
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "SBER"
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "BUY", last_price=300.0, size_pct=0.05)),
        arenago=arenago,
        settings=settings_obj,
    )
    rec = await s.run_once()
    action = rec.decisions["SBER"]["action"]
    assert action["status"] == "buy_submitted"
    ticks = [r for r in s.journal.tail(20) if r.get("event") == "tick"]
    assert ticks
    assert ticks[-1]["tick_gross"] == action["notional"]
    assert ticks[-1]["cum_gross_today"] == action["notional"]


@pytest.mark.asyncio
async def test_turnover_cum_reads_today_from_journal(monkeypatch, tmp_path) -> None:
    """cum_gross_today включает прошлые сегодняшние tick-записи (переживает рестарт)."""
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    arenago = _Arenago(cash=100_000, positions=[])
    settings_obj = _settings(tmp_path)
    settings_obj.AGENT_TICKERS = "SBER"
    s = TradingScheduler(
        graph=_Graph(_make_state("SBER", "BUY", last_price=300.0, size_pct=0.05)),
        arenago=arenago,
        settings=settings_obj,
    )
    s.journal.write("tick", n=0, tick_gross=10000.0)  # «прошлый» тик за сегодня
    rec = await s.run_once()
    action = rec.decisions["SBER"]["action"]
    ticks = [r for r in s.journal.tail(20) if r.get("event") == "tick"]
    assert ticks[-1]["cum_gross_today"] == 10000.0 + action["notional"]


def test_trader_prompt_has_ev_discipline() -> None:
    from agent.graph.nodes import TRADER_SYSTEM

    low = TRADER_SYSTEM.lower()
    assert "expected move" in low
    assert "upside" in low and "downside" in low


def test_trader_prompt_mentions_soft_profit_lock() -> None:
    from agent.graph.nodes import TRADER_SYSTEM

    low = TRADER_SYSTEM.lower()
    assert "soft profit-lock" in low
    assert "+0.7%" in low
    assert "+1.2%" in low
    assert "+2.0%" in low
    assert "-2.0%" in low
    assert "partial close" in low
