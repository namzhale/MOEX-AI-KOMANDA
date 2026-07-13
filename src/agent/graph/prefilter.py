from __future__ import annotations

import math
from datetime import UTC, datetime

import structlog

from agent.config import settings
from agent.data.microstructure import flow_enabled, mega_alert_symbols_today
from agent.graph.market_data import load_market_snapshot
from agent.schemas import AnalystOutput, Decision

log = structlog.get_logger()


def _safe_float(features: dict[str, float], key: str, default: float = 0.0) -> float:
    v = features.get(key, default)
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return default
    return float(v)


def should_skip_prefilter(
    features: dict[str, float],
    current_position: int,
    *,
    rsi_low: float,
    rsi_high: float,
    macd_hist_abs_max: float,
    ema_spread_pct_max: float,
    disb_abs_max: float | None = None,
    spread_1mio_max_bps: float | None = None,
) -> tuple[bool, str]:
    """Rule-based отсев до любого LLM. Не срабатывает при открытой позиции."""
    if current_position != 0:
        return False, "position_open"

    rsi = _safe_float(features, "rsi14", 50.0)
    macd_hist = _safe_float(features, "macd_hist", 0.0)
    ema20 = _safe_float(features, "ema20", 0.0)
    ema50 = _safe_float(features, "ema50", 0.0)
    close = _safe_float(features, "close", 0.0)

    if not (rsi_low <= rsi <= rsi_high):
        return False, f"rsi_out_of_band:{rsi:.1f}"

    if abs(macd_hist) > macd_hist_abs_max:
        return False, f"macd_hist_active:{macd_hist:.4f}"

    if close > 0 and ema20 > 0 and ema50 > 0:
        spread_pct = abs(ema20 - ema50) / close
        if spread_pct > ema_spread_pct_max:
            return False, f"ema_spread:{spread_pct:.4f}"

    if disb_abs_max is not None and "disb" in features:
        disb = _safe_float(features, "disb", 0.0)
        if abs(disb) > disb_abs_max:
            return False, f"flow_disb_active:{disb:.3f}"

    if spread_1mio_max_bps is not None:
        spread_bps = _safe_float(features, "ob_spread_1mio_bps", 0.0)
        if spread_bps <= 0:
            spread_bps = _safe_float(features, "ob_spread_bbo_bps", 0.0)
        if spread_bps > spread_1mio_max_bps:
            return True, f"illiquid_spread:{spread_bps:.1f}bps"

    return True, "flat_no_signal"


def _neutral_analyst(reason: str) -> AnalystOutput:
    return AnalystOutput(
        trend="flat",
        momentum="flat",
        volatility="normal",
        summary=reason[:200],
        confidence=0.0,
    )


def _hold_decision(symbol: str, analyst: AnalystOutput, reasoning: str) -> Decision:
    return Decision(
        symbol=symbol,
        signal="HOLD",
        size_pct=0.0,
        confidence=0.0,
        reasoning=reasoning,
        analyst_output=analyst,
        timestamp=datetime.now(UTC),
    )


def prefilter_node(state: dict) -> dict:
    """Загружает рынок; при слабом сигнале и нулевой позиции — HOLD без LLM."""
    symbol = state["symbol"]
    interval = state.get("interval", None)

    if not settings.AGENT_PREFILTER_ENABLED:
        log.info("node.prefilter.skipped", symbol=symbol, reason="disabled")
        return {"prefilter_passed": True}

    try:
        current_position = int(float(state.get("current_position") or 0))
    except (TypeError, ValueError):
        current_position = 0

    if (
        flow_enabled()
        and settings.ALGOPACK_MEGA_ALERT_SKIP
        and symbol.upper() in mega_alert_symbols_today((symbol,))
    ):
        analyst = _neutral_analyst("Prefilter: Mega Alert today")
        decision = _hold_decision(
            symbol,
            analyst,
            "Prefilter skip (mega_alert): MOEX anomaly flag — no LLM pipeline.",
        )
        snapshot = load_market_snapshot(symbol, interval=interval)
        return {
            "snapshot": snapshot,
            "interval": snapshot.interval,
            "analyst": analyst,
            "decision": decision,
            "graph_path": "prefilter_hold",
        }

    snapshot = load_market_snapshot(symbol, interval=interval)
    flow_kw: dict = {}
    if flow_enabled():
        flow_kw = {
            "disb_abs_max": settings.ALGOPACK_PREFILTER_DISB_ABS_MAX,
            "spread_1mio_max_bps": settings.ALGOPACK_PREFILTER_SPREAD_1MIO_MAX_BPS,
        }
    skip, reason = should_skip_prefilter(
        snapshot.features,
        current_position,
        rsi_low=settings.AGENT_PREFILTER_RSI_LOW,
        rsi_high=settings.AGENT_PREFILTER_RSI_HIGH,
        macd_hist_abs_max=settings.AGENT_PREFILTER_MACD_HIST_MAX,
        ema_spread_pct_max=settings.AGENT_PREFILTER_EMA_SPREAD_MAX,
        **flow_kw,
    )

    log.info(
        "node.prefilter.done",
        symbol=symbol,
        skip=skip,
        reason=reason,
        position=current_position,
    )

    if not skip:
        return {
            "snapshot": snapshot,
            "interval": snapshot.interval,
            "prefilter_passed": True,
        }

    analyst = _neutral_analyst(f"Prefilter: {reason}")
    decision = _hold_decision(
        symbol,
        analyst,
        f"Prefilter skip ({reason}): no LLM pipeline.",
    )
    return {
        "snapshot": snapshot,
        "interval": snapshot.interval,
        "analyst": analyst,
        "decision": decision,
        "graph_path": "prefilter_hold",
    }
