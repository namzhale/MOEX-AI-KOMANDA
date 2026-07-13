from __future__ import annotations

from datetime import UTC, datetime

from agent.graph.build import build_graph
from agent.graph.reflection_node import TradeHypothesis
from agent.schemas import AnalystOutput, Decision, TraderDecision


def test_graph_includes_reflection_after_trader(mocker) -> None:
    mocker.patch("agent.config.settings.REFLECTION_ENABLED", True)
    mocker.patch("agent.config.settings.REFLECTION_IN_GRAPH", True)
    mocker.patch("agent.config.settings.AGENT_PREFILTER_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_EARLY_EXIT_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_NEWS_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_DEBATE_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_PREFILTER_ENABLED", False)
    mocker.patch("agent.graph.market_data.get_candles", return_value=_fake_df())
    mocker.patch("agent.memory.retrieval.format_memory_block", return_value="")
    mocker.patch("agent.runtime.reflection._persist_reflection")

    analyst = AnalystOutput(
        trend="up", momentum="weak_up", volatility="normal",
        summary="Uptrend.", confidence=0.7,
    )
    trade = TraderDecision(signal="BUY", size_pct=0.05, confidence=0.6, reasoning="Go long.")
    hypothesis = TradeHypothesis(hypothesis="Need follow-through above EMA20.", tags=["buy"], importance=0.5)

    calls: list = []

    def fake_complete(system, user, schema, temperature=0.3):
        calls.append(schema.__name__)
        if schema is AnalystOutput:
            return analyst
        if schema is TraderDecision:
            return trade
        if schema is TradeHypothesis:
            return hypothesis
        raise AssertionError(schema)

    mocker.patch("agent.llm.client.LLMClient.complete_json", side_effect=fake_complete)

    graph = build_graph(debate_enabled=False, news_enabled=False)
    out = graph.invoke({"symbol": "SBER"})

    assert out["decision"].signal == "BUY"
    assert out.get("reflection_written") is True
    assert "TradeHypothesis" in calls


def _fake_df():
    import numpy as np
    import pandas as pd

    n = 200
    idx = pd.date_range("2026-04-01", periods=n, freq="h")
    close = 100 * (1 + np.random.default_rng(1).normal(0, 0.005, n)).cumprod()
    df = pd.DataFrame(
        {
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 10000.0, "value": close * 10000,
        },
        index=idx,
    )
    df.index.name = "begin"
    return df
