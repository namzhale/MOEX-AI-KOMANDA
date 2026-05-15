import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from arena_bot.config import BotConfig
from arena_bot.state import BotState

logger = logging.getLogger(__name__)


class TradingClient(Protocol):
    def submit_order(self, direction: str, secid: str, quantity: int, bot: str) -> dict:
        ...


class StateStore(Protocol):
    def load(self) -> BotState:
        ...

    def save(self, state: BotState) -> None:
        ...


@dataclass(frozen=True)
class OrderIntent:
    direction: str
    secid: str
    quantity: int
    bot: str


@dataclass(frozen=True)
class RunResult:
    status: str
    intent: OrderIntent | None = None
    response: dict | None = None
    reason: str | None = None


def next_direction(last_direction: str | None) -> str:
    return "S" if last_direction == "B" else "B"


def interval_elapsed(state: BotState, now: datetime, interval_hours: int) -> bool:
    if state.last_order_at is None:
        return True
    return now - state.last_order_at >= timedelta(hours=interval_hours)


def run_once(
    config: BotConfig,
    client: TradingClient,
    state_store: StateStore,
    now: datetime | None = None,
) -> RunResult:
    now = now or datetime.now(timezone.utc)
    state = state_store.load()

    if not interval_elapsed(state, now, config.interval_hours):
        logger.info("Skipping cycle: interval has not elapsed yet")
        return RunResult(status="skipped", reason="interval_not_elapsed")

    direction = next_direction(state.last_direction)
    intent = OrderIntent(
        direction=direction,
        secid=config.secid,
        quantity=config.quantity,
        bot=config.bot_name,
    )

    if config.dry_run:
        logger.info("DRY_RUN intent: %s %s x%s by %s", direction, config.secid, config.quantity, config.bot_name)
        state_store.save(BotState(last_direction=direction, last_order_at=now))
        return RunResult(status="dry_run", intent=intent)

    if not config.api_token:
        logger.error("Live trading is blocked: ARENAGO_API_KEY or SANDBOX_API_KEY is missing")
        return RunResult(status="blocked", intent=intent, reason="missing_api_token")

    response = client.submit_order(direction, config.secid, config.quantity, config.bot_name)
    logger.info("Submitted order: %s response=%s", intent, response)
    state_store.save(BotState(last_direction=direction, last_order_at=now))
    return RunResult(status="submitted", intent=intent, response=response)
