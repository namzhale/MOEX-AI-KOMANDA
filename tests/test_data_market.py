"""Dispatch source switching: iss vs algopack + graceful fallback."""

from __future__ import annotations

import httpx
import pandas as pd
import pytest

from agent.config import settings
from agent.data import algopack as algopack_mod
from agent.data import market as market_mod
from agent.data import moex as moex_mod
from agent.data.algopack import AlgopackClient


@pytest.fixture(autouse=True)
def _reset_algopack_singleton():
    market_mod._algopack_client = None
    yield
    market_mod._algopack_client = None


def _stub_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [1000.0],
            "value": [100500.0],
        }
    )


def test_dispatch_iss_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MARKET_DATA_SOURCE", "iss")
    called = {"iss": False, "algopack": False}

    def fake_iss(symbol, interval=60, days=30, board="TQBR"):
        called["iss"] = True
        return _stub_df()

    monkeypatch.setattr(moex_mod, "get_candles", fake_iss)
    df = market_mod.get_candles("SBER")
    assert called["iss"] is True
    assert len(df) == 1


def test_dispatch_algopack_when_selected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MARKET_DATA_SOURCE", "algopack")
    monkeypatch.setattr(settings, "ALGOPACK_TOKEN", "test-token")

    def fake_algo(self, symbol, interval=60, days=30, board="TQBR"):
        return _stub_df()

    monkeypatch.setattr(AlgopackClient, "get_candles", fake_algo)
    df = market_mod.get_candles("SBER")
    assert len(df) == 1


def test_dispatch_falls_back_to_iss_on_algopack_failure(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MARKET_DATA_SOURCE", "algopack")
    monkeypatch.setattr(settings, "ALGOPACK_TOKEN", "test-token")

    def boom(self, *a, **kw):
        raise RuntimeError("algopack 401")

    iss_called = {"flag": False}

    def fake_iss(symbol, interval=60, days=30, board="TQBR"):
        iss_called["flag"] = True
        return _stub_df()

    monkeypatch.setattr(AlgopackClient, "get_candles", boom)
    monkeypatch.setattr(moex_mod, "get_candles", fake_iss)

    df = market_mod.get_candles("SBER")
    assert iss_called["flag"] is True
    assert len(df) == 1


def test_algopack_client_sets_bearer_header(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ALGOPACK_TOKEN", "raw-token-abc")
    c = AlgopackClient()
    assert c._client.headers["Authorization"] == "Bearer raw-token-abc"


def test_algopack_client_strips_token_whitespace(monkeypatch) -> None:
    # CRLF на Windows иначе ломает httpx headers
    monkeypatch.setattr(settings, "ALGOPACK_TOKEN", "raw-token-abc\r\n")
    c = AlgopackClient()
    assert c._client.headers["Authorization"] == "Bearer raw-token-abc"


def test_algopack_rejects_bad_interval() -> None:
    c = AlgopackClient(token="x", base_url="http://example.invalid")
    with pytest.raises(ValueError, match="interval"):
        c.get_candles("SBER", interval=42, days=5)


def test_algopack_parses_iss_json(monkeypatch) -> None:
    payload = {
        "candles": {
            "columns": ["OPEN", "CLOSE", "HIGH", "LOW", "VOLUME", "VALUE", "BEGIN", "END"],
            "data": [
                [100.0, 101.0, 102.0, 99.0, 1000.0, 100500.0,
                 "2026-05-17 10:00:00", "2026-05-17 11:00:00"],
            ],
        }
    }
    c = AlgopackClient(token="x", base_url="http://example.invalid")
    monkeypatch.setattr(c, "_http_get", lambda path, params: payload)
    df = c.get_candles("SBER", interval=60, days=1)
    assert len(df) == 1
    assert df.index.name == "begin"
    assert df.iloc[0]["close"] == 101.0
