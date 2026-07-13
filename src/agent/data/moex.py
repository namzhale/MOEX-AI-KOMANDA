from __future__ import annotations

from datetime import UTC, datetime, timedelta

import apimoex
import pandas as pd
import requests
import structlog

log = structlog.get_logger()


# MOEX ISS supports these candle granularities: 1/10/60 min, 24=daily, 7=weekly, 31=monthly.
VALID_INTERVALS = {1, 10, 60, 24, 7, 31}


_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 30.0


class _TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (_CONNECT_TIMEOUT, _READ_TIMEOUT))
        return super().request(method, url, **kwargs)


def get_candles(
    symbol: str,
    interval: int = 60,
    days: int = 30,
    board: str = "TQBR",
) -> pd.DataFrame:
    if interval not in VALID_INTERVALS:
        raise ValueError(f"interval must be one of {sorted(VALID_INTERVALS)}, got {interval}")

    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    log.info("moex.candles.fetch", symbol=symbol, interval=interval, start=str(start), end=str(end))

    with _TimeoutSession() as session:
        data = apimoex.get_board_candles(
            session,
            security=symbol,
            interval=interval,
            start=str(start),
            end=str(end),
            board=board,
            market="shares",
            engine="stock",
        )

    df = pd.DataFrame(data)
    if df.empty:
        raise RuntimeError(f"No candles for {symbol} on {board} (interval={interval}, days={days})")

    df["begin"] = pd.to_datetime(df["begin"])
    if "end" in df.columns:
        df["end"] = pd.to_datetime(df["end"])
    df = df.set_index("begin")
    log.info("moex.candles.ok", symbol=symbol, rows=len(df))
    return df


def get_board_securities(board: str = "TQBR") -> pd.DataFrame:
    with _TimeoutSession() as session:
        data = apimoex.get_board_securities(session, board=board)
    df = pd.DataFrame(data)
    log.info("moex.securities.ok", board=board, rows=len(df))
    return df
