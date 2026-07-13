from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog

from agent.data.microstructure import format_flow_prompt_block
from agent.graph.market_data import load_market_snapshot
from agent.llm.client import LLMClient
from agent.memory.retrieval import format_memory_block
from agent.schemas import AnalystOutput, Decision, MarketSnapshot, TraderDecision

log = structlog.get_logger()

DEFAULT_INTERVAL = 60
DEFAULT_DAYS = 30
MAX_SIZE_PCT = 0.15


ANALYST_SYSTEM = """\
You are a senior technical analyst for Moscow Exchange (MOEX) stocks (board TQBR).
You analyze precomputed indicator values and return a strictly structured JSON
assessment. Be objective and grounded in the numbers — don't invent levels.
`confidence` reflects how clear the signal is (0=mixed/noisy, 1=textbook setup).

Be concise: `summary` ≤ 2 sentences, no markdown, no quoted phrases.
"""


TRADER_SYSTEM = f"""\
You are a disciplined risk-aware trader on MOEX. Both LONG and SHORT positions are permitted.
You will see: the analyst's technical read AND a Bull/Bear debate transcript
(zero or more rounds). Weigh both sides briefly in `reasoning` before deciding.
Decide BUY / SELL / HOLD.

- size_pct is share of total capital, in [0, {MAX_SIZE_PCT}] (15% hard cap), same scale for both directions.
- Signal semantics depends on `Current position` (signed: negative = short):
    * BUY when position >= 0 → opens or adds to a LONG.
    * BUY when position <  0 → covers (closes) a SHORT. Use when bear thesis is exhausted.
    * SELL when position >  0 → closes a LONG. Use to lock profits or cut losers.
    * SELL when position <= 0 → opens or adds to a SHORT. Use when bear thesis dominates.
- No single-tick reversals: an opposite-side signal only CLOSES the current position to flat
  (never flips through zero in one step). To reverse, close now; opening the opposite side is a
  separate decision on a later tick, with its own thesis. So treat "exit" and "enter opposite"
  as two independent calls.
- The system auto-closes a position at +TP%/-SL% (take-profit / stop-loss) on its own P&L,
  regardless of your signal — you don't need to micro-manage exits at those thresholds.
- When enabled, the deterministic Risk Officer may also apply soft profit-lock above your
  decision: +0.7% unrealized P&L means partial close only if you return HOLD or the
  opposite-side signal; +1.2% means partial close regardless of your signal; +2.0% means
  full take-profit; -2.0% means full stop-loss.
- If an existing position is profitable but the current edge is no longer clearly improving,
  prefer HOLD over adding to the same side; this lets the Risk Officer lock profit. Do not
  force an exit solely to manage those thresholds.
- Before any non-HOLD, weigh the EXPECTED MOVE, not just the direction: estimate a
  realistic upside % and downside % over the next ~1-2 hours (with rough odds) — judge
  this horizon, NOT one or two candles — and trade only if the expected favourable move
  clearly exceeds the round-trip cost (~0.10%) AND reward >= risk (upside >= downside).
- Judge DIRECTION and MAGNITUDE separately. If one side of the debate clearly wins
  (a confident, directionally-skewed read), a modest absolute move still trades as long
  as the favourable side beats the round-trip cost — do NOT label a skewed setup
  "symmetric" merely because volatility is low.
- A genuinely small AND/OR symmetric expected move is a HOLD. "Likely to rise a little"
  does NOT justify a BUY if the move barely clears commission.
- Account for execution commission on every entry and exit.
- Use live portfolio context when it is provided: NAV, available cash, gross/net exposure,
  and current ticker weight change over the day. Do not assume a fixed starting capital;
  scale size_pct to the current portfolio and require a clearer edge when cash is scarce,
  gross exposure is already high, or this ticker is already a meaningful share of NAV.
- Use market context when provided as a neutral description of the broad tape.
  Do not mechanically bias BUY or SELL from market regime alone; decide direction from
  ticker-specific evidence, current position, risk/reward, and the debate.
- Default to HOLD when signals are mixed, conflicting, or analyst confidence is low.
- size_pct must be 0 when signal is HOLD.
- Explain the call in 1-2 sentences in `reasoning`, citing which side of the debate won.
"""


