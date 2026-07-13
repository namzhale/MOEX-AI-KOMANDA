"""Получение размеров лотов с MOEX ISS.

Захардкоженная таблица в `universe.LOT_SIZE_BY_TICKER` устаревает —
например, GMKN был 1, а сейчас 10 (мы поймали это по логам ArenaGo:
qty=10 → order_value=12 900 = 10 × 10 × 129). Поэтому при старте
scheduler'а тянем актуальные lot_size'ы из ISS — публичный endpoint,
без ключа.

Fallback: захардкоженная таблица если ISS недоступен.
"""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()

# ISS endpoint для всех бумаг доски TQBR одним запросом.
ISS_TQBR_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
)


def fetch_lot_sizes(tickers: tuple[str, ...], timeout: float = 10.0) -> dict[str, int]:
    """Тянет {secid: lot_size} для тикеров из MOEX ISS.

    Возвращает то что удалось получить. Тикеры без data в ответе ISS
    отсутствуют в результате — caller должен использовать fallback из
    `LOT_SIZE_BY_TICKER`.
    """
    if not tickers:
        return {}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(ISS_TQBR_URL, params={"iss.meta": "off"})
            r.raise_for_status()
            payload = r.json()
    except Exception as e:
        log.warning("moex_lots.fetch_failed", error=str(e)[:200])
        return {}

    sec_block = payload.get("securities") or {}
    columns: list[str] = sec_block.get("columns") or []
    data: list[list] = sec_block.get("data") or []
    try:
        secid_idx = columns.index("SECID")
        lot_idx = columns.index("LOTSIZE")
    except ValueError:
        log.warning("moex_lots.unexpected_schema", columns=columns[:20])
        return {}

    requested = {t.upper() for t in tickers}
    out: dict[str, int] = {}
    for row in data:
        try:
            secid = str(row[secid_idx]).upper()
            if secid not in requested:
                continue
            lot = int(row[lot_idx])
            if lot > 0:
                out[secid] = lot
        except (IndexError, TypeError, ValueError):
            continue

    log.info(
        "moex_lots.fetched",
        requested=len(requested),
        resolved=len(out),
        sample={k: out[k] for k in list(out)[:5]},
    )
    return out
