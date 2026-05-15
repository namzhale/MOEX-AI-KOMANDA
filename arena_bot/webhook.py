import logging
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "api_token",
    "token",
    "secret",
    "password",
    "headers",
}


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in SENSITIVE_KEYS:
            continue
        clean[key] = value
    return clean


@dataclass
class WebhookNotifier:
    url: str | None
    source: str
    timeout_seconds: int = 5
    session: requests.Session | None = None

    def send(self, event: str, payload: dict[str, Any]) -> bool:
        if not self.url:
            return False

        http = self.session or requests
        body = {
            "source": self.source,
            "event": event,
            "payload": sanitize_payload(payload),
        }

        try:
            response = http.post(self.url, json=body, timeout=self.timeout_seconds)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Webhook delivery failed: %s: %s", type(exc).__name__, exc)
            return False

        return True
