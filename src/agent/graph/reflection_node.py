from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from agent.config import settings
from agent.llm.client import LLMClient
from agent.data.microstructure import flow_context_for_reflection
from agent.runtime.reflection import ReflectionJournal, _persist_reflection
from agent.schemas import Decision, ReflectionRecord

log = structlog.get_logger()

HYPOTHESIS_SYSTEM = """\
You record a pre-trade hypothesis for MOEX execution review.
Given the trader's intended action (not yet executed), write ONE sentence:
what must happen for this trade to be considered successful, and what would invalidate it.
Return JSON only.
"""


class TradeHypothesis(BaseModel):
    hypothesis: str = Field(description="One sentence success/invalidation criteria")
    tags: list[str] = Field(default_factory=list)
    importance: float = Field(ge=0, le=1, default=0.4)


def reflection_post_decision_node(state: dict, llm: LLMClient) -> dict:
    """Post-trader reflection: short-term hypothesis before scheduler executes."""
    decision: Decision | None = state.get("decision")
    if decision is None:
        return {}

    symbol = decision.symbol
    if not settings.REFLECTION_ENABLED or not settings.REFLECTION_IN_GRAPH:
        return {"reflection_written": False}

    if decision.signal == "HOLD" or decision.size_pct <= 0:
        log.info("node.reflection.skip", symbol=symbol, reason="hold")
        return {"reflection_written": False}

    user = (
        f"Ticker: {symbol}\n"
        f"Intended signal: {decision.signal}, size_pct={decision.size_pct:.3f}, "
        f"confidence={decision.confidence:.2f}\n"
        f"Reasoning: {decision.reasoning[:400]}\n"
        f"Analyst: trend={decision.analyst_output.trend}, "
        f"confidence={decision.analyst_output.confidence:.2f}\n"
    )
    flow_ctx = flow_context_for_reflection(symbol)
    if flow_ctx:
        user += f"{flow_ctx}\n"

    hypothesis = f"Planned {decision.signal} on {symbol} at {decision.confidence:.0%} confidence."
    tags = [decision.signal.lower(), decision.analyst_output.trend, "hypothesis"]
    importance = 0.4

    try:
        parsed = llm.complete_json(HYPOTHESIS_SYSTEM, user, TradeHypothesis, temperature=0.2)
        hypothesis = parsed.hypothesis.strip() or hypothesis
        tags = [t.strip() for t in parsed.tags if t.strip()][:6] or tags
        importance = float(parsed.importance)
    except Exception as e:
        log.warning("node.reflection.llm_failed", symbol=symbol, error=str(e)[:200])

    record = ReflectionRecord(
        symbol=symbol,
        trade_id=f"hyp-{symbol}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}",
        lesson=hypothesis,
        tags=tags,
        importance=importance,
        outcome=None,
        sector=None,
        source="hypothesis",
        timestamp=datetime.now(UTC),
    )
    _persist_reflection(record, ReflectionJournal())
    log.info("node.reflection.ok", symbol=symbol, source="hypothesis")
    return {"reflection_written": True, "reflection_record": record.model_dump(mode="json")}
