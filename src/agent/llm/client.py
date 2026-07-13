from __future__ import annotations

from typing import TypeVar

import httpx
import structlog
from openai import APIStatusError, OpenAI
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from agent.config import settings


# SDK default = 600s read; в проде trader-роль наблюдалась до 171с, держим запас.
# max_retries=0 — retry политикой управляет tenacity ниже.
_LLM_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=75.0, pool=5.0)


def _is_retryable_llm(exc: BaseException) -> bool:
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 409, 425, 429, 500, 502, 503, 504)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return True

log = structlog.get_logger()
T = TypeVar("T", bound=BaseModel)


def _fingerprint(key: str) -> dict:
    """Безопасный отпечаток ключа для логов: длина + края + признак не-ASCII."""
    if not key:
        return {"len": 0}
    return {
        "len": len(key),
        "prefix": key[:4],
        "suffix": key[-4:],
        "non_ascii": any(ord(c) > 127 for c in key),
        "has_whitespace": any(c.isspace() for c in key),
    }


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        role: str = "default",
    ) -> None:
        # strip(): CRLF в .env на Windows иначе ломает httpx-заголовок.
        self.api_key = (api_key or settings.POLZA_API_KEY).strip()
        self.base_url = (base_url or settings.LLM_BASE_URL).strip()
        self.model = (model or settings.LLM_MODEL).strip()
        self.role = role
        if not self.api_key:
            log.warning("llm.client.no_api_key", role=role)
        log.info(
            "llm.client.init",
            role=role,
            base_url=self.base_url,
            model=self.model,
            key_fingerprint=_fingerprint(self.api_key),
        )
        self.client = OpenAI(
            api_key=self.api_key or "missing",
            base_url=self.base_url,
            timeout=_LLM_TIMEOUT,
            max_retries=0,
        )

    def _log_token_usage(self, usage, schema_name: str, *, path: str) -> None:
        usage_dump = usage.model_dump() if usage else {}
        prompt = int(usage_dump.get("prompt_tokens") or 0)
        completion = int(usage_dump.get("completion_tokens") or 0)
        total = int(usage_dump.get("total_tokens") or prompt + completion)
        log.info(
            "llm.response",
            schema=schema_name,
            path=path,
            usage=usage_dump or None,
        )
        log.info(
            "llm.tokens",
            role=self.role,
            model=self.model,
            schema=schema_name,
            path=path,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable_llm),
        reraise=True,
    )
    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[T],
        temperature: float = 0.3,
    ) -> T:
        log.info(
            "llm.request",
            role=self.role,
            model=self.model,
            schema=schema.__name__,
            system_chars=len(system),
            user_chars=len(user),
        )
        log.debug(
            "llm.request.body",
            role=self.role,
            model=self.model,
            schema=schema.__name__,
            system_preview=system[:500],
            user_preview=user[:4000],
        )

        # Some gateways/models do not support beta.parse, so fall back to
        # json_object plus validation against the same Pydantic schema.
        try:
            resp = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                response_format=schema,
            )
            parsed = resp.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("LLM returned None for parsed output")
            self._log_token_usage(resp.usage, schema.__name__, path="parse")
            log.debug(
                "llm.response.body",
                role=self.role,
                model=self.model,
                schema=schema.__name__,
                parsed=parsed.model_dump(),
            )
            return parsed
        except Exception as parse_err:
            log.warning(
                "llm.parse_unsupported.fallback_json_object",
                error=str(parse_err)[:300],
            )

        schema_hint = (
            "Reply STRICTLY as JSON matching this Pydantic schema (no prose):\n"
            f"{schema.model_json_schema()}"
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": f"{system}\n\n{schema_hint}"},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        parsed = schema.model_validate_json(content)
        self._log_token_usage(resp.usage, schema.__name__, path="json_object")
        log.debug(
            "llm.response.body",
            role=self.role,
            model=self.model,
            schema=schema.__name__,
            parsed=parsed.model_dump(),
        )
        return parsed
