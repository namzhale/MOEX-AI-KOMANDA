from __future__ import annotations

import random
from typing import Mapping


def select_llm_tickers(
    universe: tuple[str, ...],
    positions: list[dict],
    *,
    max_per_tick: int,
    liquidity_scores: Mapping[str, float] | None = None,
    alert_symbols: set[str] | None = None,
) -> tuple[str, ...]:
    """Тикеры для Phase 1 LLM. Позиции всегда в приоритете."""
    if max_per_tick <= 0 or max_per_tick >= len(universe):
        return universe

    held: list[str] = []
    seen: set[str] = set()
    for p in positions:
        secid = (p.get("secid") or "").strip().upper()
        if not secid or secid in seen:
            continue
        try:
            qty = float(p.get("position") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty == 0:
            continue
        if secid in universe:
            held.append(secid)
            seen.add(secid)

    rest = [t for t in universe if t not in seen]
    alerts = alert_symbols or set()
    if alerts:
        rest = [t for t in rest if t not in alerts]

    if liquidity_scores:
        rest.sort(
            key=lambda t: float(liquidity_scores.get(t, 0.0) or 0.0),
            reverse=True,
        )
    else:
        random.shuffle(rest)

    slots = max(max_per_tick - len(held), 0)
    return tuple(held) + tuple(rest[:slots])
