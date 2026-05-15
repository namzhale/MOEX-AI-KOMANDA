import logging
import time
from pathlib import Path

from arena_bot.client import ArenagoClient
from arena_bot.config import BotConfig, load_env_file
from arena_bot.runner import run_once
from arena_bot.state import FileStateStore
from arena_bot.webhook import WebhookNotifier


def configure_logging(log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def main() -> None:
    load_env_file()
    config = BotConfig.from_env()
    configure_logging(config.log_file)
    state_store = FileStateStore(config.state_path)
    client = ArenagoClient(config.api_base_url, config.api_token or "")
    notifier = WebhookNotifier(config.log_webhook_url, config.bot_name)
    notifier.send(
        "startup",
        {
            "dry_run": config.dry_run,
            "secid": config.secid,
            "quantity": config.quantity,
            "interval_hours": config.interval_hours,
            "state_path": config.state_path,
        },
    )

    while True:
        try:
            result = run_once(config, client, state_store)
            logging.info("Cycle finished with status=%s reason=%s", result.status, result.reason)
            notifier.send(
                "cycle_finished",
                {
                    "status": result.status,
                    "reason": result.reason,
                    "direction": result.intent.direction if result.intent else None,
                    "secid": result.intent.secid if result.intent else None,
                    "quantity": result.intent.quantity if result.intent else None,
                    "response": result.response,
                },
            )
        except Exception:
            logging.exception("Cycle failed; sleeping %s seconds before retry", config.error_sleep_seconds)
            notifier.send(
                "cycle_failed",
                {
                    "error_sleep_seconds": config.error_sleep_seconds,
                },
            )
            if config.loop_forever:
                time.sleep(config.error_sleep_seconds)
                continue

        if not config.loop_forever:
            break

        time.sleep(60)


if __name__ == "__main__":
    main()