def _ctx_float(ctx: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(ctx.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def hold_finalize_node(state: dict) -> dict:
    """HOLD без trader-LLM (early exit после Market Analyst)."""
    symbol = state["symbol"]
    analyst: AnalystOutput = state["analyst"]
    log.info(
        "node.hold_finalize",
        symbol=symbol,
        path=state.get("graph_path", "early_exit"),
        analyst_confidence=analyst.confidence,
    )
    decision = Decision(
        symbol=symbol,
        signal="HOLD",
        size_pct=0.0,
        confidence=0.0,
        reasoning=(
            f"Early exit: flat setup, analyst confidence {analyst.confidence:.2f} "
            f"— skipped news/debate/trader LLM."
        ),
        analyst_output=analyst,
        timestamp=datetime.now(UTC),
    )
    return {"decision": decision, "graph_path": "early_exit_hold"}


def market_analyst_node(state: dict, llm: LLMClient) -> dict:
    symbol = state["symbol"]
    interval = state.get("interval", DEFAULT_INTERVAL)
    log.info("node.analyst.start", symbol=symbol, interval=interval)

    snapshot = state.get("snapshot")
    if snapshot is None:
        snapshot = load_market_snapshot(symbol, interval=interval)
    features = snapshot.features

    memory_block = format_memory_block(symbol)
    flow_block = format_flow_prompt_block(features)
    user = (
        f"Ticker: {symbol} (MOEX TQBR)\n"
        f"Interval: {interval} min, last {len(snapshot.candles)} bars in attached snapshot.\n"
        f"Indicator values (last bar):\n{json.dumps(features, indent=2, default=float)}\n"
        + (f"{flow_block}" if flow_block else "")
        + (f"{memory_block}\n" if memory_block else "")
        + "Return your structured analysis."
    )
    try:
        analyst = llm.complete_json(ANALYST_SYSTEM, user, AnalystOutput)
    except Exception as e:
        # Если LLM-провайдер недоступен — отдаём нейтральный analyst output,
        # дальше debate/trader увидят low confidence и выдадут HOLD.
        log.warning("node.analyst.llm_failed_fallback", symbol=symbol, error=str(e)[:200])
        analyst = AnalystOutput(
            trend="flat", momentum="flat", volatility="normal",
            summary="LLM unavailable — neutral fallback.", confidence=0.0,
        )
    log.info(
        "agent.analyst.response",
        symbol=symbol,
        role="analyst",
        schema=AnalystOutput.__name__,
        output=analyst.model_dump(),
    )
    log.info(
        "node.analyst.ok",
        symbol=symbol,
        trend=analyst.trend,
        confidence=analyst.confidence,
    )
    return {"snapshot": snapshot, "analyst": analyst, "interval": interval}


def trader_node(state: dict, llm: LLMClient) -> dict:
    symbol = state["symbol"]
    analyst: AnalystOutput = state["analyst"]
    debate = state.get("debate_arguments") or []
    news = state.get("news")
    try:
        current_position = int(float(state.get("current_position") or 0))
    except (TypeError, ValueError):
        current_position = 0
    try:
        commission_rate = max(float(state.get("commission_rate") or 0.0), 0.0)
    except (TypeError, ValueError):
        commission_rate = 0.0
    log.info(
        "node.trader.start",
        symbol=symbol,
        current_position=current_position,
        commission_rate=commission_rate,
        debate_rounds=len(debate),
        news_raw_count=getattr(news, "raw_news_count", 0),
    )

    # Сжатый analyst-блок: inline вместо полного JSON. Экономит ~150 chars
    # на промпт + повышает chance prompt-cache hit (стабильный префикс).
    user_parts = [
        f"Ticker: {symbol}",
        (
            f"Analyst: trend={analyst.trend}, momentum={analyst.momentum}, "
            f"volatility={analyst.volatility}, confidence={analyst.confidence:.2f}"
        ),
        f"Analyst summary: {analyst.summary}",
    ]
    if news is not None:
        # News-блок тоже компактно — sentiment + первые 3 события + confidence.
        events = (getattr(news, "key_events", []) or [])[:3]
        events_str = "; ".join(events) if events else "(no key events)"
        user_parts.append(
            f"News (quarantined): sentiment={news.sentiment}, "
            f"confidence={news.confidence:.2f}, items={news.raw_news_count}. "
            f"Events: {events_str}"
        )
    if debate:
        # Сжатый transcript: только thesis + 2 ключевых пункта последнего раунда
        # на каждую сторону. Полный JSON дебатов раздувал prompt в 5-10×.
        last = debate[-1]
        user_parts.append(f"Bull/Bear debate (rounds={len(debate)}, showing last):")
        for side in ("bull", "bear"):
            side_data = last.get(side, {}) or {}
            thesis = (side_data.get("thesis") or "").strip()
            key_points = side_data.get("key_points") or []
            conf = side_data.get("confidence", 0.0)
            kp_lines = "; ".join(str(p).strip() for p in key_points[:2])
            user_parts.append(
                f"  {side.upper()} (conf={conf:.2f}): {thesis}"
                + (f"\n    Top points: {kp_lines}" if kp_lines else "")
            )
    else:
        user_parts.append("Bull/Bear debate: (skipped — debate disabled or 0 rounds)")
    user_parts.append(f"Current position: {current_position} shares.")
    user_parts.append(f"Estimated commission: {commission_rate * 100:.4f}% per order.")
    portfolio_context = state.get("portfolio_context")
    if isinstance(portfolio_context, dict) and portfolio_context:
        nav = _ctx_float(portfolio_context, "nav")
        cash = _ctx_float(portfolio_context, "cash")
        cash_pct = _ctx_float(portfolio_context, "cash_pct")
        gross_pct = _ctx_float(portfolio_context, "gross_exposure_pct")
        net_pct = _ctx_float(portfolio_context, "net_exposure_pct")
        current_weight_pct = _ctx_float(portfolio_context, "current_weight_pct")
        current_value = _ctx_float(portfolio_context, "current_value")
        positions_count = int(_ctx_float(portfolio_context, "positions_count"))
        user_parts.append(
            "Portfolio context: "
            f"NAV {nav:.2f} RUB, "
            f"cash {cash:.2f} RUB ({cash_pct:.2%} NAV), "
            f"gross exposure {gross_pct:.2%} NAV, "
            f"net exposure {net_pct:.2%} NAV, "
            f"current ticker weight {current_weight_pct:.2%} NAV "
            f"(value {current_value:.2f} RUB), "
            f"open positions {positions_count}."
        )
    market_context = state.get("market_context")
    if isinstance(market_context, dict) and market_context:
        regime = str(market_context.get("regime") or "unknown")
        fast_minutes = int(_ctx_float(market_context, "fast_window_minutes", 60))
        mid_minutes = int(_ctx_float(market_context, "mid_window_minutes", 240))
        fast_return = _ctx_float(market_context, "fast_return")
        mid_return = _ctx_float(market_context, "mid_return")
        breadth_up_pct = _ctx_float(market_context, "breadth_up_pct")
        symbols = int(_ctx_float(market_context, "symbols"))
        user_parts.append(
            "Market context: "
            f"regime={regime}, "
            f"fast_return_{fast_minutes}m {fast_return:+.2%}, "
            f"mid_return_{mid_minutes // 60}h {mid_return:+.2%}, "
            f"breadth_up {breadth_up_pct:.2%}, "
            f"symbols {symbols}."
        )
    user_parts.append(f"Hard cap: size_pct ∈ [0, {MAX_SIZE_PCT}]. Decide.")
    memory_block = format_memory_block(symbol)
    if memory_block:
        user_parts.append(memory_block)
    snap = state.get("snapshot")
    if snap and snap.features:
        flow_block = format_flow_prompt_block(snap.features)
        if flow_block:
            user_parts.append(flow_block.strip())
    user = "\n".join(user_parts)

    try:
        raw = llm.complete_json(TRADER_SYSTEM, user, TraderDecision)
    except Exception as e:
        log.warning("node.trader.llm_failed_fallback_hold", symbol=symbol, error=str(e)[:200])
        raw = TraderDecision(
            signal="HOLD", size_pct=0.0, confidence=0.0,
            reasoning="LLM unavailable — defaulting to HOLD.",
        )
    log.info(
        "agent.trader.response",
        symbol=symbol,
        role="trader",
        schema=TraderDecision.__name__,
        output=raw.model_dump(),
    )

    # LLM иногда возвращает ненулевой size при HOLD или превышает кап — поправляем руками.
    size = min(max(raw.size_pct, 0.0), MAX_SIZE_PCT)
    if raw.signal == "HOLD":
        size = 0.0

    log.debug(
        "node.trader.raw_response",
        symbol=symbol,
        raw=raw.model_dump(),
    )
    decision = Decision(
        symbol=symbol,
        signal=raw.signal,
        size_pct=size,
        confidence=raw.confidence,
        reasoning=raw.reasoning,
        analyst_output=analyst,
        timestamp=datetime.now(UTC),
    )
    log.info(
        "node.trader.ok",
        symbol=symbol,
        signal=decision.signal,
        size_pct=decision.size_pct,
        confidence=decision.confidence,
    )
    log.info(
        "node.trader.rationale",
        symbol=symbol,
        signal=decision.signal,
        size_pct=round(decision.size_pct, 4),
        confidence=round(decision.confidence, 3),
        analyst_trend=analyst.trend,
        analyst_momentum=analyst.momentum,
        analyst_volatility=analyst.volatility,
        analyst_confidence=round(analyst.confidence, 3),
        news_sentiment=getattr(news, "sentiment", "absent"),
        news_events_count=len(getattr(news, "key_events", []) or []),
        debate_rounds=len(debate),
        bull_last_confidence=(
            round(debate[-1]["bull"]["confidence"], 3) if debate else None
        ),
        bear_last_confidence=(
            round(debate[-1]["bear"]["confidence"], 3) if debate else None
        ),
        reasoning=decision.reasoning[:300],
    )
    return {"decision": decision}
