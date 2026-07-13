from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from agent.config import settings
from agent.llm.client import LLMClient
from agent.memory.qdrant_store import get_memory_store
from agent.runtime.journal import JsonlJournal
from agent.data.microstructure import flow_context_for_reflection, fetch_mega_alerts_today
from agent.runtime.universe import SECTOR_MAP
from agent.schemas import Decision, ReflectionRecord

log = structlog.get_logger()

REFLECTION_SYSTEM = """\
You are a trading coach reviewing a completed MOEX trade (position closed or reduced).
Write a short lesson (1-3 sentences) for future trades on this ticker.
State what worked or failed relative to the original thesis.
Return JSON only.
"""

META_REFLECTION_SYSTEM = """\
You are a senior risk manager reviewing today's MOEX bot activity.
Identify 2-4 systematic patterns (overtrading, ignoring flat markets, sector concentration, etc.).
Return JSON with a short summary and 2-4 actionable rules.
"""


class ReflectionLesson(BaseModel):
    lesson: str = Field(description="1-3 sentences")
    tags: list[str] = Field(default_factory=list)
    importance: float = Field(ge=0, le=1, default=0.5)
    outcome: str | None = Field(default=None, description="win|loss|flat|unknown")


class MetaReflectionOutput(BaseModel):
    summary: str = Field(description="2-4 sentences on patterns today")
    rules: list[str] = Field(default_factory=list, description="2-4 actionable rules")
    importance: float = Field(ge=0, le=1, default=0.8)


class ReflectionJournal(JsonlJournal):
    def __init__(self, data_dir: str | None = None) -> None:
        base = (data_dir or settings.DATA_DIR).rstrip("/")
        super().__init__(f"{base}/reflections.jsonl")

    def write_reflection(self, record: ReflectionRecord) -> None:
        payload = record.model_dump(mode="json")
        self.write("reflection", **payload)

    def recent_for_symbol(self, symbol: str, n: int = 5) -> list[dict]:
        rows = self.tail(300)
        return [
            r for r in rows
            if r.get("symbol") == symbol and r.get("event") == "reflection"
        ][-n:]

    def recent_meta(self, n: int = 5) -> list[dict]:
        rows = self.tail(300)
        return [
            r for r in rows
            if r.get("event") == "reflection" and r.get("source") == "meta"
        ][-n:]

    def recent_hypotheses(self, symbol: str, n: int = 2) -> list[dict]:
        rows = self.tail(200)
        return [
            r for r in rows
            if r.get("symbol") == symbol
            and r.get("event") == "reflection"
            and r.get("source") == "hypothesis"
        ][-n:]


def format_lessons_block(symbol: str, journal: ReflectionJournal | None = None) -> str:
    """Обратная совместимость → unified memory retrieval."""
    _ = journal
    from agent.memory.retrieval import format_memory_block

    return format_memory_block(symbol)


_CLOSING_OP_TYPES = frozenset({"close_long", "cover_short", "risk_trim_sell", "risk_trim_cover"})


def is_closing_operation(op_type: str | None) -> bool:
    return (op_type or "") in _CLOSING_OP_TYPES


def _persist_reflection(record: ReflectionRecord, journal: ReflectionJournal) -> None:
    journal.write_reflection(record)
    store = get_memory_store()
    if store is not None:
        try:
            store.upsert_reflection(record)
        except Exception as e:
            log.warning(
                "reflection.qdrant_upsert_failed",
                symbol=record.symbol,
                error=str(e)[:200],
            )


def _infer_outcome(action_summary: dict) -> str:
    pnl = action_summary.get("estimated_pnl") or action_summary.get("pnl")
    if pnl is not None:
        try:
            pnl_f = float(pnl)
            if pnl_f > 0:
                return "win"
            if pnl_f < 0:
                return "loss"
            return "flat"
        except (TypeError, ValueError):
            pass
    return "unknown"


