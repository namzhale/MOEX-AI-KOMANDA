from __future__ import annotations

from agent.logging import DEFAULT_WEBHOOK_EVENTS, make_webhook_processor


class _Emitter:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def emit(self, payload: dict) -> None:
        self.payloads.append(payload)


def test_webhook_processor_emits_matching_events() -> None:
    emitter = _Emitter()
    processor = make_webhook_processor(
        emitter=emitter,
        events_filter="scheduler.*,arenago.submit_order.*",
    )

    event = {"event": "scheduler.tick.done", "tick_n": 7, "errors": 0}
    out = processor(None, "info", event)

    assert out is event
    assert emitter.payloads == [
        {"event": "scheduler.tick.done", "tick_n": 7, "errors": 0}
    ]


def test_webhook_processor_skips_non_matching_events() -> None:
    emitter = _Emitter()
    processor = make_webhook_processor(
        emitter=emitter,
        events_filter="scheduler.*,arenago.submit_order.*",
    )

    event = {"event": "node.analyst.start", "symbol": "SBER"}
    out = processor(None, "info", event)

    assert out is event
    assert emitter.payloads == []


def test_webhook_processor_star_emits_any_event() -> None:
    emitter = _Emitter()
    processor = make_webhook_processor(
        emitter=emitter,
        events_filter="*",
    )

    event = {"event": "llm.response.body", "schema": "TraderDecision"}
    out = processor(None, "debug", event)

    assert out is event
    assert emitter.payloads == [{"event": "llm.response.body", "schema": "TraderDecision"}]


def test_webhook_processor_redacts_sensitive_fields() -> None:
    emitter = _Emitter()
    processor = make_webhook_processor(
        emitter=emitter,
        events_filter="*",
    )

    event = {
        "event": "arenago.init",
        "api_key": "secret-token",
        "headers": {"Authorization": "secret-token"},
        "nested": [{"refresh_token": "refresh-secret", "symbol": "SBER"}],
    }
    out = processor(None, "info", event)

    assert out is event
    assert emitter.payloads == [
        {
            "event": "arenago.init",
            "api_key": "[REDACTED]",
            "headers": {"Authorization": "[REDACTED]"},
            "nested": [{"refresh_token": "[REDACTED]", "symbol": "SBER"}],
        }
    ]


def test_webhook_processor_never_breaks_logging_when_emitter_fails() -> None:
    class _FailingEmitter:
        def emit(self, payload: dict) -> None:
            raise RuntimeError("webhook down")

    processor = make_webhook_processor(
        emitter=_FailingEmitter(),
        events_filter="scheduler.*",
    )
    event = {"event": "scheduler.tick.done"}

    assert processor(None, "info", event) is event


def test_default_webhook_events_keep_only_important_operational_events() -> None:
    emitter = _Emitter()
    processor = make_webhook_processor(
        emitter=emitter,
        events_filter=DEFAULT_WEBHOOK_EVENTS,
    )

    for event in [
        {"event": "agent.trader.response", "symbol": "SBER"},
        {"event": "agent.analyst.response", "symbol": "SBER"},
        {"event": "agent.news.response", "symbol": "SBER"},
        {"event": "agent.bull.response", "symbol": "SBER"},
        {"event": "agent.bear.response", "symbol": "SBER"},
        {"event": "arenago.submit_order.ok", "secid": "SBER"},
        {"event": "arenago.dry_run.submit_order", "secid": "SBER"},
        {"event": "risk_block", "symbol": "SBER"},
        {"event": "scheduler.ticker.failed", "ticker": "SBER"},
        {"event": "journal.write_failed", "path": "/data/decisions.jsonl"},
    ]:
        processor(None, "info", event)

    for event in [
        {"event": "node.analyst.start", "symbol": "SBER"},
        {"event": "llm.client.init", "role": "trader"},
        {"event": "llm.request.body", "role": "trader"},
        {"event": "moex.candles.fetch", "symbol": "SBER"},
        {"event": "news.fetch.ok", "source": "tass"},
        {"event": "scheduler.ticker.done", "ticker": "SBER"},
        {"event": "scheduler.tick.done", "n": 1},
    ]:
        processor(None, "info", event)

    assert [payload["event"] for payload in emitter.payloads] == [
        "agent.trader.response",
        "agent.analyst.response",
        "agent.news.response",
        "agent.bull.response",
        "agent.bear.response",
        "arenago.submit_order.ok",
        "arenago.dry_run.submit_order",
        "risk_block",
        "scheduler.ticker.failed",
        "journal.write_failed",
    ]
