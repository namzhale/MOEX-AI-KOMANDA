"""Юнит-тесты для MOEX-клиента — apimoex замокан, сеть не нужна."""

from __future__ import annotations

import pytest

from agent.data import moex


_FAKE_CANDLES = [
    {
        "begin": "2026-05-12 10:00:00",
        "end": "2026-05-12 11:00:00",
        "open": 100.0,
        "high": 101.5,
        "low": 99.5,
        "close": 100.8,
        "volume": 12345.0,
        "value": 1234567.0,
    },
    {
        "begin": "2026-05-12 11:00:00",
        "end": "2026-05-12 12:00:00",
        "open": 100.8,
        "high": 102.0,
        "low": 100.5,
        "close": 101.7,
        "volume": 23456.0,
        "value": 2345678.0,
    },
]


def test_get_candles_returns_dataframe(mocker) -> None:
    mocker.patch.object(moex.apimoex, "get_board_candles", return_value=_FAKE_CANDLES)
    df = moex.get_candles("SBER", interval=60, days=5)
    assert len(df) == 2
    assert {"open", "high", "low", "close", "volume", "value"}.issubset(df.columns)
    assert df.index.name == "begin"


def test_get_candles_raises_on_empty(mocker) -> None:
    mocker.patch.object(moex.apimoex, "get_board_candles", return_value=[])
    with pytest.raises(RuntimeError, match="No candles"):
        moex.get_candles("ZZZZ", interval=60, days=5)


def test_get_candles_rejects_bad_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        moex.get_candles("SBER", interval=42, days=5)
