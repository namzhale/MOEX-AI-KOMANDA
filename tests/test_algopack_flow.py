"""Algopack Super Candles + microstructure (моки HTTP)."""

from __future__ import annotations

import pytest

from agent.config import settings
from agent.data.algopack import AlgopackClient, _parse_iss_table, _row_for_symbol
from agent.data import microstructure as ms_mod
from agent.graph.prefilter import should_skip_prefilter


def test_parse_iss_table_lowercase_columns() -> None:
    payload = {
        "tradestats": {
            "columns": ["SECID", "DISB", "VAL_B"],
            "data": [["SBER", 0.42, 1_000_000.0]],
        }
    }
    rows = _parse_iss_table(payload, "tradestats")
    assert rows[0]["secid"] == "SBER"
    assert rows[0]["disb"] == 0.42


def test_row_for_symbol_filters() -> None:
    rows = [{"secid": "GAZP", "disb": 0.1}, {"secid": "SBER", "disb": 0.5}]
    assert _row_for_symbol(rows, "SBER")["disb"] == 0.5


def test_algopack_datashop_tradestats(monkeypatch) -> None:
    payload = {
        "tradestats": {
            "columns": ["secid", "disb", "pr_change", "val_b", "val_s"],
            "data": [["SBER", 0.55, 0.02, 500.0, 400.0]],
        }
    }
    seen_paths: list[str] = []

    def fake_get(path, params):
        seen_paths.append(path)
        return payload

    c = AlgopackClient(token="x", base_url="https://apim.moex.com/iss")
    monkeypatch.setattr(c, "_http_get", fake_get)
    row = c.get_tradestats_latest("SBER")
    assert row["disb"] == 0.55
    assert seen_paths[0] == "/datashop/algopack/eq/tradestats/SBER.json"
    assert "/iss/iss/" not in seen_paths[0]


def test_load_flow_features_merges_blocks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MARKET_DATA_SOURCE", "algopack")
    monkeypatch.setattr(settings, "ALGOPACK_TOKEN", "tok")
    monkeypatch.setattr(settings, "ALGOPACK_FLOW_ENABLED", True)

    class FakeClient:
        def get_tradestats_latest(self, symbol):
            return {"disb": 0.3, "pr_change": 0.01, "val_b": 100, "val_s": 50}

        def get_obstats_latest(self, symbol):
            return {"spread_1mio": 25.0, "spread_bbo": 0.6, "imbalance_vol_bbo": -1000.0}

        def get_orderstats_latest(self, symbol):
            return {"put_orders_b": 10}

    from agent.data import market as market_mod

    monkeypatch.setattr(market_mod, "_get_algopack_client", lambda: FakeClient())

    feats = ms_mod.load_flow_features("SBER")
    assert feats["disb"] == 0.3
    assert feats["ob_spread_1mio_bps"] == 25.0
    assert abs(feats["ob_spread_1mio"] - 0.0025) < 1e-9
    assert feats["flow_val_total"] == 150.0


def test_should_skip_prefilter_flow_disb_active() -> None:
    features = {
        "rsi14": 50.0,
        "macd_hist": 0.01,
        "ema20": 100.0,
        "ema50": 100.1,
        "close": 100.0,
        "disb": 0.4,
    }
    skip, reason = should_skip_prefilter(
        features,
        0,
        rsi_low=42,
        rsi_high=58,
        macd_hist_abs_max=0.15,
        ema_spread_pct_max=0.008,
        disb_abs_max=0.15,
    )
    assert skip is False
    assert "flow_disb_active" in reason


def test_should_skip_prefilter_illiquid_spread() -> None:
    features = {
        "rsi14": 50.0,
        "macd_hist": 0.01,
        "ema20": 100.0,
        "ema50": 100.1,
        "close": 100.0,
        "ob_spread_1mio_bps": 55.0,
    }
    skip, reason = should_skip_prefilter(
        features,
        0,
        rsi_low=42,
        rsi_high=58,
        macd_hist_abs_max=0.15,
        ema_spread_pct_max=0.008,
        spread_1mio_max_bps=50.0,
    )
    assert skip is True
    assert "illiquid_spread" in reason


def test_select_llm_tickers_liquidity_rank() -> None:
    from agent.runtime.universe_select import select_llm_tickers

    picked = select_llm_tickers(
        ("SBER", "GAZP", "LKOH"),
        [],
        max_per_tick=2,
        liquidity_scores={"SBER": 100, "GAZP": 500, "LKOH": 50},
    )
    assert picked[0] == "GAZP"


def test_algopack_get_candles_paginates(monkeypatch) -> None:
    """get_candles идёт по страницам (≤500/запрос) до конца окна, а не берёт
    только старейшие 500 строк (баг 10-мин × 30д → протухший last_price)."""
    c = AlgopackClient(token="x", base_url="https://apim.moex.com/iss")
    cols = ["begin", "end", "open", "high", "low", "close", "volume", "value"]

    def row(i: int) -> list:
        return [f"2026-05-20 10:{i % 60:02d}:00", f"2026-05-20 10:{i % 60:02d}:59",
                1.0, 1.0, 1.0, 1.0, 1.0, 1.0]

    pages = [
        {"candles": {"columns": cols, "data": [row(j) for j in range(500)]}},
        {"candles": {"columns": cols, "data": [row(j) for j in range(100)]}},
    ]
    seen = {"i": 0, "offsets": []}

    def fake_get(path, params):
        seen["offsets"].append(params["start"])
        p = pages[min(seen["i"], len(pages) - 1)]
        seen["i"] += 1
        return p

    monkeypatch.setattr(c, "_http_get", fake_get)
    df = c.get_candles("SBER", interval=10, days=30)
    assert len(df) == 600  # обе страницы данных, не только первые 500
    # offset идёт 0 → 500; на 2-й странице (100 < 500) цикл завершается.
    assert seen["offsets"] == [0, 500]
