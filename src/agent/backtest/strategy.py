"""Стратегии для бэктеста.

Strategy.decide получает историю КАЖДОГО тикера, обрезанную по текущий бар
включительно (no-lookahead гарантируется движком), и портфель — возвращает
сигналы {ticker: Signal}. Risk Officer и сайзинг применяет уже движок.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from agent.features.indicators import MIN_BARS, compute_features


@dataclass
class Signal:
    """Лёгкий аналог schemas.Decision для бэктеста — risk.evaluate читает
    только эти 4 атрибута (duck typing)."""

    symbol: str
    signal: str  # "BUY" | "SELL" | "HOLD"
    size_pct: float
    confidence: float


class Strategy(Protocol):
    name: str

    def decide(
        self, history: dict[str, pd.DataFrame], portfolio: dict
    ) -> dict[str, Signal]: ...


class TechnicalStrategy:
    """Прозрачные правила на индикаторах (трендовая + MACD + RSI-фильтр).

    bullish: EMA20>EMA50 и MACD-hist>0 и RSI<rsi_overbought
    bearish: EMA20<EMA50 и MACD-hist<0 и RSI>rsi_oversold
    Иначе HOLD. confidence растёт при согласии большего числа условий.
    """

    name = "technical"

    def __init__(
        self,
        size_pct: float = 0.10,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
        allow_short: bool = True,
    ) -> None:
        self.size_pct = size_pct
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.allow_short = allow_short

    def _signal_for(self, symbol: str, df: pd.DataFrame) -> Signal:
        if df is None or len(df) < MIN_BARS:
            return Signal(symbol, "HOLD", 0.0, 0.0)
        try:
            f = compute_features(df)
        except Exception:
            return Signal(symbol, "HOLD", 0.0, 0.0)

        ema20 = f.get("ema20")
        ema50 = f.get("ema50")
        macd_hist = f.get("macd_hist")
        rsi = f.get("rsi14")
        if None in (ema20, ema50, macd_hist, rsi):
            return Signal(symbol, "HOLD", 0.0, 0.0)

        bull_conds = [ema20 > ema50, macd_hist > 0, rsi < self.rsi_overbought]
        bear_conds = [ema20 < ema50, macd_hist < 0, rsi > self.rsi_oversold]

        if all(bull_conds):
            conf = 0.5 + 0.15 * sum(bull_conds)  # 0.5..0.95
            return Signal(symbol, "BUY", self.size_pct, min(conf, 0.95))
        if all(bear_conds) and self.allow_short:
            conf = 0.5 + 0.15 * sum(bear_conds)
            return Signal(symbol, "SELL", self.size_pct, min(conf, 0.95))
        return Signal(symbol, "HOLD", 0.0, 0.0)

    def decide(
        self, history: dict[str, pd.DataFrame], portfolio: dict
    ) -> dict[str, Signal]:
        return {sym: self._signal_for(sym, df) for sym, df in history.items()}


class LLMStrategy:
    """Адаптер боевого графа (analyst→[news]→[debate]→trader) под бэктест.

    Закрывает слепую зону: до этого офлайн проверялась только TechnicalStrategy
    (прокси), а реальный LLM-движок — ничем. Здесь `decide()` строит снапшот из
    НАРЕЗАННОЙ истории (no-lookahead гарантируется движком) и гоняет настоящий
    граф, возвращая Signal из Decision.

    Ограничения:
      * news по умолчанию ВЫКЛ — исторических новостей в бэктесте нет, иначе
        news-узел полез бы в сеть и тянул сегодняшние новости (lookahead).
      * каждый decide = N_тикеров × (analyst+debate+trader) LLM-вызовов → дорого
        и медленно. Гонять на маленьком сэмпле (1-3 тикера, дневной интервал).

    Чтобы market_analyst не дёргал сеть, на время invoke патчим
    agent.graph.market_data.get_candles на нарезанный df тикера.
    """

    name = "llm"

    def __init__(
        self,
        graph=None,
        interval: int = 60,
        *,
        llm=None,
        news_enabled: bool = False,
        debate_enabled: bool = True,
        commission_rate: float = 0.0005,
    ) -> None:
        if graph is None:
            from agent.graph.build import build_graph
            graph = build_graph(
                llm=llm, news_enabled=news_enabled, debate_enabled=debate_enabled
            )
        self.graph = graph
        self.interval = interval
        self.commission_rate = commission_rate

    def decide(
        self, history: dict[str, pd.DataFrame], portfolio: dict
    ) -> dict[str, "Signal"]:
        from unittest.mock import patch

        from agent.runtime.risk import lot_size_for

        positions = portfolio.get("positions", {})
        out: dict[str, Signal] = {}
        for sym, df in history.items():
            if df is None or len(df) < MIN_BARS:
                out[sym] = Signal(sym, "HOLD", 0.0, 0.0)
                continue
            # Candle-схема боевого снапшота требует `value` (оборот ₽); кэш
            # бэктеста хранит только OHLCV → достраиваем (≈ close×volume).
            # На индикаторы не влияет, нужно лишь чтобы граф не падал.
            if "value" not in df.columns:
                df = df.assign(value=df["close"] * df["volume"])
            lots = float(positions.get(sym, 0) or 0)
            shares = int(lots * lot_size_for(sym))
            state_in = {
                "symbol": sym,
                "interval": self.interval,
                "current_position": shares,
                "commission_rate": self.commission_rate,
            }
            try:
                with patch("agent.graph.market_data.get_candles", return_value=df):
                    result = self.graph.invoke(state_in)
                dec = result.get("decision")
            except Exception:
                dec = None
            if dec is None:
                out[sym] = Signal(sym, "HOLD", 0.0, 0.0)
            else:
                out[sym] = Signal(
                    sym, dec.signal, float(dec.size_pct), float(dec.confidence)
                )
        return out


class BuyAndHoldStrategy:
    """Покупает равным весом на первом баре, дальше HOLD. Эталон-бенчмарк.
    (Чистый buy-and-hold лучше считать через engine.buy_and_hold_equity —
    эта версия для случая, когда хотим прогнать его через тот же движок/риск.)"""

    name = "buy_and_hold"

    def __init__(self, n_tickers: int) -> None:
        self.size_pct = 1.0 / max(n_tickers, 1)

    def decide(
        self, history: dict[str, pd.DataFrame], portfolio: dict
    ) -> dict[str, Signal]:
        out: dict[str, Signal] = {}
        held = portfolio.get("positions", {})
        for sym in history:
            if held.get(sym, 0) != 0:
                out[sym] = Signal(sym, "HOLD", 0.0, 0.0)
            else:
                out[sym] = Signal(sym, "BUY", self.size_pct, 1.0)
        return out
