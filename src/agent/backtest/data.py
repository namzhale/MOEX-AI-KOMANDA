"""Загрузка исторических OHLCV для бэктеста с кэшем на диск.

Источник — MOEX ISS (публичный, через agent.data.moex). Кэшируем в parquet,
чтобы повторные прогоны не дёргали биржу.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import structlog

from agent.data import moex as moex_mod
from agent.runtime.universe import DEFAULT_UNIVERSE

log = structlog.get_logger()

DEFAULT_CACHE_DIR = Path("data/backtest_cache")
_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


def load_history(
    tickers: tuple[str, ...] = DEFAULT_UNIVERSE,
    interval: int = 24,
    days: int = 365,
    board: str = "TQBR",
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
    polite_sleep: float = 0.3,
) -> dict[str, pd.DataFrame]:
    """Возвращает {ticker: DataFrame[open/high/low/close/volume]} с DatetimeIndex.

    interval=24 — дневные свечи (быстро, для длинных прогонов), 60 — часовые.
    Кэш-ключ учитывает interval/days/board, так что разные конфигурации не
    перетирают друг друга.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        cache_file = cache_dir / f"{ticker}_{board}_{interval}_{days}.parquet"
        if use_cache and cache_file.exists():
            try:
                out[ticker] = pd.read_parquet(cache_file)
                continue
            except Exception:
                log.warning("backtest.cache.read_failed", file=str(cache_file))

        try:
            df = moex_mod.get_candles(
                symbol=ticker, interval=interval, days=days, board=board
            )
        except Exception as e:
            log.warning("backtest.history.fetch_failed", ticker=ticker, error=str(e)[:200])
            continue

        cols = [c for c in _OHLCV_COLS if c in df.columns]
        df = df[cols].copy()
        df = df[~df.index.duplicated(keep="last")].sort_index()
        out[ticker] = df
        try:
            df.to_parquet(cache_file)
        except Exception:
            log.warning("backtest.cache.write_failed", file=str(cache_file))
        if polite_sleep:
            time.sleep(polite_sleep)

    log.info(
        "backtest.history.loaded",
        tickers=len(out),
        interval=interval,
        days=days,
        rows={t: len(df) for t, df in list(out.items())[:5]},
    )
    return out


def aligned_calendar(prices: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Объединённый отсортированный календарь по всем тикерам (union).

    Union, а не intersection: тикеры могут иметь редкие пропуски (праздники,
    остановки торгов). На отсутствующем баре тикера стратегия его пропустит.
    """
    if not prices:
        return pd.DatetimeIndex([])
    idx = None
    for df in prices.values():
        idx = df.index if idx is None else idx.union(df.index)
    return idx.sort_values()
