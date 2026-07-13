"""Интеграционный тест графа: LLM и MOEX замоканы, проверяем форму ответа."""

from __future__ import annotations

import numpy as np
import pandas as pd

from agent.graph import nodes as graph_nodes
from agent.graph.build import build_graph
from agent.schemas import (
    AnalystOutput,
    BearArgument,
    BullArgument,
    Decision,
    TraderDecision,
)


def _synthetic_candles(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    idx = pd.date_range("2026-04-01", periods=n, freq="h")
    rets = rng.normal(0, 0.005, n)
    close = 100 * (1 + rets).cumprod()
    high = close * 1.005
    low = close * 0.995
    open_ = close * 1.001
    volume = rng.integers(10_000, 100_000, n).astype(float)
    value = volume * close
    df = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "value": value,
        },
        index=idx,
    )
    df.index.name = "begin"
    return df


def test_graph_returns_valid_decision(mocker) -> None:
    # MOEX замокан — синтетика
    mocker.patch("agent.graph.market_data.get_candles", return_value=_synthetic_candles())

    # LLM возвращает фиксированные структурированные ответы
    fake_analyst = AnalystOutput(
        trend="up",
        momentum="weak_up",
        volatility="normal",
        summary="EMA20 выше EMA50, RSI~55 — умеренный uptrend.",
        confidence=0.7,
    )
    fake_trade = TraderDecision(
        signal="BUY",
        size_pct=0.08,
        confidence=0.65,
        reasoning="Trend up + RSI not overbought.",
    )

    def fake_complete_json(system, user, schema, temperature=0.3):
        if schema is AnalystOutput:
            return fake_analyst
        if schema is TraderDecision:
            return fake_trade
        raise AssertionError(f"unexpected schema: {schema}")

    mocker.patch("agent.llm.client.LLMClient.complete_json", side_effect=fake_complete_json)
    mocker.patch("agent.config.settings.AGENT_PREFILTER_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_EARLY_EXIT_ENABLED", False)

    # debate отключаем — этот тест проверяет analyst → trader pipeline, не debate.
    graph = build_graph(debate_enabled=False, news_enabled=False)
    out = graph.invoke({"symbol": "SBER"})

    assert "decision" in out
    dec: Decision = out["decision"]
    assert dec.symbol == "SBER"
    assert dec.signal == "BUY"
    assert 0.0 <= dec.size_pct <= 0.15
    assert dec.analyst_output.trend == "up"


def test_trader_zeros_size_on_hold(mocker) -> None:
    mocker.patch("agent.graph.market_data.get_candles", return_value=_synthetic_candles())

    fake_analyst = AnalystOutput(
        trend="flat",
        momentum="flat",
        volatility="normal",
        summary="Боковик.",
        confidence=0.4,
    )
    fake_trade = TraderDecision(
        signal="HOLD",
        size_pct=0.12,  # LLM «забыла» обнулить — наша логика должна обнулить
        confidence=0.4,
        reasoning="Сигналы разнонаправленные.",
    )

    def fake_complete_json(system, user, schema, temperature=0.3):
        return fake_analyst if schema is AnalystOutput else fake_trade

    mocker.patch("agent.llm.client.LLMClient.complete_json", side_effect=fake_complete_json)
    mocker.patch("agent.config.settings.AGENT_PREFILTER_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_EARLY_EXIT_ENABLED", False)

    graph = build_graph(debate_enabled=False, news_enabled=False)
    out = graph.invoke({"symbol": "GAZP"})
    assert out["decision"].signal == "HOLD"
    assert out["decision"].size_pct == 0.0


def test_trader_prompt_includes_current_position() -> None:
    captured: dict[str, str] = {}

    class _LLM:
        def complete_json(self, system, user, schema, temperature=0.3):
            captured["user"] = user
            return TraderDecision(
                signal="SELL",
                size_pct=0.10,
                confidence=0.7,
                reasoning="Exit existing long position.",
            )

    analyst = AnalystOutput(
        trend="down",
        momentum="weak_down",
        volatility="normal",
        summary="Weak setup.",
        confidence=0.7,
    )
    out = graph_nodes.trader_node(
        {"symbol": "SBER", "analyst": analyst, "current_position": 50},
        _LLM(),
    )

    assert "Current position: 50 shares." in captured["user"]
    assert out["decision"].signal == "SELL"
    _ = BullArgument, BearArgument  # импорты используются в test_graph_debate.py — pin'им


