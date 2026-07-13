from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import urllib.error
import urllib.request
from contextlib import suppress
from typing import Any

import structlog

DEFAULT_WEBHOOK_EVENTS = (
    "agent.*,"
    "arenago.submit_order.*,"
    "arenago.dry_run.submit_order,"
    "risk_*,"
    "scheduler.ticker.failed,"
    "journal.write_failed"
)
REDACTED = "[REDACTED]"
SENSITIVE_FIELD_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "access_token",
    "refresh_token",
    "token",
    "password",
    "secret",
    "sandbox_api_key",
    "arenago_api_key",
    "polza_api_key",
    "algopack_token",
    "headers",
}


class AsyncWebhookEmitter:
    def __init__(
        self,
        url: str,
        timeout_seconds: float = 1.0,
        max_queue: int = 1000,
    ) -> None:
        self.url = url
        self.timeout_seconds = max(float(timeout_seconds), 0.1)
        self.queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=max(int(max_queue), 1)
        )
        self.worker = threading.Thread(
            target=self._run,
            name="log-webhook-emitter",
            daemon=True,
        )
        self.worker.start()

    def emit(self, payload: dict[str, Any]) -> None:
        with suppress(queue.Full):
            self.queue.put_nowait(dict(payload))

    def _run(self) -> None:
        while True:
            payload = self.queue.get()
            if payload is None:
                return
            try:
                data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                request = urllib.request.Request(
                    self.url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.timeout_seconds):
                    pass
            except (OSError, urllib.error.URLError, ValueError):
                pass


def make_webhook_processor(
    emitter,
    events_filter: str = DEFAULT_WEBHOOK_EVENTS,
) -> structlog.types.Processor:
    patterns = _parse_event_patterns(events_filter)

    def processor(_logger, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        event_name = str(event_dict.get("event") or "")
        if not _event_matches(event_name, patterns):
            return event_dict
        with suppress(Exception):
            emitter.emit(_redact_sensitive(event_dict))
        return event_dict

    return processor


def _parse_event_patterns(events_filter: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in events_filter.split(",") if part.strip())


def _event_matches(event_name: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        if pattern == "*":
            return True
        if pattern.endswith("*") and event_name.startswith(pattern[:-1]):
            return True
        if event_name == pattern:
            return True
    return False


def _is_sensitive_field(key: object) -> bool:
    key_lower = str(key).lower()
    return (
        key_lower in SENSITIVE_FIELD_NAMES
        or key_lower.endswith("_api_key")
        or key_lower.endswith("_token")
        or "authorization" in key_lower
    )


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_field(key):
                if isinstance(item, dict):
                    redacted[key] = _redact_sensitive(item)
                elif isinstance(item, list):
                    redacted[key] = _redact_sensitive(item)
                else:
                    redacted[key] = REDACTED
            else:
                redacted[key] = _redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive(item) for item in value)
    return value


def configure_logging(
    level: str = "INFO",
    fmt: str = "console",
    webhook_url: str | None = None,
    webhook_events: str | None = None,
    webhook_timeout_seconds: float | None = None,
    webhook_max_queue: int | None = None,
) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    resolved_webhook_url = (
        webhook_url if webhook_url is not None else os.getenv("LOG_WEBHOOK_URL", "")
    ).strip()
    if resolved_webhook_url:
        timeout = (
            webhook_timeout_seconds
            if webhook_timeout_seconds is not None
            else float(os.getenv("LOG_WEBHOOK_TIMEOUT_SECONDS", "1.0") or 1.0)
        )
        max_queue = (
            webhook_max_queue
            if webhook_max_queue is not None
            else int(os.getenv("LOG_WEBHOOK_MAX_QUEUE", "1000") or 1000)
        )
        events_filter = (
            webhook_events
            if webhook_events is not None
            else os.getenv("LOG_WEBHOOK_EVENTS", DEFAULT_WEBHOOK_EVENTS)
        )
        shared_processors.append(
            make_webhook_processor(
                emitter=AsyncWebhookEmitter(
                    resolved_webhook_url,
                    timeout_seconds=timeout,
                    max_queue=max_queue,
                ),
                events_filter=events_filter,
            )
        )

    if fmt.lower() == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        # Без явного exception_formatter: ConsoleRenderer возьмёт rich, если он есть,
        # иначе откатится к plain — кастомный formatter тут роняет инициализацию.
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            sort_keys=False,
            pad_event=28,
        )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
