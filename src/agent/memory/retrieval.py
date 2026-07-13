from __future__ import annotations

from datetime import UTC, datetime

import structlog

from agent.config import settings
from agent.memory.qdrant_store import get_memory_store
from agent.runtime.journal import JsonlJournal
from agent.runtime.universe import SECTOR_MAP

log = structlog.get_logger()

_PORTFOLIO_SYMBOL = "_PORTFOLIO_"


def _today_prefix() -> str:
    return datetime.now(UTC).astimezone().date().isoformat()


def _working_memory_lines(symbol: str, *, max_items: int = 3) -> list[str]:
    """Working memory: сегодняшние решения по тикеру из decisions.jsonl."""
    path = f"{settings.DATA_DIR.rstrip('/')}/decisions.jsonl"
    journal = JsonlJournal(path)
    prefix = _today_prefix()
    lines: list[str] = []
    for row in reversed(journal.tail(400)):
        if not str(row.get("ts", "")).startswith(prefix):
            continue
        decisions = row.get("decisions") or {}
        if not isinstance(decisions, dict):
            continue
        summary = decisions.get(symbol)
        if not isinstance(summary, dict):
            continue
        signal = summary.get("signal", "?")
        reasoning = (summary.get("reasoning") or "")[:120]
        action = summary.get("action") or {}
        status = action.get("status", "none")
        lines.append(f"- {signal} ({status}): {reasoning}")
        if len(lines) >= max_items:
            break
    return list(reversed(lines))


def _journal_lessons(
    journal: ReflectionJournal,
    symbol: str,
    *,
    include_meta: bool,
    max_trade: int,
    max_meta: int,
) -> list[str]:
    lessons: list[str] = []
    for row in journal.recent_for_symbol(symbol, n=max_trade):
        text = str(row.get("lesson") or "").strip()
        if text:
            lessons.append(text)
    if include_meta:
        for row in journal.recent_meta(n=max_meta):
            text = str(row.get("lesson") or "").strip()
            if text:
                lessons.append(f"[portfolio] {text}")
    return lessons


def retrieve_memory_context(
    symbol: str,
    *,
    include_working: bool = True,
    include_meta: bool = True,
    max_long_term: int = 3,
) -> str:
    """FinMem-style контекст: working + long-term (JSONL/Qdrant) + meta rules."""
    blocks: list[str] = []

    if include_working:
        working = _working_memory_lines(symbol)
        if working:
            blocks.append("Today's decisions for this ticker:")
            blocks.extend(working)

    long_term: list[str] = []
    store = get_memory_store()
    if store is not None:
        try:
            long_term = store.search_lessons(symbol, k=max_long_term)
            if include_meta:
                long_term.extend(store.search_lessons(_PORTFOLIO_SYMBOL, k=2))
        except Exception as e:
            log.warning("memory.retrieve.qdrant_failed", symbol=symbol, error=str(e)[:200])

    from agent.runtime.reflection import ReflectionJournal

    journal = ReflectionJournal()
    if not long_term:
        long_term = _journal_lessons(
            journal, symbol, include_meta=include_meta, max_trade=max_long_term, max_meta=2
        )
    elif include_meta:
        long_term.extend(
            _journal_lessons(journal, _PORTFOLIO_SYMBOL, include_meta=False, max_trade=0, max_meta=2)
        )

    seen: set[str] = set()
    deduped: list[str] = []
    for lesson in long_term:
        key = lesson[:80]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(lesson[:240])
        if len(deduped) >= max_long_term + (2 if include_meta else 0):
            break

    if deduped:
        sector = SECTOR_MAP.get(symbol.upper(), "?")
        blocks.append(f"Long-term lessons (sector {sector}):")
        blocks.extend(f"- {line}" for line in deduped)

    return "\n".join(blocks)


def format_memory_block(symbol: str) -> str:
    """Публичный API для узлов графа."""
    if not settings.REFLECTION_ENABLED:
        return ""
    return retrieve_memory_context(symbol)
