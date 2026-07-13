from __future__ import annotations

import math
from collections.abc import Iterable


def quantity_for_buy(
    cash: float,
    size_pct: float,
    last_price: float,
    lot_size: int = 1,
) -> int:
    """Сколько ЛОТОВ можно купить под size_pct от cash.

    ArenaGo API: `quantity` в submit_order — ЛОТЫ (доказано по логам:
    quantity=10 GMKN @ 129 → order_value=12 900 = 10 × 10 × 129, т.е.
    ArenaGo внутри умножает на lot_size). Документация неточна.

    Возвращаем число лотов: floor(cash × size_pct / (price × lot_size)).
    """
    if cash <= 0 or last_price <= 0 or size_pct <= 0 or lot_size <= 0:
        return 0
    raw_lots = (cash * size_pct) / (last_price * lot_size)
    return max(int(math.floor(raw_lots)), 0)


def position_for(positions: Iterable[dict], secid: str) -> int:
    for p in positions:
        if p.get("secid") == secid:
            return int(p.get("position", 0))
    return 0
