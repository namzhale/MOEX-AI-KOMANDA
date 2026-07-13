from __future__ import annotations

import json
import time

import structlog

from agent.config import settings
from agent.llm.client import LLMClient
from agent.schemas import AnalystOutput, BearArgument, BullArgument

log = structlog.get_logger()


BULL_SYSTEM = """\
You are a bullish equity analyst for Moscow Exchange (MOEX) stocks.
Your job: build the strongest case the price will RISE in the next 1-3 sessions.
Use the technical indicators and the prior analyst summary you are shown.
On round > 0 you also see the previous bear argument — directly rebut its weakest claim.
Stay grounded in the numbers — do not invent levels.
`confidence` reflects how convincing the bull case is (0=weak, 1=textbook setup).

Be concise:
- `thesis`: ONE sentence (≤ 25 words).
- `key_points`: 2-4 bullets, each ≤ 20 words. No markdown, no nested lists.
- `rebuttal`: ≤ 2 sentences, target the single weakest opponent claim.
- Do NOT repeat the indicators verbatim — reference numbers, don't quote blocks.
"""


BEAR_SYSTEM = """\
You are a bearish equity analyst for Moscow Exchange (MOEX) stocks.
Your job: build the strongest case the price will FALL in the next 1-3 sessions.
Use the technical indicators and the prior analyst summary you are shown.
On round > 0 you also see the previous bull argument — directly rebut its weakest claim.
Stay grounded in the numbers — do not invent levels.
`confidence` reflects how convincing the bear case is (0=weak, 1=textbook setup).

Be concise:
- `thesis`: ONE sentence (≤ 25 words).
- `key_points`: 2-4 bullets, each ≤ 20 words. No markdown, no nested lists.
- `rebuttal`: ≤ 2 sentences, target the single weakest opponent claim.
- Do NOT repeat the indicators verbatim — reference numbers, don't quote blocks.
"""


def bull_bear_debate_node(state: dict, llm: LLMClient) -> dict:
    rounds = max(int(settings.AGENT_DEBATE_ROUNDS), 0)
    symbol = state["symbol"]
    analyst: AnalystOutput = state["analyst"]

    log.info(
        "node.debate.start",
        symbol=symbol,
        rounds=rounds,
        analyst_trend=analyst.trend,
        analyst_confidence=analyst.confidence,
    )

    if rounds == 0:
        log.info("node.debate.done", symbol=symbol, rounds=0, total_llm_calls=0, elapsed_ms=0)
        return {"debate_arguments": []}

    snapshot = state.get("snapshot")
    features_json = (
        json.dumps(snapshot.features, indent=2, default=float)
        if snapshot is not None
        else "{}"
    )
    analyst_json = analyst.model_dump_json(indent=2)
    news_summary = _news_summary(state.get("news"))

    history: list[dict] = []
    t0 = time.monotonic()
    llm_calls = 0

    for round_idx in range(rounds):
        prev_bear = history[-1]["bear"] if history else None

        bull_user = _build_user_prompt(
            symbol=symbol,
            features_json=features_json,
            analyst_json=analyst_json,
            news_summary=news_summary,
            round_idx=round_idx,
            opponent_label="bear",
            opponent_argument=prev_bear,
        )
        bull = llm.complete_json(BULL_SYSTEM, bull_user, BullArgument)
        llm_calls += 1
        log.info(
            "agent.bull.response",
            symbol=symbol,
            role="bull",
            schema=BullArgument.__name__,
            round_idx=round_idx,
            output=bull.model_dump(),
        )

        bear_user = _build_user_prompt(
            symbol=symbol,
            features_json=features_json,
            analyst_json=analyst_json,
            news_summary=news_summary,
            round_idx=round_idx,
            opponent_label="bull",
            opponent_argument=bull,
        )
        bear = llm.complete_json(BEAR_SYSTEM, bear_user, BearArgument)
        llm_calls += 1
        log.info(
            "agent.bear.response",
            symbol=symbol,
            role="bear",
            schema=BearArgument.__name__,
            round_idx=round_idx,
            output=bear.model_dump(),
        )

        history.append(
            {
                "round": round_idx,
                "bull": bull.model_dump(),
                "bear": bear.model_dump(),
            }
        )
        log.info(
            "node.debate.round",
            symbol=symbol,
            round_idx=round_idx,
            bull_confidence=bull.confidence,
            bear_confidence=bear.confidence,
        )
        log.debug(
            "node.debate.round.body",
            symbol=symbol,
            round_idx=round_idx,
            bull=bull.model_dump(),
            bear=bear.model_dump(),
        )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "node.debate.done",
        symbol=symbol,
        rounds=rounds,
        total_llm_calls=llm_calls,
        elapsed_ms=elapsed_ms,
    )
    return {"debate_arguments": history}


def _news_summary(news_analyst) -> str | None:
    """Короткая сводка для bull/bear; None если news не запускался или пуст."""
    if news_analyst is None:
        return None
    events = getattr(news_analyst, "key_events", []) or []
    sentiment = getattr(news_analyst, "sentiment", "neutral")
    raw_count = getattr(news_analyst, "raw_news_count", 0)
    if raw_count == 0 and not events:
        return None
    lines = [
        f"News sentiment (from quarantined analyst): {sentiment}",
        f"News items aggregated: {raw_count}",
    ]
    if events:
        lines.append("Key events:")
        for e in events[:4]:
            lines.append(f"  - {e}")
    return "\n".join(lines)


def _build_user_prompt(
    symbol: str,
    features_json: str,
    analyst_json: str,
    news_summary: str | None,
    round_idx: int,
    opponent_label: str,
    opponent_argument: object | None,
) -> str:
    lines = [
        f"Ticker: {symbol} (MOEX TQBR)",
        f"Round: {round_idx + 1}",
        "Indicator values (last bar):",
        features_json,
        "Prior analyst output:",
        analyst_json,
    ]
    if news_summary:
        lines += ["", news_summary, ""]
    if opponent_argument is not None:
        op_json = (
            opponent_argument.model_dump_json(indent=2)
            if hasattr(opponent_argument, "model_dump_json")
            else json.dumps(opponent_argument, default=str, indent=2)
        )
        lines += [
            f"Previous {opponent_label} argument (rebut its weakest claim):",
            op_json,
        ]
    lines.append("Return your structured argument.")
    return "\n".join(lines)
