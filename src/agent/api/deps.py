from __future__ import annotations

from fastapi import Header, HTTPException

from agent.config import settings


def require_api_token(x_api_token: str | None = Header(default=None, alias="X-API-Token")) -> None:
    """Защита опасных ручек (/scheduler/tick). Пустой API_TOKEN → ручка отключена."""
    expected = (settings.API_TOKEN or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="API_TOKEN not configured; endpoint disabled",
        )
    if not x_api_token or x_api_token.strip() != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Token")
