from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from agent.api.routes import router
from agent.config import settings
from agent.data.arenago import ArenaGoClient
from agent.graph.build import build_graph
from agent.logging import configure_logging
from agent.runtime.scheduler import TradingScheduler
from agent.webhook import get_webhook_notifier

log = structlog.get_logger()


def _patch_langchain_compat() -> None:
    """langchain-core 0.3.x reads langchain.debug/verbose; meta-package optional."""
    try:
        import langchain  # type: ignore[import-untyped]

        if not hasattr(langchain, "debug"):
            langchain.debug = False  # type: ignore[attr-defined]
        if not hasattr(langchain, "verbose"):
            langchain.verbose = False  # type: ignore[attr-defined]
    except ImportError:
        pass


_patch_langchain_compat()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(
        settings.LOG_LEVEL,
        fmt=settings.LOG_FORMAT,
        webhook_url=settings.LOG_WEBHOOK_URL,
        webhook_events=settings.LOG_WEBHOOK_EVENTS,
        webhook_timeout_seconds=settings.LOG_WEBHOOK_TIMEOUT_SECONDS,
        webhook_max_queue=settings.LOG_WEBHOOK_MAX_QUEUE,
    )
    log.info(
        "app.startup",
        dry_run=settings.DRY_RUN,
        llm_base_url=settings.LLM_BASE_URL,
        llm_model=settings.LLM_MODEL,
        log_level=settings.LOG_LEVEL,
        log_format=settings.LOG_FORMAT,
        agent_enabled=settings.AGENT_ENABLED,
        agent_tick_minutes=settings.AGENT_TICK_MINUTES,
        agent_mode=settings.AGENT_MODE,
    )
    get_webhook_notifier().send(
        "app_startup",
        {
            "mode": settings.AGENT_MODE,
            "dry_run": settings.DRY_RUN,
            "agent_enabled": settings.AGENT_ENABLED,
        },
    )
    if settings.AGENT_MODE.strip().lower() == "short_check":
        log.warning(
            "app.mode.short_check",
            hint="Use: python -m agent.runtime.short_check_main",
        )
    app.state.graph = build_graph()
    app.state.arenago = ArenaGoClient()
    app.state.scheduler = TradingScheduler(
        graph=app.state.graph,
        arenago=app.state.arenago,
        settings=settings,
    )
    if settings.AGENT_ENABLED:
        app.state.scheduler.start()
    else:
        log.warning("app.scheduler.disabled_by_env")
    try:
        yield
    finally:
        log.info("app.shutdown")
        try:
            await app.state.scheduler.stop()
        except Exception:
            log.exception("app.shutdown.scheduler_stop_failed")
        try:
            close = getattr(app.state.arenago, "close", None)
            if callable(close):
                close()
        except Exception:
            log.exception("app.shutdown.arenago_close_failed")


app = FastAPI(
    title="team-24 trading agent",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "team-24 trading agent", "version": "0.2.0", "status": "ok"}
