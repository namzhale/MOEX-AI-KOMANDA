from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from agent.data.arenago import ArenaGoClient
from agent.runtime.universe import parse_universe

log = structlog.get_logger()


class ShortCheckRunner:
    """One-shot ArenaGo short capability check.

    Submits one SELL, waits, then one BUY. State on disk survives pod restarts.
    """

    def __init__(
        self,
        arenago: ArenaGoClient,
        data_dir: str,
        secid: str = "SBER",
        candidates: tuple[str, ...] = (),
        quantity: int = 1,
        delay_seconds: int = 300,
    ) -> None:
        self.arenago = arenago
        self.secid = secid.strip().upper()
        self.candidates = tuple(c.strip().upper() for c in candidates if c.strip())
        self.quantity = max(int(quantity), 1)
        self.delay_seconds = max(int(delay_seconds), 0)
        self.state_path = Path(data_dir) / "short_check_state.json"
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self.run(), name="short-check-runner")
        log.info(
            "short_check.start",
            secid=self.secid,
            quantity=self.quantity,
            delay_seconds=self.delay_seconds,
            state_path=str(self.state_path),
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

    async def run(self) -> None:
        state = self._read_state()
        status = state.get("status")
        if status == "done":
            log.info("short_check.skip", reason="already_done")
            return

        if status == "sell_submitted":
            await self._wait_then_cover(state)
            return

        if status in {"sell_started", "buy_started", "sell_rejected", "buy_rejected"}:
            log.warning("short_check.skip", reason=f"terminal_or_uncertain_state:{status}")
            return

        secid = await self._resolve_secid()
        if not secid:
            self._write_state(
                {
                    "status": "no_candidate",
                    "requested_secid": self.secid,
                    "candidates": list(self._candidate_universe()),
                    "checked_at": _utc_now(),
                }
            )
            log.warning("short_check.no_candidate")
            return

        await self._submit_sell(secid)
        state = self._read_state()
        if state.get("status") == "sell_submitted":
            await self._wait_then_cover(state)

    async def _resolve_secid(self) -> str | None:
        if self.secid != "AUTO":
            return self.secid

        portfolio = await asyncio.to_thread(self.arenago.get_portfolio)
        positions = portfolio.get("positions", []) or []
        held = {
            str(p.get("secid") or "").strip().upper()
            for p in positions
            if float(p.get("position") or 0) != 0
        }
        for candidate in self._candidate_universe():
            if candidate not in held:
                log.info("short_check.auto_selected", secid=candidate, held_count=len(held))
                return candidate
        return None

    def _candidate_universe(self) -> tuple[str, ...]:
        return self.candidates or parse_universe("")

    async def _submit_sell(self, secid: str) -> None:
        now = _utc_now()
        self._write_state(
            {
                "status": "sell_started",
                "secid": secid,
                "quantity": self.quantity,
                "sell_started_at": now,
            }
        )
        try:
            response = await asyncio.to_thread(
                self.arenago.submit_order,
                secid,
                "S",
                self.quantity,
            )
        except Exception as e:
            self._write_state(
                {
                    "status": "sell_rejected",
                    "secid": secid,
                    "quantity": self.quantity,
                    "sell_started_at": now,
                    "error": str(e)[:300],
                }
            )
            log.exception("short_check.sell_failed", secid=secid)
            return

        sold_at = _utc_now()
        status = "sell_submitted" if response.get("success") else "sell_rejected"
        self._write_state(
            {
                "status": status,
                "secid": secid,
                "quantity": self.quantity,
                "sell_started_at": now,
                "sold_at": sold_at,
                "sell_response": response,
            }
        )
        log.info("short_check.sell_done", status=status, response=response)

    async def _wait_then_cover(self, state: dict[str, Any]) -> None:
        remaining = self._remaining_delay(state)
        if remaining > 0:
            log.info("short_check.wait_before_cover", seconds=remaining)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                return
        await self._submit_buy(state)

    async def _submit_buy(self, state: dict[str, Any]) -> None:
        secid = str(state.get("secid") or self.secid).strip().upper()
        self._write_state(
            {
                **state,
                "status": "buy_started",
                "buy_started_at": _utc_now(),
            }
        )
        try:
            response = await asyncio.to_thread(
                self.arenago.submit_order,
                secid,
                "B",
                self.quantity,
            )
        except Exception as e:
            next_state = {
                **self._read_state(),
                "status": "buy_rejected",
                "error": str(e)[:300],
            }
            self._write_state(next_state)
            log.exception("short_check.buy_failed", secid=secid)
            return

        status = "done" if response.get("success") else "buy_rejected"
        next_state = {
            **self._read_state(),
            "status": status,
            "bought_at": _utc_now(),
            "buy_response": response,
        }
        self._write_state(next_state)
        log.info("short_check.buy_done", status=status, response=response)

    def _remaining_delay(self, state: dict[str, Any]) -> float:
        sold_at_raw = state.get("sold_at")
        if not sold_at_raw:
            return float(self.delay_seconds)
        try:
            sold_at = datetime.fromisoformat(str(sold_at_raw))
        except ValueError:
            return float(self.delay_seconds)
        elapsed = (datetime.now(UTC) - sold_at.astimezone(UTC)).total_seconds()
        return max(float(self.delay_seconds) - elapsed, 0.0)

    def read_state(self) -> dict[str, Any]:
        return self._read_state()

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("short_check.state_read_failed", path=str(self.state_path))
            return {}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
