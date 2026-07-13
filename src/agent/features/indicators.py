from __future__ import annotations

import math

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import VolumeWeightedAveragePrice

MIN_BARS = 50


def compute_features(df: pd.DataFrame) -> dict[str, float]:
    if len(df) < MIN_BARS:
        raise ValueError(f"need at least {MIN_BARS} bars for indicators, got {len(df)}")

    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    out = pd.DataFrame(index=df.index)
    out["close"] = close

    out["rsi14"] = RSIIndicator(close=close, window=14).rsi()

    macd = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()
    out["ema20"] = EMAIndicator(close=close, window=20).ema_indicator()
    out["ema50"] = EMAIndicator(close=close, window=50).ema_indicator()

    out["atr14"] = AverageTrueRange(
        high=high, low=low, close=close, window=14
    ).average_true_range()
    bb = BollingerBands(close=close, window=20, window_dev=2)
    out["bb_upper"] = bb.bollinger_hband()
    out["bb_mid"] = bb.bollinger_mavg()
    out["bb_lower"] = bb.bollinger_lband()

    # VWAP может бросить на коротких окнах / некорректных volume — терпим, без него обходимся.
    try:
        out["vwap"] = VolumeWeightedAveragePrice(
            high=high, low=low, close=close, volume=volume, window=14
        ).volume_weighted_average_price()
    except Exception:
        pass

    last = out.iloc[-1].to_dict()
    # Фильтруем NaN/Inf — иначе LLM-prompt JSON ломается на сериализации.
    # NaN бывает при коротких окнах, Inf — при делении на 0 в TA-формулах.
    cleaned: dict[str, float] = {}
    for k, v in last.items():
        if v is None or pd.isna(v):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(fv) or math.isinf(fv):
            continue
        cleaned[k] = fv
    return cleaned
