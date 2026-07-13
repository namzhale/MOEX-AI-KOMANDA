from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Candle(BaseModel):
    begin: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    value: float


class MarketSnapshot(BaseModel):
    symbol: str
    interval: int
    candles: list[Candle]
    features: dict[str, float]


class AnalystOutput(BaseModel):
    trend: Literal["up", "down", "flat"]
    momentum: Literal["strong_up", "weak_up", "flat", "weak_down", "strong_down"]
    volatility: Literal["low", "normal", "high"]
    # Field descriptions ниже попадают в JSON-schema, который видит LLM — не удаляем.
    summary: str = Field(description="1-2 sentences describing the current setup")
    confidence: float = Field(ge=0, le=1)


class BullArgument(BaseModel):
    thesis: str = Field(description="1-2 sentence bull case for this ticker")
    key_points: list[str] = Field(description="2-4 specific reasons price will rise")
    confidence: float = Field(ge=0, le=1)
    rebuttal: str | None = Field(
        default=None,
        description="Counter to the previous bear argument (null on round 0)",
    )


class BearArgument(BaseModel):
    thesis: str = Field(description="1-2 sentence bear case for this ticker")
    key_points: list[str] = Field(description="2-4 specific reasons price will fall")
    confidence: float = Field(ge=0, le=1)
    rebuttal: str | None = Field(
        default=None,
        description="Counter to the previous bull argument (null on round 0)",
    )


class NewsAnalystOutput(BaseModel):
    sentiment: Literal["bullish", "neutral", "bearish"]
    key_events: list[str] = Field(
        default_factory=list,
        description="2-4 concrete events from the news (dividends, earnings, sanctions, M&A, ...)",
    )
    citations: list[str] = Field(
        default_factory=list,
        description="URLs of source articles",
    )
    confidence: float = Field(
        ge=0, le=1,
        description="0 = conflicting / unclear, 1 = unanimous clear story",
    )
    raw_news_count: int = Field(
        ge=0,
        description="How many news items were aggregated to produce this view",
    )


class TraderDecision(BaseModel):
    """Торговая часть, которую возвращает LLM. Метаданные (symbol, timestamp,
    analyst_output) дописываются кодом после получения ответа."""

    signal: Literal["BUY", "SELL", "HOLD"]
    size_pct: float = Field(ge=0, le=1, description="Share of total capital, 0..1")
    confidence: float = Field(default=0.0, ge=0, le=1)
    reasoning: str = Field(description="1-2 sentences justifying the call")


class Decision(BaseModel):
    symbol: str
    signal: Literal["BUY", "SELL", "HOLD"]
    size_pct: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    analyst_output: AnalystOutput
    timestamp: datetime


class ReflectionRecord(BaseModel):
    """Запись долгосрочной памяти (JSONL + опционально Qdrant)."""

    symbol: str
    trade_id: str
    lesson: str = Field(description="1-3 sentences: what worked or failed")
    tags: list[str] = Field(default_factory=list)
    importance: float = Field(ge=0, le=1, default=0.5)
    pnl_hint: str | None = None
    outcome: Literal["win", "loss", "flat", "unknown"] | None = None
    sector: str | None = None
    source: Literal["trade", "meta", "hypothesis"] = "trade"
    timestamp: datetime | None = None


class RiskGateResult(BaseModel):
    """Что вернул Risk Officer scheduler-у."""

    allowed: bool
    gate: str  # имя последнего сработавшего гейта ("all_passed" если все ок)
    reason: str
    effective_size: float | None = None  # после возможного clip'а
    qty: int | None = None  # финальное кол-во для одиночной заявки
    metrics: dict[str, float] = Field(default_factory=dict)
    # Single-tick flip через ноль: scheduler делает 2 submit_order'а подряд
    # (close → open). flip_close_qty всегда положительный.
    flip_close_qty: int | None = None
    flip_open_qty: int | None = None
    # op_type — что именно делаем; полезно для логов и Loki-фильтров
    op_type: str | None = None
