from __future__ import annotations

import structlog
from fastapi import APIRouter

log = structlog.get_logger()
router = APIRouter()


# Только /health — нужен k8s liveness/readiness-пробам. Остальные ручки
# (/decide, /portfolio, /scheduler/*, /reflection/*, /short-check/*) намеренно
# удалены: внешнего доступа к ним нет, а мутирующие (/scheduler/tick,
# /reflection/meta) при утечке токена позволяли бы триггерить реальные сделки.
# Бот торгует автономно через фоновый scheduler (стартует в lifespan), HTTP
# для этого не нужен.
@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
