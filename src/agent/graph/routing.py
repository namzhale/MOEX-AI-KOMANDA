from __future__ import annotations

from langgraph.graph import END

from agent.config import settings


def route_after_prefilter(state: dict) -> str:
    if state.get("decision") is not None:
        return END
    return "market_analyst"


def _early_exit_eligible(state: dict) -> bool:
    if not settings.AGENT_EARLY_EXIT_ENABLED:
        return False
    if state.get("decision") is not None:
        return False

    try:
        current_position = int(float(state.get("current_position") or 0))
    except (TypeError, ValueError):
        current_position = 0
    if current_position != 0:
        return False

    analyst = state.get("analyst")
    if analyst is None:
        return False

    conf_floor = settings.AGENT_EARLY_EXIT_MAX_CONFIDENCE
    if analyst.confidence > conf_floor:
        return False
    if analyst.trend != "flat":
        return False
    if analyst.momentum not in ("flat", "weak_up", "weak_down"):
        return False

    return True


def make_route_after_analyst(*, news_enabled: bool, debate_enabled: bool):
    """Маршрут после analyst — флаги совпадают с узлами, добавленными в build_graph."""

    def route_after_analyst(state: dict) -> str:
        if _early_exit_eligible(state):
            return "hold_finalize"
        if news_enabled:
            return "news_analyst"
        if debate_enabled and settings.AGENT_DEBATE_ROUNDS > 0:
            return "bull_bear_debate"
        return "trader"

    return route_after_analyst


def make_route_after_news(*, debate_enabled: bool):
    def route_after_news(state: dict) -> str:
        if debate_enabled and settings.AGENT_DEBATE_ROUNDS > 0:
            return "bull_bear_debate"
        return "trader"

    return route_after_news


def route_after_analyst(state: dict) -> str:
    return make_route_after_analyst(
        news_enabled=settings.AGENT_NEWS_ENABLED,
        debate_enabled=settings.AGENT_DEBATE_ENABLED,
    )(state)


def route_after_news(state: dict) -> str:
    return make_route_after_news(debate_enabled=settings.AGENT_DEBATE_ENABLED)(state)
