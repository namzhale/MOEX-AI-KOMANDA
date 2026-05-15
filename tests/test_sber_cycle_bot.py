from datetime import datetime, timedelta, timezone

from arena_bot.config import BotConfig
from arena_bot.runner import run_once
from arena_bot.state import BotState, MemoryStateStore


class RecordingClient:
    def __init__(self):
        self.orders = []

    def submit_order(self, direction, secid, quantity, bot):
        self.orders.append(
            {
                "direction": direction,
                "secid": secid,
                "quantity": quantity,
                "bot": bot,
            }
        )
        return {"success": True, "price": 100, "quantity": quantity}


def test_dry_run_first_cycle_prepares_buy_without_http_call():
    config = BotConfig(
        api_token=None,
        bot_name="Team24Bot",
        dry_run=True,
        quantity=1,
        interval_hours=12,
        state_path="/tmp/state.json",
    )
    store = MemoryStateStore(BotState())
    client = RecordingClient()
    now = datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)

    result = run_once(config, client, store, now=now)

    assert result.status == "dry_run"
    assert result.intent.direction == "B"
    assert result.intent.secid == "SBER"
    assert client.orders == []
    assert store.load().last_direction == "B"
    assert store.load().last_order_at == now


def test_cycle_skips_until_twelve_hours_pass():
    config = BotConfig(
        api_token="token",
        bot_name="Team24Bot",
        dry_run=False,
        quantity=1,
        interval_hours=12,
        state_path="/tmp/state.json",
    )
    last_order_at = datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)
    store = MemoryStateStore(BotState(last_direction="B", last_order_at=last_order_at))
    client = RecordingClient()

    result = run_once(config, client, store, now=last_order_at + timedelta(hours=11, minutes=59))

    assert result.status == "skipped"
    assert result.reason == "interval_not_elapsed"
    assert client.orders == []
    assert store.load().last_direction == "B"


def test_live_cycle_sells_after_previous_buy_and_records_success():
    config = BotConfig(
        api_token="token",
        bot_name="Team24Bot",
        dry_run=False,
        quantity=2,
        interval_hours=12,
        state_path="/tmp/state.json",
    )
    last_order_at = datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)
    now = last_order_at + timedelta(hours=12)
    store = MemoryStateStore(BotState(last_direction="B", last_order_at=last_order_at))
    client = RecordingClient()

    result = run_once(config, client, store, now=now)

    assert result.status == "submitted"
    assert result.intent.direction == "S"
    assert client.orders == [
        {
            "direction": "S",
            "secid": "SBER",
            "quantity": 2,
            "bot": "Team24Bot",
        }
    ]
    assert store.load().last_direction == "S"
    assert store.load().last_order_at == now


def test_live_cycle_requires_api_token_before_http_call():
    config = BotConfig(
        api_token=None,
        bot_name="Team24Bot",
        dry_run=False,
        quantity=1,
        interval_hours=12,
        state_path="/tmp/state.json",
    )
    store = MemoryStateStore(BotState())
    client = RecordingClient()

    result = run_once(config, client, store, now=datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc))

    assert result.status == "blocked"
    assert result.reason == "missing_api_token"
    assert client.orders == []
    assert store.load().last_direction is None
