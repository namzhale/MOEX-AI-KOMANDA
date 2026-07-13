from __future__ import annotations

from agent.config import settings
from agent.data.market import get_candles
from agent.data.microstructure import flow_enabled, load_flow_features
from agent.features.indicators import compute_features
from agent.schemas import Candle, MarketSnapshot

DEFAULT_INTERVAL = 60


def load_market_snapshot(
    symbol: str,
    interval: int | None = None,
    days: int | None = None,
) -> MarketSnapshot:
    """Загружает свечи MOEX и считает индикаторы — общий шаг для prefilter и analyst."""
    interval = interval or DEFAULT_INTERVAL
    days = days if days is not None else settings.AGENT_CANDLE_DAYS
    df = get_candles(symbol, interval=interval, days=days)
    features = compute_features(df)
    if flow_enabled():
        features.update(load_flow_features(symbol))
    tail = df.reset_index().tail(50).to_dict("records")
    candles = [Candle(**row) for row in tail]
    return MarketSnapshot(
        symbol=symbol,
        interval=interval,
        candles=candles,
        features=features,
    )
