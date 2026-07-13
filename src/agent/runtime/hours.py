from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

MSK = timezone(timedelta(hours=3))

# Trading: 00:00–23:50 MSK (https://arenago.ru/api)
ARENAGO_WEEKDAY_OPEN = time(7, 0)
ARENAGO_WEEKDAY_CLOSE = time(23, 50)
ARENAGO_WEEKEND_OPEN = time(10, 0)
ARENAGO_WEEKEND_CLOSE = time(19, 0)

MOEX_MAIN_OPEN = time(10, 0)
MOEX_MAIN_CLOSE = time(18, 39, 59)


def now_msk() -> datetime:
    return datetime.now(UTC).astimezone(MSK)


def is_arenago_open(at: datetime | None = None) -> bool:
    t = (at or now_msk()).astimezone(MSK)
    if t.weekday() >= 5:
        return ARENAGO_WEEKEND_OPEN <= t.time() < ARENAGO_WEEKEND_CLOSE
    return ARENAGO_WEEKDAY_OPEN <= t.time() < ARENAGO_WEEKDAY_CLOSE


def is_moex_main_session(at: datetime | None = None) -> bool:
    t = (at or now_msk()).astimezone(MSK)
    if t.weekday() >= 5:
        return False
    return MOEX_MAIN_OPEN <= t.time() <= MOEX_MAIN_CLOSE


def is_tradable(respect_moex_hours: bool = False, at: datetime | None = None) -> bool:
    if respect_moex_hours:
        return is_moex_main_session(at)
    return is_arenago_open(at)


def is_main_session(at: datetime | None = None) -> bool:
    return is_moex_main_session(at)
