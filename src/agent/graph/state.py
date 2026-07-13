from __future__ import annotations

from typing import NotRequired, TypedDict

from agent.schemas import AnalystOutput, Decision, MarketSnapshot, NewsAnalystOutput


class GraphState(TypedDict):
    symbol: str
    interval: NotRequired[int]
    current_position: NotRequired[int]
    commission_rate: NotRequired[float]
    portfolio_context: NotRequired[dict]
    market_context: NotRequired[dict]
    snapshot: NotRequired[MarketSnapshot]
    analyst: NotRequired[AnalystOutput]
    news: NotRequired[NewsAnalystOutput]
    debate_arguments: NotRequired[list[dict]]
    decision: NotRequired[Decision]
    prefilter_passed: NotRequired[bool]
    graph_path: NotRequired[str]
    reflection_written: NotRequired[bool]
    reflection_record: NotRequired[dict]
