from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from agent.config import settings
from agent.data.news import NewsAggregator
from agent.graph.debate import bull_bear_debate_node
from agent.graph.news import news_analyst_node
from agent.graph.nodes import hold_finalize_node, market_analyst_node, trader_node
from agent.graph.reflection_node import reflection_post_decision_node
from agent.graph.prefilter import prefilter_node
from agent.graph.routing import (
    make_route_after_analyst,
    make_route_after_news,
    route_after_prefilter,
)
from agent.graph.state import GraphState
from agent.llm.client import LLMClient


_ROLES = ("analyst", "news", "debate", "trader")


def _build_llm_pool(default_llm: LLMClient | None) -> dict[str, LLMClient]:
    """Один LLMClient на уникальную модель — экономим httpx-pool'ы.

    Если caller передал default_llm (например, мок в тестах) — используем его
    для всех ролей и не лезем в settings.
    """
    if default_llm is not None:
        return {role: default_llm for role in _ROLES}

    pool: dict[str, LLMClient] = {}
    out: dict[str, LLMClient] = {}
    for role in _ROLES:
        model = settings.model_for(role)
        if model not in pool:
            pool[model] = LLMClient(model=model, role=role)
        out[role] = pool[model]
    return out


def build_graph(
    llm: LLMClient | None = None,
    llms: dict[str, LLMClient] | None = None,
    debate_enabled: bool | None = None,
    news_enabled: bool | None = None,
    news_aggregator: NewsAggregator | None = None,
):
    if debate_enabled is None:
        debate_enabled = settings.AGENT_DEBATE_ENABLED
    if news_enabled is None:
        news_enabled = settings.AGENT_NEWS_ENABLED

    llms = llms or _build_llm_pool(llm)

    g = StateGraph(GraphState)
    g.add_node("prefilter", prefilter_node)
    g.add_node("market_analyst", partial(market_analyst_node, llm=llms["analyst"]))
    g.add_node("hold_finalize", hold_finalize_node)
    if news_enabled:
        aggregator = news_aggregator or NewsAggregator()
        g.add_node(
            "news_analyst",
            partial(news_analyst_node, llm=llms["news"], aggregator=aggregator),
        )
    if debate_enabled:
        g.add_node("bull_bear_debate", partial(bull_bear_debate_node, llm=llms["debate"]))
    g.add_node("trader", partial(trader_node, llm=llms["trader"]))
    if settings.REFLECTION_ENABLED and settings.REFLECTION_IN_GRAPH:
        g.add_node(
            "reflection_post_decision",
            partial(reflection_post_decision_node, llm=llms["analyst"]),
        )

    g.add_edge(START, "prefilter")
    g.add_conditional_edges("prefilter", route_after_prefilter)
    g.add_conditional_edges(
        "market_analyst",
        make_route_after_analyst(news_enabled=news_enabled, debate_enabled=debate_enabled),
    )

    if news_enabled:
        g.add_conditional_edges(
            "news_analyst",
            make_route_after_news(debate_enabled=debate_enabled),
        )

    if debate_enabled:
        g.add_edge("bull_bear_debate", "trader")

    g.add_edge("hold_finalize", END)
    if settings.REFLECTION_ENABLED and settings.REFLECTION_IN_GRAPH:
        g.add_edge("trader", "reflection_post_decision")
        g.add_edge("reflection_post_decision", END)
    else:
        g.add_edge("trader", END)
    return g.compile()
