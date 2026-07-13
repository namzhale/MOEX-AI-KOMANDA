from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx
import structlog

from agent.config import settings

log = structlog.get_logger()

SENSITIVE_KEYS = frozenset({
    "authorization",
    "api_key",
    "api_token",
    "token",
    "secret",
    "password",
    "headers",
    "sandbox_api_key",
    "polza_api_key",
    "algopack_token",
})


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Убирает секреты из webhook payload (паттерн namzhale/arena_bot)."""
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            clean[key] = sanitize_payload(value)
        else:
            clean[key] = value
    return clean


@dataclass
class WebhookNotifier:
    url: str | None
    source: str
    timeout_seconds: float = 5.0
    _client: httpx.Client | None = field(default=None, repr=False)

    def send(self, event: str, payload: dict[str, Any]) -> bool:
        if not self.url:
            return False

        body = {
            "source": self.source,
            "event": event,
            "payload": sanitize_payload(payload),
        }
        try:
            client = self._client or httpx.Client(timeout=self.timeout_seconds)
            response = client.post(self.url, json=body)
            response.raise_for_status()
            log.debug("webhook.sent", webhook_event=event, status_code=response.status_code)
            return True
        except Exception as exc:
            log.warning(
                "webhook.delivery_failed",
                webhook_event=event,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            return False


@lru_cache(maxsize=1)
def get_webhook_notifier() -> WebhookNotifier:
    return WebhookNotifier(
        url=(settings.LOG_WEBHOOK_URL or "").strip() or None,
        source=settings.WEBHOOK_SOURCE.strip() or settings.ARENAGO_BOT or "team-24",
        timeout_seconds=settings.LOG_WEBHOOK_TIMEOUT_SECONDS,
    )
