from __future__ import annotations

from agent.graph.prefilter import should_skip_prefilter


def test_should_skip_flat_market_no_position() -> None:
    features = {
        "rsi14": 50.0,
        "macd_hist": 0.02,
        "ema20": 100.0,
        "ema50": 100.2,
        "close": 100.0,
    }
    skip, reason = should_skip_prefilter(
        features,
        0,
        rsi_low=42.0,
        rsi_high=58.0,
        macd_hist_abs_max=0.15,
        ema_spread_pct_max=0.008,
    )
    assert skip is True
    assert reason == "flat_no_signal"


def test_should_not_skip_with_open_position() -> None:
    features = {"rsi14": 50.0, "macd_hist": 0.0, "ema20": 100.0, "ema50": 100.0, "close": 100.0}
    skip, reason = should_skip_prefilter(
        features, 10, rsi_low=42.0, rsi_high=58.0, macd_hist_abs_max=0.15, ema_spread_pct_max=0.008
    )
    assert skip is False
    assert reason == "position_open"


def test_should_not_skip_strong_macd() -> None:
    features = {
        "rsi14": 50.0,
        "macd_hist": 0.5,
        "ema20": 100.0,
        "ema50": 100.0,
        "close": 100.0,
    }
    skip, _ = should_skip_prefilter(
        features, 0, rsi_low=42.0, rsi_high=58.0, macd_hist_abs_max=0.15, ema_spread_pct_max=0.008
    )
    assert skip is False