def reflect_on_trade(
    *,
    symbol: str,
    decision: Decision,
    op_type: str,
    action_summary: dict,
    llm: LLMClient | None = None,
    journal: ReflectionJournal | None = None,
) -> ReflectionRecord | None:
    """Single-level reflection после закрывающей сделки (RESEARCH §2.4)."""
    if not settings.REFLECTION_ENABLED:
        return None
    if not is_closing_operation(op_type):
        return None

    j = journal or ReflectionJournal()
    sector = SECTOR_MAP.get(symbol.upper())
    trade_id = f"{symbol}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
    outcome = _infer_outcome(action_summary)

    hypotheses = j.recent_hypotheses(symbol, n=2)
    hyp_text = ""
    if hypotheses:
        hyp_text = " Prior hypotheses: " + "; ".join(
            str(h.get("lesson", ""))[:120] for h in hypotheses
        )

    user = (
        f"Ticker: {symbol} (sector {sector or '?'})\n"
        f"Closed via: {op_type}, outcome={outcome}\n"
        f"Trader signal was: {decision.signal}, confidence={decision.confidence:.2f}\n"
        f"Trader reasoning: {decision.reasoning[:500]}\n"
        f"Analyst: trend={decision.analyst_output.trend}, "
        f"confidence={decision.analyst_output.confidence:.2f}\n"
        f"Action: status={action_summary.get('status')}, op_type={op_type}\n"
        f"{hyp_text}\n"
    )
    flow_ctx = flow_context_for_reflection(symbol)
    if flow_ctx:
        user += f"{flow_ctx}\n"

    lesson_text = (
        f"Closed {op_type} on {symbol} ({outcome}); prior {decision.signal} "
        f"with analyst {decision.analyst_output.trend}."
    )
    tags = [op_type, decision.analyst_output.trend, outcome]
    importance = 0.6 if outcome in ("win", "loss") else 0.5

    client = llm or LLMClient(model=settings.model_for("analyst"), role="reflection")
    try:
        parsed = client.complete_json(REFLECTION_SYSTEM, user, ReflectionLesson, temperature=0.2)
        lesson_text = parsed.lesson.strip() or lesson_text
        tags = [t.strip() for t in parsed.tags if t.strip()][:8] or tags
        importance = float(parsed.importance)
        if parsed.outcome in ("win", "loss", "flat", "unknown"):
            outcome = parsed.outcome
    except Exception as e:
        log.warning("reflection.llm_failed", symbol=symbol, error=str(e)[:200])

    record = ReflectionRecord(
        symbol=symbol,
        trade_id=trade_id,
        lesson=lesson_text,
        tags=tags,
        importance=importance,
        pnl_hint=str(action_summary.get("estimated_pnl") or "")[:32] or None,
        outcome=outcome,  # type: ignore[arg-type]
        sector=sector,
        source="trade",
        timestamp=datetime.now(UTC),
    )
    _persist_reflection(record, j)
    log.info(
        "reflection.written",
        symbol=symbol,
        trade_id=trade_id,
        source="trade",
        outcome=outcome,
    )
    return record


def _meta_state_path() -> Path:
    return Path(settings.DATA_DIR.rstrip("/")) / "meta_reflection_state.json"


def _meta_already_ran_today() -> bool:
    path = _meta_state_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("date") == datetime.now(UTC).astimezone().date().isoformat()
    except Exception:
        return False


def _mark_meta_ran() -> None:
    path = _meta_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": datetime.now(UTC).astimezone().date().isoformat()}),
        encoding="utf-8",
    )


def should_run_meta_reflection(at: datetime | None = None) -> bool:
    if not settings.META_REFLECTION_ENABLED:
        return False
    if _meta_already_ran_today():
        return False
    from agent.runtime.hours import MSK, is_moex_main_session

    t = (at or datetime.now(UTC)).astimezone(MSK)
    if t.weekday() >= 5:
        return False
    if is_moex_main_session(t):
        return False
    if t.time().hour < 18 or (t.time().hour == 18 and t.time().minute < 40):
        return False
    return True


def run_meta_reflection(
    *,
    trade_journal: JsonlJournal | None = None,
    reflection_journal: ReflectionJournal | None = None,
    llm: LLMClient | None = None,
    force: bool = False,
) -> ReflectionRecord | None:
    """Dual-level meta-reflection (FinAgent-style)."""
    if not settings.META_REFLECTION_ENABLED:
        return None
    if not force and not should_run_meta_reflection():
        return None

    tj = trade_journal or JsonlJournal(
        f"{settings.DATA_DIR.rstrip('/')}/decisions.jsonl"
    )
    rj = reflection_journal or ReflectionJournal()
    today = datetime.now(UTC).astimezone().date().isoformat()
    tick_rows = [
        r for r in tj.tail(500)
        if str(r.get("ts", "")).startswith(today) and str(r.get("event", "")).startswith("tick")
    ]
    trade_reflections = [
        r for r in rj.tail(300)
        if str(r.get("ts", "")).startswith(today) and r.get("source") == "trade"
    ]

    alert_sample: list[dict] = []
    try:
        alert_sample = fetch_mega_alerts_today(max_rows=15)[:8]
    except Exception:
        pass

    user = (
        f"Date: {today}\n"
        f"Tick events today: {len(tick_rows)}\n"
        f"Trade reflections today: {len(trade_reflections)}\n"
        f"Mega Alerts sample: {json.dumps(alert_sample, ensure_ascii=False, default=str)[:1500]}\n"
        f"Sample ticks: {json.dumps(tick_rows[-5:], ensure_ascii=False, default=str)[:3000]}\n"
        f"Sample trade lessons: {json.dumps(trade_reflections[-8:], ensure_ascii=False, default=str)[:3000]}\n"
    )

    client = llm or LLMClient(model=settings.model_for("trader"), role="meta_reflection")
    try:
        parsed = client.complete_json(
            META_REFLECTION_SYSTEM, user, MetaReflectionOutput, temperature=0.2
        )
        lesson = parsed.summary.strip()
        if parsed.rules:
            lesson += " Rules: " + "; ".join(parsed.rules[:4])
        importance = float(parsed.importance)
        tags = ["meta", today]
    except Exception as e:
        log.warning("meta_reflection.llm_failed", error=str(e)[:200])
        lesson = f"Meta review {today}: {len(tick_rows)} ticks, {len(trade_reflections)} trade lessons."
        importance = 0.6
        tags = ["meta", today]

    record = ReflectionRecord(
        symbol="_PORTFOLIO_",
        trade_id=f"meta-{today}-{uuid4().hex[:6]}",
        lesson=lesson[:2000],
        tags=tags,
        importance=importance,
        source="meta",
        timestamp=datetime.now(UTC),
    )
    _persist_reflection(record, rj)
    if not force:
        _mark_meta_ran()
    log.info("meta_reflection.written", date=today, importance=importance)
    return record