def test_trader_prompt_renders_negative_position_for_short() -> None:
    """Шорт показывается как отрицательное число акций — LLM видит signed position."""
    captured: dict[str, str] = {}

    class _LLM:
        def complete_json(self, system, user, schema, temperature=0.3):
            captured["user"] = user
            return TraderDecision(
                signal="BUY", size_pct=0.05, confidence=0.7,
                reasoning="Cover the short.",
            )

    analyst = AnalystOutput(
        trend="up", momentum="weak_up", volatility="normal",
        summary="Reversal forming.", confidence=0.7,
    )
    graph_nodes.trader_node(
        {"symbol": "SBER", "analyst": analyst, "current_position": -50},
        _LLM(),
    )

    assert "Current position: -50 shares." in captured["user"]


def test_trader_prompt_includes_commission_rate() -> None:
    captured: dict[str, str] = {}

    class _LLM:
        def complete_json(self, system, user, schema, temperature=0.3):
            captured["user"] = user
            return TraderDecision(
                signal="HOLD",
                size_pct=0.0,
                confidence=0.7,
                reasoning="Costs eat the edge.",
            )

    analyst = AnalystOutput(
        trend="flat",
        momentum="flat",
        volatility="normal",
        summary="No clear edge.",
        confidence=0.7,
    )
    graph_nodes.trader_node(
        {
            "symbol": "SBER",
            "analyst": analyst,
            "current_position": 0,
            "commission_rate": 0.0005,
        },
        _LLM(),
    )

    assert "Estimated commission: 0.0500% per order." in captured["user"]


def test_trader_prompt_includes_portfolio_context() -> None:
    captured: dict[str, str] = {}

    class _LLM:
        def complete_json(self, system, user, schema, temperature=0.3):
            captured["user"] = user
            return TraderDecision(
                signal="HOLD",
                size_pct=0.0,
                confidence=0.7,
                reasoning="Portfolio is already exposed.",
            )

    analyst = AnalystOutput(
        trend="flat",
        momentum="flat",
        volatility="normal",
        summary="No clear edge.",
        confidence=0.7,
    )
    graph_nodes.trader_node(
        {
            "symbol": "SBER",
            "analyst": analyst,
            "current_position": 50,
            "portfolio_context": {
                "nav": 250_000.0,
                "cash": 100_000.0,
                "cash_pct": 0.40,
                "gross_exposure_pct": 0.60,
                "net_exposure_pct": -0.20,
                "current_weight_pct": 0.20,
                "current_value": 50_000.0,
                "positions_count": 3,
            },
        },
        _LLM(),
    )

    assert "Portfolio context: NAV 250000.00 RUB" in captured["user"]
    assert "cash 100000.00 RUB (40.00% NAV)" in captured["user"]
    assert "gross exposure 60.00% NAV" in captured["user"]
    assert "net exposure -20.00% NAV" in captured["user"]
    assert "current ticker weight 20.00% NAV" in captured["user"]


def test_trader_prompt_includes_market_context_without_directional_bias() -> None:
    captured: dict[str, str] = {}

    class _LLM:
        def complete_json(self, system, user, schema, temperature=0.3):
            captured["system"] = system
            captured["user"] = user
            return TraderDecision(
                signal="BUY",
                size_pct=0.05,
                confidence=0.7,
                reasoning="Market context is one input among others.",
            )

    analyst = AnalystOutput(
        trend="up",
        momentum="weak_up",
        volatility="normal",
        summary="Constructive setup.",
        confidence=0.7,
    )
    graph_nodes.trader_node(
        {
            "symbol": "SBER",
            "analyst": analyst,
            "current_position": 0,
            "market_context": {
                "regime": "bullish",
                "fast_return": 0.003,
                "mid_return": 0.006,
                "breadth_up_pct": 0.68,
                "symbols": 19,
            },
        },
        _LLM(),
    )

    assert "Market context: regime=bullish" in captured["user"]
    assert "fast_return_60m +0.30%" in captured["user"]
    assert "mid_return_4h +0.60%" in captured["user"]
    assert "breadth_up 68.00%" in captured["user"]
    assert "Do not mechanically bias BUY or SELL from market regime alone" in captured["system"]
    assert "prefer BUY or HOLD over opening new SHORT" not in captured["system"]
    assert "Open/add SHORT only when ticker-specific bearish evidence clearly overcomes" not in captured["system"]
