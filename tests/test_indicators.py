"""Юнит-тест индикаторов на синтетических свечах."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agent.features.indicators import compute_features


def _synthetic_candles(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    # Random-walk close + базовый OHLCV
    rets = rng.normal(0, 0.005, n)
    close = 100 * (1 + rets).cumprod()
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    volume = rng.integers(10_000, 100_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_compute_features_keys_present() -> None:
    df = _synthetic_candles()
    feats = compute_features(df)
    for key in ("rsi14", "atr14", "ema20", "ema50", "close"):
        assert key in feats, f"missing {key}"
        assert isinstance(feats[key], float)


def test_compute_features_short_history_rejected() -> None:
    df = _synthetic_candles(n=10)
    with pytest.raises(ValueError, match="need at least"):
        compute_features(df)


def test_compute_features_missing_columns_rejected() -> None:
    df = _synthetic_candles().drop(columns=["volume"])
    with pytest.raises(ValueError, match="missing required columns"):
        compute_features(df)


def test_features_are_json_serializable() -> None:
    import json

    df = _synthetic_candles()
    feats = compute_features(df)
    # Не должно бросать TypeError на numpy-типах
    json.dumps(feats)
