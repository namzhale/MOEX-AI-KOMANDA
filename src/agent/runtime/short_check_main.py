from __future__ import annotations

import asyncio

import structlog

from agent.config import settings
from agent.data.arenago import ArenaGoClient
from agent.logging import configure_logging
from agent.runtime.short_check import ShortCheckRunner
from agent.runtime.universe import parse_universe
from agent.webhook import get_webhook_notifier

log = structlog.get_logger()


async def amain() -> None:
    configure_logging(
        settings.LOG_LEVEL,
        fmt=settings.LOG_FORMAT,
        webhook_url=settings.LOG_WEBHOOK_URL,
        webhook_events=settings.LOG_WEBHOOK_EVENTS,
        webhook_timeout_seconds=settings.LOG_WEBHOOK_TIMEOUT_SECONDS,
        webhook_max_queue=settings.LOG_WEBHOOK_MAX_QUEUE,
    )
    log.info(
        "short_check.app_start",
        dry_run=settings.DRY_RUN,
        bot=settings.ARENAGO_BOT,
        secid=settings.SHORT_CHECK_SECID,
        quantity=settings.SHORT_CHECK_QUANTITY,
        delay_seconds=settings.SHORT_CHECK_DELAY_SECONDS,
    )
    webhook = get_webhook_notifier()
    webhook.send("short_check_startup", {
        "secid": settings.SHORT_CHECK_SECID,
        "dry_run": settings.DRY_RUN,
    })

    arenago = ArenaGoClient()
    try:
        runner = ShortCheckRunner(
            arenago=arenago,
            data_dir=settings.DATA_DIR,
            secid=settings.SHORT_CHECK_SECID,
            candidates=parse_universe(settings.SHORT_CHECK_CANDIDATES),
            quantity=settings.SHORT_CHECK_QUANTITY,
            delay_seconds=settings.SHORT_CHECK_DELAY_SECONDS,
        )
        await runner.run()
        state = runner.read_state()
        webhook.send("short_check_finished", {"status": state.get("status"), "secid": state.get("secid")})
    except Exception as e:
        webhook.send("short_check_failed", {"error": str(e)[:300]})
        raise
    finally:
        arenago.close()
        log.info("short_check.app_done")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
