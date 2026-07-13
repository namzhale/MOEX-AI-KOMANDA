from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from agent.data.news import NewsItem
from agent.graph import debate as debate_mod
from agent.graph import news as news_mod
from agent.graph import nodes as nodes_mod
from agent.schemas import (
    AnalystOutput,
    BearArgument,
    BullArgument,
    Candle,
    MarketSnapshot,
    NewsAnalystOutput,
    TraderDecision,
)


class _CaptureLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))

    def debug(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))


class _LLM:
    def __init__(self, responses: dict[type, object]) -> None:
        self.responses = responses

    def complete_json(self, _system, _user, schema, temperature: float = 0.3):
        return self.responses[schema]


def test_analyst_response_is_logged_at_agent_level(monkeypatch) -> None:
    capture = _CaptureLog()
    analyst = AnalystOutput(
        trend="up",
        momentum="weak_up",
        volatility="normal",
        summary="EMA20 > EMA50",
        confidence=0.7,
    )
    candle = {
        "begin": datetime.now(UTC),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000.0,
        "value": 100500.0,
    }
    monkeypatch.setattr(nodes_mod, "log", capture)
    monkeypatch.setattr(
        nodes_mod,
        "load_market_snapshot",
        lambda *a, **k: MarketSnapshot(
            symbol="SBER",
            interval=60,
            candles=[Candle(**candle)],
            features={"rsi14": 55.0},
        ),
    )
    monkeypatch.setattr(nodes_mod, "format_memory_block", lambda *_a, **_k: "")

    nodes_mod.market_analyst_node({"symbol": "SBER"}, _LLM({AnalystOutput: analyst}))

    assert ("agent.analyst.response", {
        "symbol": "SBER",
        "role": "analyst",
        "schema": "AnalystOutput",
        "output": analyst.model_dump(),
    }) in capture.events


def test_news_response_is_logged_at_agent_level(monkeypatch) -> None:
    capture = _CaptureLog()
    output = NewsAnalystOutput(
        sentiment="bullish",
        key_events=["dividend"],
        citations=["https://example.com/news"],
        confidence=0.8,
        raw_news_count=1,
    )

    class _Aggregator:
        def fetch_for_ticker(self, _symbol: str) -> list[NewsItem]:
            return [
                NewsItem(
                    id="n1",
                    source="tass",
                    published_at=datetime.now(UTC),
                    tickers=["SBER"],
                    type="general",
                    title="Sber news",
                    body="Body",
                    url="https://example.com/news",
                    language="ru",
                )
            ]

    monkeypatch.setattr(news_mod, "log", capture)

    news_mod.news_analyst_node(
        {"symbol": "SBER"},
        _LLM({NewsAnalystOutput: output}),
        _Aggregator(),
    )

    assert ("agent.news.response", {
        "symbol": "SBER",
        "role": "news",
        "schema": "NewsAnalystOutput",
        "llm_called": True,
        "output": output.model_dump(),
    }) in capture.events


def test_debate_responses_are_logged_at_agent_level(monkeypatch) -> None:
    capture = _CaptureLog()
    bull = BullArgument(
        thesis="Bull case",
        key_points=["uptrend"],
        confidence=0.7,
        rebuttal=None,
    )
    bear = BearArgument(
        thesis="Bear case",
        key_points=["overbought"],
        confidence=0.4,
        rebuttal=None,
    )
    analyst = AnalystOutput(
        trend="up",
        momentum="weak_up",
        volatility="normal",
        summary="EMA20 > EMA50",
        confidence=0.7,
    )
    monkeypatch.setattr(debate_mod.settings, "AGENT_DEBATE_ROUNDS", 1)
    monkeypatch.setattr(debate_mod, "log", capture)

    debate_mod.bull_bear_debate_node(
        {"symbol": "SBER", "analyst": analyst},
        _LLM({BullArgument: bull, BearArgument: bear}),
    )

    assert ("agent.bull.response", {
        "symbol": "SBER",
        "role": "bull",
        "schema": "BullArgument",
        "round_idx": 0,
        "output": bull.model_dump(),
    }) in capture.events
    assert ("agent.bear.response", {
        "symbol": "SBER",
        "role": "bear",
        "schema": "BearArgument",
        "round_idx": 0,
        "output": bear.model_dump(),
    }) in capture.events


def test_trader_response_is_logged_at_agent_level(monkeypatch) -> None:
    capture = _CaptureLog()
    analyst = AnalystOutput(
        trend="up",
        momentum="weak_up",
        volatility="normal",
        summary="EMA20 > EMA50",
        confidence=0.7,
    )
    trader = TraderDecision(
        signal="BUY",
        size_pct=0.08,
        confidence=0.65,
        reasoning="Bull case wins.",
    )
    monkeypatch.setattr(nodes_mod, "log", capture)

    nodes_mod.trader_node(
        {"symbol": "SBER", "analyst": analyst},
        _LLM({TraderDecision: trader}),
    )

    assert ("agent.trader.response", {
        "symbol": "SBER",
        "role": "trader",
        "schema": "TraderDecision",
        "output": trader.model_dump(),
    }) in capture.events
