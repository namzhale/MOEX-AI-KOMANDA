from __future__ import annotations

import json

import pytest

from agent.runtime.short_check import ShortCheckRunner


class _Arenago:
    def __init__(self, positions: list[dict] | None = None) -> None:
        self._positions = positions or []
        self.submitted: list[dict] = []

    def get_portfolio(self) -> dict:
        return {"bot": "test", "cash": 100_000, "positions": self._positions}

    def submit_order(self, secid, direction, quantity):
        payload = {"secid": secid, "direction": direction, "quantity": quantity}
        self.submitted.append(payload)
        return {"success": True, "order": payload}


@pytest.mark.asyncio
async def test_short_check_sells_waits_and_buys_once(tmp_path) -> None:
    arenago = _Arenago()
    runner = ShortCheckRunner(
        arenago=arenago,
        data_dir=str(tmp_path),
        secid="SBER",
        quantity=1,
        delay_seconds=0,
    )

    await runner.run()
    await runner.run()

    assert arenago.submitted == [
        {"secid": "SBER", "direction": "S", "quantity": 1},
        {"secid": "SBER", "direction": "B", "quantity": 1},
    ]
    state = json.loads((tmp_path / "short_check_state.json").read_text())
    assert state["status"] == "done"


@pytest.mark.asyncio
async def test_short_check_resumes_cover_after_sell(tmp_path) -> None:
    (tmp_path / "short_check_state.json").write_text(
        json.dumps(
            {
                "status": "sell_submitted",
                "secid": "SBER",
                "quantity": 1,
                "sold_at": "2026-05-18T14:00:00+00:00",
            }
        )
    )
    arenago = _Arenago()
    runner = ShortCheckRunner(
        arenago=arenago,
        data_dir=str(tmp_path),
        secid="SBER",
        quantity=1,
        delay_seconds=0,
    )

    await runner.run()

    assert arenago.submitted == [
        {"secid": "SBER", "direction": "B", "quantity": 1},
    ]


@pytest.mark.asyncio
async def test_short_check_auto_selects_ticker_not_in_portfolio(tmp_path) -> None:
    arenago = _Arenago(
        positions=[
            {"secid": "SBER", "position": 1},
            {"secid": "GAZP", "position": 1},
        ]
    )
    runner = ShortCheckRunner(
        arenago=arenago,
        data_dir=str(tmp_path),
        secid="AUTO",
        candidates=("SBER", "GAZP", "LKOH"),
        quantity=1,
        delay_seconds=0,
    )

    await runner.run()

    assert arenago.submitted == [
        {"secid": "LKOH", "direction": "S", "quantity": 1},
        {"secid": "LKOH", "direction": "B", "quantity": 1},
    ]
    assert runner.read_state()["secid"] == "LKOH"
