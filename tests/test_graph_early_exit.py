from __future__ import annotations

import numpy as np
import pandas as pd

from agent.graph import nodes as graph_nodes
from agent.graph.build import build_graph
from agent.schemas import AnalystOutput


def _synthetic_candles(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    idx = pd.date_range("2026-04-01", periods=n, freq="h")
    rets = rng.normal(0, 0.005, n)
    close = 100 * (1 + rets).cumprod()
    df = pd.DataFrame(
        {
            "open": close * 1.001,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": rng.integers(10_000, 100_000, n).astype(float),
            "value": rng.integers(10_000, 100_000, n).astype(float) * close,
        },
        index=idx,
    )
    df.index.name = "begin"
    return df


def test_early_exit_skips_trader_llm(mocker) -> None:
    mocker.patch("agent.graph.market_data.get_candles", return_value=_synthetic_candles())
    mocker.patch("agent.graph.prefilter.settings.AGENT_PREFILTER_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_EARLY_EXIT_ENABLED", True)
    mocker.patch("agent.config.settings.AGENT_EARLY_EXIT_MAX_CONFIDENCE", 0.35)
    mocker.patch("agent.config.settings.AGENT_NEWS_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_DEBATE_ENABLED", False)

    flat_analyst = AnalystOutput(
        trend="flat",
        momentum="flat",
        volatility="normal",
        summary="No clear edge.",
        confidence=0.2,
    )

    def fake_complete_json(system, user, schema, temperature=0.3):
        if schema is AnalystOutput:
            return flat_analyst
        raise AssertionError(f"unexpected LLM call for {schema}")

    mocker.patch("agent.llm.client.LLMClient.complete_json", side_effect=fake_complete_json)

    graph = build_graph(debate_enabled=False, news_enabled=False)
    out = graph.invoke({"symbol": "SBER", "current_position": 0})

    assert out["decision"].signal == "HOLD"
    assert out.get("graph_path") == "early_exit_hold"


def test_prefilter_hold_skips_all_llm(mocker) -> None:
    mocker.patch("agent.graph.market_data.get_candles", return_value=_synthetic_candles())
    mocker.patch("agent.graph.prefilter.settings.AGENT_PREFILTER_ENABLED", True)
    mocker.patch("agent.config.settings.AGENT_EARLY_EXIT_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_NEWS_ENABLED", False)
    mocker.patch("agent.config.settings.AGENT_DEBATE_ENABLED", False)
    mocker.patch(
        "agent.graph.prefilter.should_skip_prefilter",
        return_value=(True, "flat_no_signal"),
    )

    calls: list = []

    def track_complete(*args, **kwargs):
        calls.append(kwargs.get("schema") or args[2])
        raise AssertionError("LLM should not be called")

    mocker.patch("agent.llm.client.LLMClient.complete_json", side_effect=track_complete)

    graph = build_graph(debate_enabled=False, news_enabled=False)
    out = graph.invoke({"symbol": "SBER", "current_position": 0})

    assert out["decision"].signal == "HOLD"
    assert out.get("graph_path") == "prefilter_hold"
    assert calls == []
