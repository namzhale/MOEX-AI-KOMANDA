"""Расписание: ArenaGo competition window and strict MOEX mode."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.runtime.hours import (
    MSK,
    is_arenago_open,
    is_main_session,
    is_moex_main_session,
    is_tradable,
)


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=MSK)


# ── ArenaGo competition window, MSK ──────────────────────────────────────────


def test_arenago_weekday_window_msk() -> None:
    assert not is_arenago_open(at(2026, 5, 18, 6, 59))  # Monday before open
    assert is_arenago_open(at(2026, 5, 18, 7, 0))
    assert is_arenago_open(at(2026, 5, 18, 23, 49))
    assert not is_arenago_open(at(2026, 5, 18, 23, 50))


def test_arenago_weekend_window_msk() -> None:
    assert not is_arenago_open(at(2026, 5, 16, 9, 59))  # Saturday before open
    assert is_arenago_open(at(2026, 5, 16, 10, 0))
    assert is_arenago_open(at(2026, 5, 16, 18, 59))
    assert not is_arenago_open(at(2026, 5, 16, 19, 0))


# ── Строгий MOEX (10:00–18:39 пн–пт) ─────────────────────────────────────────


def test_moex_main_session_open_window() -> None:
    assert is_moex_main_session(at(2026, 5, 18, 10, 0))
    assert is_moex_main_session(at(2026, 5, 18, 12, 30))


def test_moex_main_session_closed_weekend() -> None:
    assert not is_moex_main_session(at(2026, 5, 16, 12, 0))  # сб
    assert not is_moex_main_session(at(2026, 5, 17, 12, 0))  # вс


def test_moex_main_session_closed_outside_hours() -> None:
    assert not is_moex_main_session(at(2026, 5, 18, 9, 59))   # до открытия
    assert not is_moex_main_session(at(2026, 5, 18, 22, 0))   # вечер


# ── Политика is_tradable ─────────────────────────────────────────────────────


def test_is_tradable_default_uses_arenago() -> None:
    # Saturday noon: ArenaGo competition is open, MOEX main session is closed.
    sat = at(2026, 5, 16, 12, 0)
    assert is_tradable(respect_moex_hours=False, at=sat)
    assert not is_tradable(respect_moex_hours=True, at=sat)


def test_is_tradable_strict_blocks_evening() -> None:
    # Monday evening: ArenaGo competition is open, MOEX main session is closed.
    evening = at(2026, 5, 18, 21, 0)
    assert is_tradable(respect_moex_hours=False, at=evening)
    assert not is_tradable(respect_moex_hours=True, at=evening)


# ── Алиас is_main_session для обратной совместимости ────────────────────────


def test_legacy_alias_still_works() -> None:
    assert is_main_session(at(2026, 5, 18, 12, 0))
    assert not is_main_session(at(2026, 5, 16, 12, 0))


# ── TZ-independence ─────────────────────────────────────────────────────────


def test_timezone_normalized_to_msk() -> None:
    utc = timezone.utc
    # 07:00 UTC == 10:00 MSK, MOEX только открылась
    dt_utc = datetime(2026, 5, 18, 7, 0, tzinfo=utc)
    assert is_moex_main_session(dt_utc)
    # ArenaGo competition is open too.
    assert is_arenago_open(dt_utc)
    # Чуть раньше — MOEX ещё закрыта
    dt_before = dt_utc - timedelta(seconds=1)
    assert not is_moex_main_session(dt_before)
