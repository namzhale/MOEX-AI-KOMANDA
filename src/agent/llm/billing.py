from __future__ import annotations

import httpx
import structlog

from agent.config import settings

log = structlog.get_logger()


class PolzaBillingClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = (api_key or settings.POLZA_API_KEY).strip()
        self.base_url = (base_url or settings.LLM_BASE_URL).strip().rstrip("/")
        timeout_value = (
            float(timeout)
            if timeout is not None
            else float(getattr(settings, "POLZA_BALANCE_TIMEOUT_SECONDS", 5.0) or 5.0)
        )
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout_value,
            transport=transport,
        )

    def get_balance_amount(self) -> float:
        response = self._client.get("balance")
        response.raise_for_status()
        data = response.json()
        raw = data.get("amount", data.get("balance"))
        if raw is None:
            raise ValueError("Polza balance response does not contain amount")
        return float(raw)

    def close(self) -> None:
        self._client.close()
