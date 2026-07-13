"""Bull/Bear debate node + graph wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent.config import settings
from agent.graph import debate as debate_mod
from agent.graph.build import build_graph
from agent.graph.debate import bull_bear_debate_node
from agent.graph.nodes import trader_node
from agent.schemas import (
    AnalystOutput,
    BearArgument,
    BullArgument,
    Candle,
    Decision,
    MarketSnapshot,
    TraderDecision,
)


def _fake_snapshot(symbol: str = "SBER") -> MarketSnapshot:
    t = datetime.now(UTC)
    c = Candle(
        begin=t, open=100.0, high=101.0, low=99.0, close=100.5,
        volume=1000.0, value=100500.0,
    )
    return MarketSnapshot(
        symbol=symbol,
        interval=60,
        candles=[c],
        features={"rsi14": 55.0, "macd_hist": 0.2, "ema20": 100.0, "ema50": 99.0, "close": 100.5},
    )


def _patch_graph_market(monkeypatch) -> None:
    import agent.graph.market_data as md

    monkeypatch.setattr(md, "load_market_snapshot", lambda symbol, **kw: _fake_snapshot(symbol))
    monkeypatch.setattr(settings, "AGENT_PREFILTER_ENABLED", False)
    monkeypatch.setattr(settings, "AGENT_EARLY_EXIT_ENABLED", False)


def _analyst() -> AnalystOutput:
    return AnalystOutput(
        trend="up", momentum="weak_up", volatility="normal",
        summary="EMA20 > EMA50, RSI 55", confidence=0.65,
    )


def _bull(round_idx: int) -> BullArgument:
    return BullArgument(
        thesis=f"bull thesis r{round_idx}",
        key_points=["point a", "point b"],
        confidence=0.7,
        rebuttal=None if round_idx == 0 else "vs bear",
    )


def _bear(round_idx: int) -> BearArgument:
    return BearArgument(
        thesis=f"bear thesis r{round_idx}",
        key_points=["point x", "point y"],
        confidence=0.5,
        rebuttal=None if round_idx == 0 else "vs bull",
    )


class _LLM:
    """LLM-мок: возвращает заранее заготовленные ответы по очереди по типу схемы."""

    def __init__(self, bulls: list[BullArgument], bears: list[BearArgument], trader_decision: TraderDecision | None = None) -> None:
        self.bulls = list(bulls)
        self.bears = list(bears)
        self.trader_decision = trader_decision
        self.calls: list[str] = []

    def complete_json(self, system, user, schema, temperature: float = 0.3):
        self.calls.append(schema.__name__)
        if schema is BullArgument:
            return self.bulls.pop(0)
        if schema is BearArgument:
            return self.bears.pop(0)
        if schema is TraderDecision:
            return self.trader_decision or TraderDecision(
                signal="HOLD", size_pct=0.0, confidence=0.5, reasoning="ok"
            )
        raise AssertionError(f"unexpected schema {schema}")


# ── Прямые тесты узла ────────────────────────────────────────────────────────


def test_debate_runs_n_rounds(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AGENT_DEBATE_ROUNDS", 2)
    llm = _LLM(bulls=[_bull(0), _bull(1)], bears=[_bear(0), _bear(1)])
    state = {"symbol": "SBER", "analyst": _analyst()}
    out = bull_bear_debate_node(state, llm)

    assert "debate_arguments" in out
    assert len(out["debate_arguments"]) == 2
    # 2 раунда × 2 вызова (bull + bear) = 4 LLM-вызова
    assert llm.calls == ["BullArgument", "BearArgument", "BullArgument", "BearArgument"]
    # round 0 — без rebuttal, round 1 — с rebuttal
    assert out["debate_arguments"][0]["bull"]["rebuttal"] is None
    assert out["debate_arguments"][1]["bull"]["rebuttal"] is not None


def test_zero_rounds_passes_through(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AGENT_DEBATE_ROUNDS", 0)
    llm = _LLM(bulls=[], bears=[])
    state = {"symbol": "SBER", "analyst": _analyst()}
    out = bull_bear_debate_node(state, llm)

    assert out == {"debate_arguments": []}
    assert llm.calls == []  # ни одного LLM-вызова


# ── Trader-узел видит debate ─────────────────────────────────────────────────


def test_trader_sees_debate_in_state() -> None:
    decision = TraderDecision(
        signal="BUY", size_pct=0.08, confidence=0.7, reasoning="bull won r2"
    )
    llm = _LLM(bulls=[], bears=[], trader_decision=decision)

    debate_args = [
        {"round": 0, "bull": _bull(0).model_dump(), "bear": _bear(0).model_dump()},
        {"round": 1, "bull": _bull(1).model_dump(), "bear": _bear(1).model_dump()},
    ]
    state = {
        "symbol": "SBER",
        "analyst": _analyst(),
        "debate_arguments": debate_args,
    }
    out = trader_node(state, llm)

    assert isinstance(out["decision"], Decision)
    assert out["decision"].signal == "BUY"
    assert "BullArgument" not in llm.calls  # debate уже завершён
    assert llm.calls == ["TraderDecision"]


def test_trader_works_without_debate() -> None:
    """Если debate отключён → trader всё ещё работает (debate_arguments=[])."""
    decision = TraderDecision(
        signal="HOLD", size_pct=0.0, confidence=0.4, reasoning="mixed"
    )
    llm = _LLM(bulls=[], bears=[], trader_decision=decision)
    state = {"symbol": "SBER", "analyst": _analyst()}
    out = trader_node(state, llm)
    assert out["decision"].signal == "HOLD"


# ── Wiring графа ─────────────────────────────────────────────────────────────


class _LLMForGraph(_LLM):
    """Variant with analyst+trader+debate baked in. graph.invoke() drives everything."""

    def __init__(self) -> None:
        analyst_out = _analyst()
        trader = TraderDecision(
            signal="HOLD", size_pct=0.0, confidence=0.4, reasoning="mixed"
        )
        super().__init__(
            bulls=[_bull(0), _bull(1)],
            bears=[_bear(0), _bear(1)],
            trader_decision=trader,
        )
        self._analyst = analyst_out

    def complete_json(self, system, user, schema, temperature: float = 0.3):
        # Счётчик зовём один раз: либо здесь (analyst), либо в super (всё остальное).
        if schema is AnalystOutput:
            self.calls.append(schema.__name__)
            return self._analyst
        return super().complete_json(system, user, schema, temperature)


def test_graph_includes_debate_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AGENT_DEBATE_ROUNDS", 2)
    _patch_graph_market(monkeypatch)

    llm = _LLMForGraph()
    graph = build_graph(llm=llm, debate_enabled=True, news_enabled=False)
    state = graph.invoke({"symbol": "SBER", "interval": 60})

    # В цепочке: analyst (1) + 2 раунда debate (4) + trader (1) = 6 LLM-вызовов
    assert llm.calls == [
        "AnalystOutput",
        "BullArgument", "BearArgument",
        "BullArgument", "BearArgument",
        "TraderDecision",
    ]
    assert state["decision"].signal == "HOLD"
    assert len(state["debate_arguments"]) == 2


def test_graph_skips_debate_when_disabled(monkeypatch) -> None:
    _patch_graph_market(monkeypatch)

    llm = _LLMForGraph()
    graph = build_graph(llm=llm, debate_enabled=False, news_enabled=False)
    state = graph.invoke({"symbol": "SBER", "interval": 60})

    # Без debate: analyst (1) + trader (1) = 2 LLM-вызова
    assert llm.calls == ["AnalystOutput", "TraderDecision"]
    assert state.get("debate_arguments") in (None, [])
