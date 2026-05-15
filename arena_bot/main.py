import logging
import time

from arena_bot.client import ArenagoClient
from arena_bot.config import BotConfig, load_env_file
from arena_bot.runner import run_once
from arena_bot.state import FileStateStore


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    configure_logging()
    load_env_file()
    config = BotConfig.from_env()
    state_store = FileStateStore(config.state_path)
    client = ArenagoClient(config.api_base_url, config.api_token or "")

    while True:
        try:
            result = run_once(config, client, state_store)
            logging.info("Cycle finished with status=%s reason=%s", result.status, result.reason)
        except Exception:
            logging.exception("Cycle failed; sleeping %s seconds before retry", config.error_sleep_seconds)
            if config.loop_forever:
                time.sleep(config.error_sleep_seconds)
                continue

        if not config.loop_forever:
            break

        time.sleep(60)


if __name__ == "__main__":
    main()
