from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from agent.runtime import reflection as refl_mod


def test_should_run_meta_after_session(mocker) -> None:
    mocker.patch.object(refl_mod.settings, "META_REFLECTION_ENABLED", True)
    mocker.patch.object(refl_mod, "_meta_already_ran_today", return_value=False)
    MSK = timezone(timedelta(hours=3))
    at = datetime(2026, 5, 20, 19, 0, tzinfo=MSK)
    assert refl_mod.should_run_meta_reflection(at) is True


def test_should_not_run_meta_during_session(mocker) -> None:
    mocker.patch.object(refl_mod.settings, "META_REFLECTION_ENABLED", True)
    mocker.patch.object(refl_mod, "_meta_already_ran_today", return_value=False)
    MSK = timezone(timedelta(hours=3))
    at = datetime(2026, 5, 20, 12, 0, tzinfo=MSK)
    assert refl_mod.should_run_meta_reflection(at) is False
