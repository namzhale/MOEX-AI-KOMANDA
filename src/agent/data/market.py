from __future__ import annotations

import pandas as pd
import structlog

from agent.config import settings
from agent.data import moex as moex_mod

log = structlog.get_logger()

_algopack_client = None


def _get_algopack_client():
    """Лениво создаём AlgopackClient, чтобы при MARKET_DATA_SOURCE=iss не было
    нужды в токене даже для импорта модуля."""
    global _algopack_client
    if _algopack_client is None:
        from agent.data.algopack import AlgopackClient
        _algopack_client = AlgopackClient()
    return _algopack_client


def get_candles(
    symbol: str,
    interval: int = 60,
    days: int = 30,
    board: str = "TQBR",
) -> pd.DataFrame:
    """Единая точка входа для свечей. Выбор источника — через MARKET_DATA_SOURCE.

    При отказе ALGOPACK (отсутствует токен, сеть, эндпоинт) — мягкий fallback
    на публичный ISS, чтобы pipeline не вставал.
    """
    source = (settings.MARKET_DATA_SOURCE or "iss").lower()

    if source == "algopack":
        try:
            return _get_algopack_client().get_candles(
                symbol=symbol, interval=interval, days=days, board=board
            )
        except Exception as e:
            log.warning(
                "market.algopack_fallback_to_iss",
                symbol=symbol,
                error=str(e)[:200],
            )

    return moex_mod.get_candles(
        symbol=symbol, interval=interval, days=days, board=board
    )
