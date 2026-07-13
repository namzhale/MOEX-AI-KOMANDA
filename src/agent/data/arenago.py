"""ArenaGo REST client — https://arenago.ru/api."""

from __future__ import annotations

from typing import Literal

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from agent.config import settings

log = structlog.get_logger()


Direction = Literal["B", "S"]


def _is_retryable_arenago(exc: BaseException) -> bool:
    """Retry на сетевых таймаутах и 5xx. **НЕ** retry на 4xx — иначе
    повторно отправим уже принятую заявку (idempotency-риск)."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


def _fingerprint(key: str) -> dict:
    # TODO(security): см. llm/client.py — value: key добавлен временно для диагностики.
    if not key:
        return {"len": 0}
    return {
        "len": len(key),
        "prefix": key[:4],
        "suffix": key[-4:],
        "non_ascii": any(ord(c) > 127 for c in key),
        "has_whitespace": any(c.isspace() for c in key),
    }


class ArenaGoClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        bot: str | None = None,
        dry_run: bool | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = (base_url or settings.ARENAGO_BASE_URL).strip().rstrip("/")
        # strip(): CRLF в .env на Windows иначе ломает httpx-заголовок.
        self.api_key = (api_key or settings.SANDBOX_API_KEY).strip()
        self.bot = (bot or settings.ARENAGO_BOT).strip()
        self.dry_run = settings.DRY_RUN if dry_run is None else dry_run
        # ArenaGo требует «голый» токен в Authorization — без префикса Bearer.
        headers = {"Authorization": self.api_key} if self.api_key else {}
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)
        log.info(
            "arenago.init",
            base_url=self.base_url,
            bot=self.bot,
            dry_run=self.dry_run,
            key_fingerprint=_fingerprint(self.api_key),
        )

    def close(self) -> None:
        self._client.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception(_is_retryable_arenago),
        reraise=True,
    )
    def get_bots(self) -> list[dict]:
        r = self._client.get("/api/bots")
        r.raise_for_status()
        return r.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception(_is_retryable_arenago),
        reraise=True,
    )
    def get_positions(self, bot: str | None = None) -> list[dict]:
        b = bot or self.bot
        r = self._client.get(f"/api/positions/{b}")
        r.raise_for_status()
        # /api/positions/{bot} — список позиций: position (signed лоты),
        # average_price, direction, secid.
        return r.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception(_is_retryable_arenago),
        reraise=True,
    )
    def get_trades(self, bot: str | None = None) -> list[dict]:
        b = bot or self.bot
        r = self._client.get(f"/api/trades/{b}")
        r.raise_for_status()
        return r.json()

    def get_portfolio(self) -> dict:
        bots = self.get_bots()
        ours = next((b for b in bots if b.get("name") == self.bot), None)
        cash = float(ours["cash_balance"]) if ours and "cash_balance" in ours else 0.0
        # /api/positions/{bot} (публичный) — список позиций с полями position
        # (signed лоты), average_price, direction, secid. Без total_pnl/lot_size/
        # last_price (это есть только в клиентском эндпоинте, к которому у бота
        # доступа нет). lot_size берём из ISS-таблицы, NAV считаем сами.
        positions = self.get_positions()
        return {"bot": self.bot, "cash": cash, "positions": positions}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=6),
        retry=retry_if_exception(_is_retryable_arenago),
        reraise=True,
    )
    def submit_order(
        self,
        secid: str,
        direction: Direction,
        quantity: int,
        bot: str | None = None,
    ) -> dict:
        b = bot or self.bot
        payload = {"direction": direction, "secid": secid, "quantity": int(quantity), "bot": b}

        if self.dry_run:
            log.warning("arenago.dry_run.submit_order", **payload)
            return {"success": True, "status": "DRY_RUN", "order": payload}

        log.info("arenago.submit_order.send", **payload)
        r = self._client.post("/api/submit_order", json=payload)
        # raise_for_status — tenacity ловит HTTPStatusError, retries только 5xx.
        r.raise_for_status()
        result = r.json()
        log.info("arenago.submit_order.ok", payload=payload, response=result)
        return result
