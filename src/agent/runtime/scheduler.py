from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING

import structlog

from agent.data import market as market_data
from agent.data.arenago import ArenaGoClient
from agent.data.moex_lots import fetch_lot_sizes
from agent.llm.billing import PolzaBillingClient
from agent.runtime import risk as risk_mod
from agent.runtime.hours import MSK, is_tradable, now_msk
from agent.runtime.journal import JsonlJournal
from agent.runtime.risk import (
    NAV_CALC_VERSION,
    RiskContext,
    load_nav_history,
    log_returns,
)
from agent.runtime.reflection import is_closing_operation, reflect_on_trade, run_meta_reflection
from agent.webhook import get_webhook_notifier
from agent.runtime.sizing import position_for
from agent.runtime.universe import LOT_SIZE_BY_TICKER, parse_universe
from agent.data.microstructure import (
    fetch_universe_liquidity,
    flow_enabled,
    mega_alert_symbols_today,
)
from agent.runtime.universe_select import select_llm_tickers
from agent.schemas import AnalystOutput, Decision

if TYPE_CHECKING:
    from agent.config import Settings

log = structlog.get_logger()


@dataclass
class TickRecord:
    started_at: datetime
    finished_at: datetime | None = None
    universe: tuple[str, ...] = ()
    decisions: dict[str, dict] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    skipped_reason: str | None = None
    nav: float = 0.0
    cash: float = 0.0


class SchedulerStatus:
    def __init__(self) -> None:
        self.started_at: datetime | None = None
        self.last_tick: TickRecord | None = None
        self.tick_count: int = 0
        self.running: bool = False

    def to_dict(self) -> dict:
        lt = self.last_tick
        return {
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "tick_count": self.tick_count,
            "last_tick": (
                {
                    "started_at": lt.started_at.isoformat(),
                    "finished_at": lt.finished_at.isoformat() if lt.finished_at else None,
                    "universe": list(lt.universe),
                    "decisions": lt.decisions,
                    "errors": lt.errors,
                    "skipped_reason": lt.skipped_reason,
                    "nav": lt.nav,
                    "cash": lt.cash,
                }
                if lt
                else None
            ),
        }


class TradingScheduler:
    def __init__(
        self,
        graph,
        arenago: ArenaGoClient,
        settings: "Settings",
        journal: JsonlJournal | None = None,
        billing_client: PolzaBillingClient | None = None,
    ) -> None:
        self.graph = graph
        self.arenago = arenago
        self.settings = settings
        self.universe = parse_universe(settings.AGENT_TICKERS)
        # Lot sizes: захардкоженная таблица как baseline, поверх — актуальные из MOEX ISS.
        # ArenaGo трактует quantity в submit_order как ЛОТЫ, реальный объём =
        # quantity × lot_size. Точное значение lot_size критично для расчётов.
        self.lot_sizes = dict(LOT_SIZE_BY_TICKER)
        self._refresh_lot_sizes_from_moex()
        self.tick_seconds = max(settings.AGENT_TICK_MINUTES, 1) * 60
        self.candle_interval = settings.AGENT_INTERVAL
        self.respect_moex_hours = settings.AGENT_RESPECT_MOEX_HOURS
        self.commission_rate = risk_mod.commission_rate_for(settings)
        self.daily_trade_limit = max(
            int(getattr(settings, "ARENAGO_DAILY_TRADE_LIMIT", 200) or 0),
            0,
        )
        self.max_concurrent_tickers = max(
            int(getattr(settings, "AGENT_MAX_CONCURRENT_TICKERS", 8)), 1
        )
        self.journal = journal or JsonlJournal(
            f"{settings.DATA_DIR.rstrip('/')}/decisions.jsonl"
        )
        self.polza_billing = billing_client or self._make_polza_billing_client()
        self._polza_balance_depleted_at: datetime | None = (
            self._load_polza_balance_depleted_at()
        )
        self.status = SchedulerStatus()
        # Anti-paralysis watchdog: считаем тики подряд без ордеров, чтобы в
        # Loki было видно «бот замолчал N часов» — не auto-fix (опасно), но
        # observability важна для post-mortem.
        self.consecutive_idle_ticks: int = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def _make_polza_billing_client(self) -> PolzaBillingClient | None:
        if not bool(getattr(self.settings, "POLZA_BALANCE_FAILSAFE_ENABLED", False)):
            return None
        base_url = str(getattr(self.settings, "LLM_BASE_URL", "") or "").strip()
        api_key = str(getattr(self.settings, "POLZA_API_KEY", "") or "").strip()
        if "polza.ai" not in base_url.lower():
            log.info(
                "polza.balance_failsafe.disabled_non_polza_base_url",
                base_url=base_url,
            )
            return None
        if not api_key:
            log.warning("polza.balance_failsafe.disabled_no_api_key")
            return None
        return PolzaBillingClient(
            api_key=api_key,
            base_url=base_url,
            timeout=float(
                getattr(self.settings, "POLZA_BALANCE_TIMEOUT_SECONDS", 5.0) or 5.0
            ),
        )

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _load_polza_balance_depleted_at(self) -> datetime | None:
        for rec in reversed(self.journal.tail(2000)):
            event = rec.get("event")
            if event == "polza_balance_recovered":
                return None
            if event == "polza_balance_depleted":
                return self._parse_datetime(rec.get("depleted_at") or rec.get("ts"))
        return None

    async def _handle_polza_balance_failsafe(
        self,
        rec: "TickRecord",
        positions: list[dict],
    ) -> bool:
        if not bool(getattr(self.settings, "POLZA_BALANCE_FAILSAFE_ENABLED", False)):
            return False
        if self.polza_billing is None:
            return False

        try:
            balance = await asyncio.to_thread(self.polza_billing.get_balance_amount)
        except Exception as e:
            log.warning("polza.balance_check.failed", error=str(e)[:200])
            return False

        min_balance = float(
            getattr(self.settings, "POLZA_BALANCE_MIN_RUB", 0.01) or 0.01
        )
        now = self._now_utc()
        if balance > min_balance:
            if self._polza_balance_depleted_at is not None:
                self.journal.write(
                    "polza_balance_recovered",
                    balance=balance,
                    min_balance=min_balance,
                )
                log.info(
                    "polza.balance.recovered",
                    balance=balance,
                    min_balance=min_balance,
                )
            self._polza_balance_depleted_at = None
            return False

        if self._polza_balance_depleted_at is None:
            self._polza_balance_depleted_at = now
            self.journal.write(
                "polza_balance_depleted",
                balance=balance,
                min_balance=min_balance,
                depleted_at=now.isoformat(),
            )
            log.error(
                "polza.balance.depleted",
                balance=balance,
                min_balance=min_balance,
            )

        grace = timedelta(
            minutes=max(
                int(getattr(self.settings, "POLZA_BALANCE_GRACE_MINUTES", 30) or 30),
                0,
            )
        )
        elapsed = now - self._polza_balance_depleted_at
        if elapsed < grace:
            rec.skipped_reason = "polza_balance_depleted_grace"
            rec.finished_at = now
            self.journal.write(
                "tick_skipped",
                n=self.status.tick_count,
                reason=rec.skipped_reason,
                polza_balance=balance,
                depleted_at=self._polza_balance_depleted_at.isoformat(),
                grace_minutes=grace.total_seconds() / 60,
                elapsed_seconds=elapsed.total_seconds(),
            )
            log.error(
                "scheduler.tick.skipped",
                reason=rec.skipped_reason,
                polza_balance=balance,
                depleted_at=self._polza_balance_depleted_at.isoformat(),
                grace_minutes=grace.total_seconds() / 60,
                elapsed_seconds=round(elapsed.total_seconds(), 1),
            )
            return True

        decisions = await asyncio.to_thread(
            self._close_positions_for_polza_failsafe,
            positions,
        )
        rec.decisions.update(decisions)
        rec.skipped_reason = (
            "polza_balance_failsafe_closed_positions"
            if decisions else
            "polza_balance_failsafe_no_positions"
        )
        rec.finished_at = now
        rejected = sum(
            1
            for summary in decisions.values()
            if (summary.get("action") or {}).get("status") == "order_rejected"
        )
        self.journal.write(
            "polza_balance_failsafe_close_all",
            n=self.status.tick_count,
            balance=balance,
            depleted_at=self._polza_balance_depleted_at.isoformat(),
            elapsed_seconds=elapsed.total_seconds(),
            positions=len(decisions),
            rejected=rejected,
            decisions=decisions,
        )
        log.error(
            "polza.balance_failsafe.close_all",
            balance=balance,
            positions=len(decisions),
            rejected=rejected,
            elapsed_seconds=round(elapsed.total_seconds(), 1),
        )
        return True

    def _close_positions_for_polza_failsafe(
        self,
        positions: list[dict],
    ) -> dict[str, dict]:
        decisions: dict[str, dict] = {}
        for position in positions:
            ticker = str(position.get("secid") or "").upper()
            try:
                signed_qty = int(float(position.get("position") or 0))
            except (TypeError, ValueError):
                continue
            qty = abs(signed_qty)
            if not ticker or qty <= 0:
                continue

            direction = "S" if signed_qty > 0 else "B"
            op_type = (
                "polza_failsafe_close_long"
                if signed_qty > 0 else
                "polza_failsafe_cover_short"
            )
            try:
                response = self.arenago.submit_order(ticker, direction, quantity=qty)
            except Exception as e:
                response = {"success": False, "error": str(e)[:300]}
            status = (
                "sell_submitted"
                if direction == "S" and response.get("success")
                else "buy_submitted"
                if direction == "B" and response.get("success")
                else "order_rejected"
            )
            decisions[ticker] = {
                "signal": "FAILSAFE_CLOSE",
                "size_pct": 0.0,
                "confidence": 1.0,
                "reasoning": "Polza API balance depleted; closing all positions after grace period.",
                "last_price": 0.0,
                "graph_ms": 0,
                "action": {
                    "status": status,
                    "op_type": op_type,
                    "qty": qty,
                    "response": response,
                },
            }
        return decisions

    def _refresh_lot_sizes_from_moex(self) -> None:
        """Подтягивает актуальные LOTSIZE из MOEX ISS — источник правды.

        Захардкоженная `LOT_SIZE_BY_TICKER` — fallback только если ISS
        недоступен. Все эмпирические наблюдения (GMKN=10, SNGSP=10) совпадают
        с ISS — значит ArenaGo использует те же lot_size'ы что и MOEX.

        Override применяем сразу. Расхождения с baseline логируем для
        visibility в Loki, чтобы видеть когда биржа меняет лот.
        """
        try:
            fresh = fetch_lot_sizes(self.universe)
        except Exception:
            log.exception("scheduler.lot_sizes.fetch_failed_using_baseline")
            return
        if not fresh:
            log.warning(
                "scheduler.lot_sizes.empty_iss_using_baseline",
                baseline=self.lot_sizes,
            )
            return
        diffs = {
            sym: {"baseline": self.lot_sizes.get(sym), "moex_iss": v}
            for sym, v in fresh.items()
            if self.lot_sizes.get(sym) != v
        }
        if diffs:
            log.warning("scheduler.lot_sizes.iss_baseline_drift", diffs=diffs)
        # ISS — источник правды; override baseline.
        self.lot_sizes.update(fresh)
        log.info("scheduler.lot_sizes.in_use", sizes=self.lot_sizes)

    def start(self) -> None:
        if self._task is not None:
            return
        self.status.started_at = datetime.now(UTC)
        self.status.running = True
        self._task = asyncio.create_task(self._loop(), name="trading-scheduler")
        log.info(
            "scheduler.start",
            tick_minutes=self.settings.AGENT_TICK_MINUTES,
            interval=self.candle_interval,
            universe_size=len(self.universe),
            dry_run=self.settings.DRY_RUN,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            # Жёсткое окно: если таска торчит в blocking-httpx, не блокируем
            # lifespan-shutdown больше 3 секунд — K8s всё равно SIGKILL'нёт по grace.
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        self.status.running = False
        log.info("scheduler.stop")

    async def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                tick_started = time.monotonic()
                try:
                    await self.run_once()
                except Exception:
                    log.exception("scheduler.tick.unhandled")
                sleep_seconds = self._seconds_until_next_tick(tick_started)
                if sleep_seconds <= 0:
                    continue
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=sleep_seconds)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    def _seconds_until_next_tick(self, tick_started_monotonic: float) -> float:
        elapsed = max(time.monotonic() - tick_started_monotonic, 0.0)
        return max(self.tick_seconds - elapsed, 0.0)

    async def run_once(self, force: bool = False) -> TickRecord:
        rec = TickRecord(started_at=datetime.now(UTC), universe=self.universe)
        self.status.last_tick = rec
        self.status.tick_count += 1
        # tick_n будет добавлен ко всем log-events внутри этого тика
        # (через merge_contextvars). Snimaem v finally ниже.
        structlog.contextvars.bind_contextvars(tick_n=self.status.tick_count)
        try:
            return await self._run_once_inner(rec, force)
        finally:
            structlog.contextvars.unbind_contextvars("tick_n")

    async def _run_once_inner(self, rec: "TickRecord", force: bool) -> "TickRecord":
        log.info(
            "scheduler.tick.start",
            n=self.status.tick_count,
            msk=now_msk().isoformat(),
            force=force,
            respect_moex_hours=self.respect_moex_hours,
        )

        if not force and not is_tradable(respect_moex_hours=self.respect_moex_hours):
            rec.skipped_reason = (
                "outside_moex_main_session"
                if self.respect_moex_hours
                else "outside_arenago_window"
            )
            rec.finished_at = datetime.now(UTC)
            self.journal.write("tick_skipped", n=self.status.tick_count, reason=rec.skipped_reason)
            log.info("scheduler.tick.skipped", reason=rec.skipped_reason)
            return rec

        try:
            portfolio = await asyncio.to_thread(self.arenago.get_portfolio)
        except Exception as e:
            rec.skipped_reason = f"portfolio_unavailable: {str(e)[:200]}"
            rec.finished_at = datetime.now(UTC)
            self.journal.write("tick_skipped", n=self.status.tick_count, reason=rec.skipped_reason)
            log.warning("scheduler.tick.skipped", reason=rec.skipped_reason)
            return rec

        cash = float(portfolio.get("cash", 0))
        positions = portfolio.get("positions", []) or []
        rec.cash = cash
        log.info("scheduler.tick.portfolio", cash=cash, positions=len(positions))

        if await self._handle_polza_balance_failsafe(rec, positions):
            return rec

        # /api/trades/{bot} возвращает только сделки за сегодня — len() ок.
        try:
            daily_trade_count = await asyncio.to_thread(self._daily_trade_count)
        except Exception as e:
            rec.skipped_reason = f"trades_unavailable: {str(e)[:200]}"
            rec.finished_at = datetime.now(UTC)
            self.journal.write("tick_skipped", n=self.status.tick_count, reason=rec.skipped_reason)
            log.warning("scheduler.tick.skipped", reason=rec.skipped_reason)
            return rec

        log.info(
            "scheduler.tick.daily_trades",
            count=daily_trade_count,
            limit=self.daily_trade_limit,
        )
        if self.daily_trade_limit > 0 and daily_trade_count >= self.daily_trade_limit:
            rec.skipped_reason = (
                f"daily_trade_limit_reached: {daily_trade_count}/{self.daily_trade_limit}"
            )
            rec.finished_at = datetime.now(UTC)
            self.journal.write("tick_skipped", n=self.status.tick_count, reason=rec.skipped_reason)
            log.info("scheduler.tick.skipped", reason=rec.skipped_reason)
            return rec

        lot_sizes = self._lot_sizes_for(positions)
        portfolio_prices = self._latest_prices_for_positions(positions, {})
        portfolio_nav = self._portfolio_nav(cash, positions, portfolio_prices, lot_sizes)
        log.info(
            "scheduler.tick.portfolio_context",
            nav=portfolio_nav,
            cash=cash,
            positions=len(positions),
        )
        market_context = await asyncio.to_thread(self._market_context)
        if market_context:
            log.info("scheduler.tick.market_context", **market_context)

        llm_cap = max(int(getattr(self.settings, "AGENT_LLM_TICKERS_PER_TICK", 0) or 0), 0)
        liquidity = (
            fetch_universe_liquidity(self.universe) if flow_enabled() else None
        )
        alert_syms = (
            mega_alert_symbols_today(self.universe)
            if flow_enabled() and self.settings.ALGOPACK_MEGA_ALERT_SKIP
            else None
        )
        llm_universe = select_llm_tickers(
            self.universe,
            positions,
            max_per_tick=llm_cap,
            liquidity_scores=liquidity,
            alert_symbols=alert_syms,
        )
        skipped_llm = [t for t in self.universe if t not in llm_universe]
        if skipped_llm:
            log.info(
                "scheduler.phase1.llm_universe_capped",
                cap=llm_cap or len(self.universe),
                llm_tickers=len(llm_universe),
                skipped=len(skipped_llm),
                skipped_sample=skipped_llm[:5],
            )

        # Phase 1 — параллельная LLM-фаза. Каждый тикер прогоняется через граф
        # (analyst → news → debate → trader) независимо, без портфельных мутаций.
        # Semaphore ограничивает одновременные обращения к polza.ai (RPS-limit).
        sem = asyncio.Semaphore(self.max_concurrent_tickers)

        per_ticker_timeout = max(
            float(getattr(self.settings, "AGENT_TICKER_TIMEOUT_SEC", 360.0) or 360.0),
            360.0,
        )

        async def _evaluate(ticker: str):
            async with sem:
                # contextvars изолированы между coroutine'ами в asyncio.gather
                # (каждая корутина — свой Task → свой context). bound_contextvars
                # автоматически добавит {"symbol": ticker} ко ВСЕМ log-events
                # внутри обработки этого тикера (через merge_contextvars в
                # structlog-конфиге). Включая news.fetch, llm.request, и т.д.
                with structlog.contextvars.bound_contextvars(symbol=ticker):
                    # position_for — в ЛОТАХ (как в /api/positions). Трейдеру
                    # показываем в АКЦИЯХ (× lot_size) — так LLM видит реальный
                    # размер экспозиции, а не лот-число. Risk Officer считает
                    # отдельно в лотах (position_for на ctx.positions).
                    lots = position_for(positions, ticker)
                    lot_size = risk_mod.lot_size_for(ticker, lot_sizes)
                    portfolio_context = self._portfolio_context_for_ticker(
                        ticker,
                        cash,
                        positions,
                        portfolio_prices,
                        lot_sizes,
                        portfolio_nav,
                    )
                    try:
                        return await asyncio.wait_for(
                            asyncio.to_thread(
                                self._compute_decision,
                                ticker,
                                lots * lot_size,
                                portfolio_context,
                                market_context,
                            ),
                            timeout=per_ticker_timeout,
                        )
                    except asyncio.TimeoutError:
                        log.warning(
                            "scheduler.ticker.timeout",
                            ticker=ticker,
                            timeout_sec=per_ticker_timeout,
                        )
                        raise

        log.info(
            "scheduler.phase1.start",
            tickers=len(llm_universe),
            universe_total=len(self.universe),
            concurrency=self.max_concurrent_tickers,
        )
        t_phase1 = time.monotonic()
        llm_results = await asyncio.gather(
            *(_evaluate(t) for t in llm_universe),
            return_exceptions=True,
        )
        results_by_ticker = dict(zip(llm_universe, llm_results, strict=True))
        results = [
            results_by_ticker.get(t) or self._skipped_llm_result(t)
            for t in self.universe
        ]
        log.info(
            "scheduler.phase1.done",
            elapsed_sec=round(time.monotonic() - t_phase1, 2),
            llm_evaluated=len(llm_universe),
            hold_without_llm=len(skipped_llm),
        )

        # Phase 2 — synchronous Risk Officer + order submission. Cash/positions
        # обновляются между тикерами, in-tick simulation сохраняется.
        live_prices = dict(portfolio_prices)
        live_prices.update(self._prices_from_results(self.universe, results))
        live_prices.update(self._latest_prices_for_positions(positions, live_prices))
        nav = self._portfolio_nav(cash, positions, live_prices, lot_sizes)
        rec.nav = nav
        log.info(
            "scheduler.tick.nav_mark_to_market",
            nav=nav,
            priced_positions=sum(1 for p in positions if p.get("secid") in live_prices),
        )

        live_cash = cash
        live_positions = [dict(p) for p in positions]
        live_nav = nav
        live_trade_count = daily_trade_count
        tick_open_nav = nav
        tick_buy_spent = 0.0

        # Перемешиваем порядок Phase 2 — без этого первые тикеры в universe
        # всегда забирают tick_allocation бюджет, последние — никогда.
        phase2_order = list(zip(self.universe, results, strict=True))
        random.shuffle(phase2_order)

        for ticker, result in phase2_order:
            if isinstance(result, BaseException):
                rec.errors[ticker] = str(result)[:300]
                log.exception(
                    "scheduler.ticker.failed",
                    ticker=ticker,
                    exc_info=result,
                )
                continue

            with structlog.contextvars.bound_contextvars(symbol=ticker):
                try:
                    # submit_order — blocking httpx; без to_thread морозит event loop.
                    summary = await asyncio.to_thread(
                        self._apply_decision_with_risk,
                        ticker,
                        result,
                        live_cash,
                        live_positions,
                        live_nav,
                        lot_sizes,
                        live_trade_count,
                        tick_open_nav,
                        tick_buy_spent,
                    )
                    rec.decisions[ticker] = summary
                    action = summary.get("action", {})
                    status = action.get("status")
                    op_type = action.get("op_type") or ""
                    if status in ("buy_submitted", "sell_submitted"):
                        live_trade_count += 1
                    elif status == "flip_executed":
                        live_trade_count += 2  # close + open
                    # tick_buy_spent (gross): любое **открытие/добавление** позиции
                    # съедает бюджет — лонг (тратит cash) и шорт (создаёт liability).
                    # close_long/cover_short — не съедают (позиция уменьшается).
                    opening_ops = {
                        "open_long", "add_long", "open_short", "add_short",
                        "flip_long_to_short", "flip_short_to_long",
                    }
                    if op_type in opening_ops:
                        if status == "flip_executed":
                            # Только open-часть. qty в action — ЛОТЫ, notional = qty × lot × price.
                            open_qty = float(action.get("open_qty") or 0)
                            last_p = float(summary.get("last_price") or 0.0)
                            t_lot = risk_mod.lot_size_for(ticker, lot_sizes)
                            tick_buy_spent += open_qty * t_lot * last_p * (
                                1.0 + self.commission_rate
                            )
                        else:
                            total_cost = float(
                                action.get("total_cost")
                                or action.get("notional")
                                or 0.0
                            )
                            tick_buy_spent += total_cost
                    live_cash, live_positions = self._apply_summary_delta(
                        summary, ticker, live_cash, live_positions, lot_sizes
                    )
                    if summary.get("last_price"):
                        live_prices[ticker] = float(summary["last_price"])
                    live_nav = self._portfolio_nav(
                        live_cash, live_positions, live_prices, lot_sizes
                    )
                except Exception as e:
                    rec.errors[ticker] = str(e)[:300]
                    log.exception("scheduler.ticker.apply_failed", ticker=ticker)

        rec.finished_at = datetime.now(UTC)
        elapsed = (rec.finished_at - rec.started_at).total_seconds()

        # Watchdog: считаем тики подряд без реальных ордеров. Только лог,
        # без авто-вмешательства в risk-логику.
        orders_this_tick = sum(
            1
            for d in rec.decisions.values()
            if d.get("action", {}).get("status")
            in ("buy_submitted", "sell_submitted", "flip_executed")
        )
        if orders_this_tick == 0:
            self.consecutive_idle_ticks += 1
        else:
            self.consecutive_idle_ticks = 0
        if self.consecutive_idle_ticks >= 4:
            # 4 тика × 30 мин = 2 часа без активности.
            log.warning(
                "scheduler.bot_idle",
                ticks_idle=self.consecutive_idle_ticks,
                nav=rec.nav,
                cash=rec.cash,
                positions_count=sum(
                    1 for d in rec.decisions.values()
                    if d.get("action", {}).get("status") == "risk_block"
                ),
            )

        log.info(
            "scheduler.tick.done",
            n=self.status.tick_count,
            elapsed_sec=elapsed,
            errors=len(rec.errors),
            orders=orders_this_tick,
            consecutive_idle_ticks=self.consecutive_idle_ticks,
        )
        get_webhook_notifier().send(
            "scheduler_tick_done",
            {
                "tick_n": self.status.tick_count,
                "orders": orders_this_tick,
                "errors": len(rec.errors),
                "nav": rec.nav,
                "skipped_reason": rec.skipped_reason,
            },
        )

        # Turnover-pace монитор (observability): ноционал исполненных сделок за
        # тик + кумулятив за сегодня (контроль floor оборота 10М). Только лог,
        # поведение не меняем.
        tick_gross = sum(
            float((d.get("action") or {}).get("notional") or 0.0)
            for d in rec.decisions.values()
            if (d.get("action") or {}).get("status")
            in ("buy_submitted", "sell_submitted", "flip_executed")
        )
        cum_gross_today = self._turnover_today_from_journal() + tick_gross
        days = max(int(getattr(self.settings, "AGENT_TURNOVER_DAYS", 10) or 10), 1)
        floor_rub = float(getattr(self.settings, "AGENT_TURNOVER_FLOOR_RUB", 0.0) or 0.0)
        target_today = floor_rub / days if floor_rub > 0 else 0.0
        pace_ratio = (cum_gross_today / target_today) if target_today > 0 else 0.0
        log.info(
            "scheduler.turnover.pace",
            tick_gross=round(tick_gross, 2),
            cum_gross_today=round(cum_gross_today, 2),
            target_today=round(target_today, 2),
            pace_ratio=round(pace_ratio, 3),
            floor_rub=floor_rub,
            days=days,
        )

        self.journal.write(
            "tick",
            n=self.status.tick_count,
            elapsed_sec=elapsed,
            cash=cash,
            nav=nav,
            # Маркер версии формулы NAV: load_nav_history считает peak только
            # по записям текущей версии → старые багнутые NAV игнорируются.
            nav_calc=NAV_CALC_VERSION,
            tick_gross=tick_gross,
            cum_gross_today=cum_gross_today,
            decisions=rec.decisions,
            errors=rec.errors,
        )
        try:
            run_meta_reflection(trade_journal=self.journal)
        except Exception:
            log.exception("meta_reflection.failed")
        return rec

    @staticmethod
    def _skipped_llm_result(ticker: str) -> dict:
        """HOLD без вызова графа — тикер не попал в LLM-батч этого тика."""
        analyst = AnalystOutput(
            trend="flat",
            momentum="flat",
            volatility="normal",
            summary="LLM universe cap — skipped this tick.",
            confidence=0.0,
        )
        decision = Decision(
            symbol=ticker,
            signal="HOLD",
            size_pct=0.0,
            confidence=0.0,
            reasoning="Not in LLM batch this tick (AGENT_LLM_TICKERS_PER_TICK).",
            analyst_output=analyst,
            timestamp=datetime.now(UTC),
        )
        return {
            "decision": decision,
            "last_price": 0.0,
            "returns_window": [],
            "graph_ms": 0,
        }

    def _portfolio_nav(
        self,
        cash: float,
        positions: list[dict],
        prices: dict[str, float] | None = None,
        lot_sizes: dict[str, int] | None = None,
    ) -> float:
        """NAV под collateral-модель ArenaGo.

        ArenaGo при открытии позиции (и long, и short) блокирует cost_basis из
        cash — видно по логам: cash падает на order_value при ЛЮБОМ открытии,
        включая шорт. `cash_balance` из /api/bots — это ДОСТУПНЫЙ кэш после
        блокировки. Поэтому:

            NAV = available_cash + Σ(cost_basis + unrealized_PnL)
              cost_basis = |position| × lot × average_price  (вернётся при закрытии)
              PnL_long   = position × lot × (last − avg)
              PnL_short  = |position| × lot × (avg − last)

        Наивная формула cash + Σ(signed_pos × lot × last) даёт двойной учёт
        для шортов (на реальных данных ArenaGo выдавала −652k вместо ~1M).

        `position` несёт `lot_size` и `last_price` прямо из ArenaGo — берём их,
        иначе fallback на live-prices / нашу таблицу.
        """
        prices = prices or {}
        lot_sizes = lot_sizes or {}
        nav = float(cash)
        for position in positions:
            secid = str(position.get("secid") or "")
            pos = float(position.get("position", 0) or 0)
            if pos == 0:
                continue
            lot_size = float(
                position.get("lot_size") or risk_mod.lot_size_for(secid, lot_sizes)
            )
            avg = float(position.get("average_price", 0) or 0)
            last = (
                prices.get(secid)
                or float(position.get("last_price", 0) or 0)
                or avg
            )
            cost_basis = abs(pos) * lot_size * avg
            if pos > 0:
                pnl = pos * lot_size * (last - avg)
            else:
                pnl = abs(pos) * lot_size * (avg - last)
            nav += cost_basis + pnl
        return nav

    def _portfolio_context_for_ticker(
        self,
        ticker: str,
        cash: float,
        positions: list[dict],
        prices: dict[str, float],
        lot_sizes: dict[str, int],
        nav: float,
    ) -> dict[str, float | int]:
        gross_value = 0.0
        net_value = 0.0
        current_value = 0.0
        positions_count = 0
        ticker = ticker.upper()

        for position in positions:
            secid = str(position.get("secid") or "").upper()
            pos = float(position.get("position", 0) or 0)
            if not secid or pos == 0:
                continue
            lot_size = float(
                position.get("lot_size") or risk_mod.lot_size_for(secid, lot_sizes)
            )
            price = (
                float(prices.get(secid) or 0.0)
                or float(position.get("last_price", 0) or 0)
                or float(position.get("average_price", 0) or 0)
            )
            signed_value = pos * lot_size * price
            gross_value += abs(signed_value)
            net_value += signed_value
            positions_count += 1
            if secid == ticker:
                current_value += signed_value

        return {
            "nav": nav,
            "cash": cash,
            "cash_pct": cash / nav if nav > 0 else 0.0,
            "gross_exposure_pct": gross_value / nav if nav > 0 else 0.0,
            "net_exposure_pct": net_value / nav if nav > 0 else 0.0,
            "current_weight_pct": abs(current_value) / nav if nav > 0 else 0.0,
            "current_value": current_value,
            "positions_count": positions_count,
        }

    def _market_context(self) -> dict[str, float | int | str]:
        if not bool(getattr(self.settings, "MARKET_CONTEXT_ENABLED", True)):
            return {}

        interval = max(int(self.candle_interval or 10), 1)
        fast_minutes = max(
            int(getattr(self.settings, "MARKET_CONTEXT_FAST_MINUTES", 60) or 60),
            interval,
        )
        mid_minutes = max(
            int(getattr(self.settings, "MARKET_CONTEXT_MID_MINUTES", 240) or 240),
            fast_minutes,
        )
        fast_bars = max(round(fast_minutes / interval), 1)
        mid_bars = max(round(mid_minutes / interval), fast_bars)
        needed = mid_bars + 1

        fast_returns: list[float] = []
        mid_returns: list[float] = []
        failures = 0
        for symbol in self.universe:
            try:
                df = market_data.get_candles(symbol, interval=interval, days=2)
                closes = [
                    float(v)
                    for v in df["close"].tail(needed).tolist()
                    if v is not None and float(v) > 0
                ]
            except Exception as e:
                failures += 1
                log.debug("market_context.symbol_failed", symbol=symbol, error=str(e)[:160])
                continue
            if len(closes) < needed:
                failures += 1
                continue
            last = closes[-1]
            fast_base = closes[-(fast_bars + 1)]
            mid_base = closes[-(mid_bars + 1)]
            if fast_base <= 0 or mid_base <= 0:
                failures += 1
                continue
            fast_returns.append(last / fast_base - 1.0)
            mid_returns.append(last / mid_base - 1.0)

        if not mid_returns:
            return {}

        fast_return = median(fast_returns) if fast_returns else 0.0
        mid_return = median(mid_returns)
        breadth_up_pct = sum(1 for r in mid_returns if r > 0.0) / len(mid_returns)
        fast_breadth_up_pct = (
            sum(1 for r in fast_returns if r > 0.0) / len(fast_returns)
            if fast_returns else 0.0
        )

        trend_threshold = float(
            getattr(self.settings, "MARKET_CONTEXT_RETURN_THRESHOLD", 0.0025)
            or 0.0025
        )
        reversal_threshold = float(
            getattr(self.settings, "MARKET_CONTEXT_REVERSAL_THRESHOLD", 0.002)
            or 0.002
        )
        bullish_breadth = float(
            getattr(self.settings, "MARKET_CONTEXT_BULLISH_BREADTH", 0.55)
            or 0.55
        )
        bearish_breadth = float(
            getattr(self.settings, "MARKET_CONTEXT_BEARISH_BREADTH", 0.45)
            or 0.45
        )

        if mid_return < -trend_threshold and fast_return > reversal_threshold:
            regime = "rebound"
        elif mid_return > trend_threshold and fast_return < -reversal_threshold:
            regime = "pullback"
        elif (
            mid_return > trend_threshold
            and breadth_up_pct > bullish_breadth
            and fast_return >= -trend_threshold / 2
        ):
            regime = "bullish"
        elif (
            mid_return < -trend_threshold
            and breadth_up_pct < bearish_breadth
            and fast_return <= trend_threshold / 2
        ):
            regime = "bearish"
        else:
            regime = "neutral"

        return {
            "regime": regime,
            "fast_window_minutes": fast_minutes,
            "mid_window_minutes": mid_minutes,
            "fast_return": fast_return,
            "mid_return": mid_return,
            "breadth_up_pct": breadth_up_pct,
            "fast_breadth_up_pct": fast_breadth_up_pct,
            "symbols": len(mid_returns),
            "failures": failures,
        }

    def _lot_sizes_for(self, positions: list[dict]) -> dict[str, int]:
        lot_sizes = dict(self.lot_sizes)
        for position in positions:
            secid = str(position.get("secid") or "")
            raw = position.get("lot_size", position.get("lotsize"))
            if secid and raw:
                try:
                    lot_sizes[secid] = int(raw)
                except (TypeError, ValueError):
                    pass
        return lot_sizes

    def _prices_from_results(
        self,
        tickers: tuple[str, ...],
        results: list[dict | BaseException],
    ) -> dict[str, float]:
        prices: dict[str, float] = {}
        for ticker, result in zip(tickers, results, strict=True):
            if isinstance(result, BaseException):
                continue
            price = float(result.get("last_price") or 0.0)
            if price > 0:
                prices[ticker] = price
        return prices

    def _latest_prices_for_positions(
        self,
        positions: list[dict],
        known_prices: dict[str, float],
    ) -> dict[str, float]:
        prices: dict[str, float] = {}
        for position in positions:
            secid = str(position.get("secid") or "")
            qty = float(position.get("position", 0) or 0)
            # qty != 0 — шортам тоже нужен свежий price для mark-to-market.
            if not secid or qty == 0 or secid in known_prices:
                continue
            embedded_price = float(position.get("last_price", 0) or 0)
            if embedded_price > 0:
                prices[secid] = embedded_price
                continue
            try:
                df = market_data.get_candles(
                    secid,
                    interval=self.candle_interval,
                    days=5,
                )
                price = float(df.iloc[-1]["close"])
            except Exception:
                log.warning("scheduler.position_price.unavailable", secid=secid)
                continue
            if price > 0:
                prices[secid] = price
        return prices

    def _daily_trade_count(self) -> int:
        """Сделок за сегодня. `/api/trades/{bot}` сам возвращает только
        сегодняшние, плюс источник — внешний API → переживает рестарт пода."""
        return len(self.arenago.get_trades())

    def _turnover_today_from_journal(self) -> float:
        """Сумма tick_gross по сегодняшним (MSK) tick-записям журнала.
        Переживает рестарт пода: журнал на персистентном диске."""
        today = now_msk().date()
        total = 0.0
        for rec in self.journal.tail(2000):
            if rec.get("event") != "tick":
                continue
            ts_raw = rec.get("ts")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.astimezone(MSK).date() != today:
                continue
            try:
                total += float(rec.get("tick_gross") or 0.0)
            except (TypeError, ValueError):
                continue
        return total

    def _profit_steps_done(self, ticker: str, positions: list[dict]) -> set[str]:
        current = next(
            (p for p in positions if str(p.get("secid", "")).upper() == ticker.upper()),
            None,
        )
        if not current:
            return set()
        try:
            qty = int(float(current.get("position") or 0))
            avg_price = float(current.get("average_price") or 0.0)
        except (TypeError, ValueError):
            return set()
        if qty == 0 or avg_price <= 0:
            return set()
        side = "long" if qty > 0 else "short"
        out: set[str] = set()
        for rec in self.journal.tail(2000):
            if rec.get("event") != "tick":
                continue
            summary = (rec.get("decisions") or {}).get(ticker) or {}
            action = summary.get("action") or {}
            op_type = action.get("op_type") or ""
            if op_type not in ("take_profit_sell", "take_profit_cover"):
                continue
            action_side = action.get("side")
            if not action_side:
                action_side = "short" if op_type.endswith("_cover") else "long"
            if action_side != side:
                continue
            try:
                action_avg = float(action.get("avg_price") or 0.0)
                step_pct = float(action.get("profit_step") or 0.0)
            except (TypeError, ValueError):
                continue
            if action_avg <= 0 or abs(action_avg - avg_price) > 1e-6:
                continue
            step_id = risk_mod.profit_step_id_from_pct(step_pct, self.settings)
            if step_id:
                out.add(step_id)
        return out

    def _compute_decision(
        self,
        ticker: str,
        current_position: int = 0,
        portfolio_context: dict | None = None,
        market_context: dict | None = None,
    ) -> dict:
        """Phase 1: только LLM-фаза, без портфельных мутаций.

        Возвращает payload для phase 2: decision + ценовой контекст для
        Risk Officer. Этот метод можно безопасно запускать параллельно по
        всем тикерам — никаких side-effect'ов кроме чтения внешних API
        (MOEX/news через граф) и логов.
        """
        t0 = time.monotonic()
        state = self.graph.invoke(
            {
                "symbol": ticker,
                "interval": self.candle_interval,
                "current_position": current_position,
                "commission_rate": self.commission_rate,
                "portfolio_context": portfolio_context or {},
                "market_context": market_context or {},
            }
        )
        decision: Decision = state["decision"]
        graph_ms = int((time.monotonic() - t0) * 1000)
        last_price = self._last_price_from_state(state) or 0.0
        returns_window = self._returns_from_state(state, self.settings.RISK_VAR_LOOKBACK)
        flow_features: dict[str, float] = {}
        snap = state.get("snapshot")
        snap_features = getattr(snap, "features", None) if snap else None
        if isinstance(snap_features, dict):
            flow_features = {
                k: float(v)
                for k, v in snap_features.items()
                if k.startswith(("disb", "pr_vwap", "pr_change", "ob_", "order_", "flow_"))
            }

        return {
            "decision": decision,
            "last_price": last_price,
            "returns_window": returns_window,
            "graph_ms": graph_ms,
            "flow_features": flow_features,
        }

    def _apply_decision_with_risk(
        self,
        ticker: str,
        compute_result: dict,
        live_cash: float,
        live_positions: list[dict],
        live_nav: float,
        lot_sizes: dict[str, int],
        daily_trade_count: int,
        tick_open_nav: float = 0.0,
        tick_buy_spent: float = 0.0,
    ) -> dict:
        """Phase 2: Risk Officer + submit_order поверх свежего snapshot'а.

        Вызывается последовательно в цикле run_once — между вызовами cash
        и positions обновляются. Этот метод сам ничего не мутирует, только
        возвращает summary; обновление state делает run_once через
        _apply_summary_delta."""
        decision: Decision = compute_result["decision"]
        last_price = compute_result["last_price"]
        returns_window = compute_result["returns_window"]
        graph_ms = compute_result["graph_ms"]

        nav_history = load_nav_history(
            self.journal, lookback_days=self.settings.RISK_NAV_HISTORY_DAYS
        )
        ctx = RiskContext(
            cash=live_cash,
            positions=live_positions,
            nav=live_nav,
            last_price=last_price,
            returns_window=returns_window,
            nav_history=nav_history,
            settings=self.settings,
            lot_sizes=lot_sizes,
            tick_buy_spent=tick_buy_spent,
            tick_open_nav=tick_open_nav,
            flow_features=compute_result.get("flow_features") or {},
            profit_steps_done=self._profit_steps_done(ticker, live_positions),
        )
        gate = risk_mod.evaluate(decision, ctx)
        action = self._apply_gate(decision, gate, ticker, last_price, daily_trade_count)

        summary = {
            "signal": decision.signal,
            "size_pct": decision.size_pct,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
            "last_price": last_price,
            "graph_ms": graph_ms,
            "action": action,
        }
        log.info("scheduler.ticker.done", ticker=ticker, **action)
        op_type = action.get("op_type") or ""
        if is_closing_operation(op_type):
            try:
                reflect_on_trade(
                    symbol=ticker,
                    decision=decision,
                    op_type=op_type,
                    action_summary=action,
                )
            except Exception as e:
                log.warning(
                    "reflection.failed",
                    ticker=ticker,
                    error=str(e)[:200],
                )
        return summary

    @staticmethod
    def _apply_summary_delta(
        summary: dict,
        ticker: str,
        live_cash: float,
        live_positions: list[dict],
        lot_sizes: dict[str, int] | None = None,
    ) -> tuple[float, list[dict]]:
        """In-tick симуляция cash/positions под collateral-модель ArenaGo.

        cash меняется по типу операции (не по buy/sell):
          * opening (open/add long, open/add short): cash -= notional (collateral)
          * closing (close long, cover short):       cash += notional (возврат)
        position (лоты) меняется по направлению заявки: BUY → +qty, SELL → −qty.
        Для flip два шага: close (cash в) + open (cash из).

        В реальной торговле response.remaining_cash override-ит расчёт.
        """
        lot_sizes = lot_sizes or {}
        lot_size = risk_mod.lot_size_for(ticker, lot_sizes)
        action = summary.get("action") or {}
        status = action.get("status")
        if status not in ("buy_submitted", "sell_submitted", "flip_executed"):
            return live_cash, live_positions

        price = float(summary.get("last_price") or 0.0)
        if price <= 0:
            return live_cash, live_positions

        new_positions = [dict(p) for p in live_positions]
        op_type = action.get("op_type") or ""

        # Шаги: (pos_delta_lots, cash_delta). cash_delta < 0 — открытие (collateral
        # списан), > 0 — закрытие (collateral возвращён).
        steps: list[tuple[int, float]] = []
        if status == "flip_executed":
            close_qty = int(action.get("close_qty") or 0)
            open_qty = int(action.get("open_qty") or 0)
            close_notional = close_qty * lot_size * price
            open_notional = open_qty * lot_size * price
            if "long_to_short" in op_type:
                # close long (pos↓, cash↑) + open short (pos↓, cash↓)
                if close_qty > 0:
                    steps.append((-close_qty, +close_notional))
                if open_qty > 0:
                    steps.append((-open_qty, -open_notional))
            else:  # short_to_long
                # cover short (pos↑, cash↑) + open long (pos↑, cash↓)
                if close_qty > 0:
                    steps.append((+close_qty, +close_notional))
                if open_qty > 0:
                    steps.append((+open_qty, -open_notional))
        else:
            qty = int(action.get("qty") or 0)
            if qty <= 0:
                return live_cash, live_positions
            notional = qty * lot_size * price
            if status == "buy_submitted":
                # cover_short + reduce-override *_cover — закрытие (cash↑);
                # open/add long — открытие (cash↓)
                closing = op_type == "cover_short" or op_type.endswith("_cover")
                cash_delta = +notional if closing else -notional
                steps.append((+qty, cash_delta))
            else:  # sell_submitted
                # close_long + reduce-override *_sell — закрытие (cash↑);
                # open/add short — открытие (cash↓)
                closing = op_type == "close_long" or op_type.endswith("_sell")
                cash_delta = +notional if closing else -notional
                steps.append((-qty, cash_delta))

        # commission хранится либо в response.commission, либо в action.estimated_commission
        response = action.get("response") or {}
        commission_total = float(
            response.get("commission")
            or action.get("estimated_commission")
            or 0.0
        )

        for delta_qty, cash_delta in steps:
            live_cash += cash_delta
            idx = next(
                (i for i, p in enumerate(new_positions) if p.get("secid") == ticker),
                -1,
            )
            if idx >= 0:
                old_qty = float(new_positions[idx].get("position", 0))
                old_avg = float(new_positions[idx].get("average_price", price))
                new_qty = old_qty + delta_qty
                # Усреднение avg_price имеет смысл только когда **направление** не
                # меняется (одно расширение позиции). Иначе берём текущую цену.
                if old_qty == 0:
                    new_avg = price
                elif (old_qty > 0) == (delta_qty > 0):
                    # Расширяем в ту же сторону → классическое усреднение.
                    new_avg = (
                        (abs(old_qty) * old_avg + abs(delta_qty) * price)
                        / abs(new_qty)
                        if new_qty != 0 else price
                    )
                else:
                    # Уменьшаем/закрываем/пересекаем ноль → avg оставляем как есть
                    # (для остатка) либо переключаем на price (если перешли через 0).
                    new_avg = old_avg if (old_qty > 0) == (new_qty > 0) and new_qty != 0 else price
                if new_qty == 0:
                    new_positions.pop(idx)
                else:
                    new_positions[idx]["position"] = new_qty
                    new_positions[idx]["average_price"] = new_avg
            else:
                # Нет позиции → создаём со знаком sign.
                new_positions.append(
                    {"secid": ticker, "position": float(delta_qty), "average_price": price}
                )

        # Списываем комиссию один раз за всю операцию (для flip — суммарно).
        live_cash -= commission_total

        # remaining_cash из response может override-нуть всю математику (если API возвращает).
        remaining_cash = response.get("remaining_cash")
        if remaining_cash is not None:
            live_cash = float(remaining_cash)

        return live_cash, new_positions

    @staticmethod
    def _last_price_from_state(state: dict) -> float | None:
        snap = state.get("snapshot")
        if snap and snap.candles:
            return float(snap.candles[-1].close)
        return None

    @staticmethod
    def _returns_from_state(state: dict, lookback: int) -> list[float]:
        snap = state.get("snapshot")
        if not snap or not snap.candles:
            return []
        closes = [float(c.close) for c in snap.candles[-(lookback + 1):]]
        return log_returns(closes)

    def _apply_gate(
        self,
        decision: Decision,
        gate,
        ticker: str,
        last_price: float,
        daily_trade_count: int = 0,
    ) -> dict:
        """Применить решение Risk Officer: либо отправить ордер, либо записать block."""
        # reduce-override (risk_trim / take_profit / stop_loss) перехватывает ВЫШЕ
        # сигнала LLM — может прийти на HOLD-решении. Направление по суффиксу op_type.
        gate_op = (gate.op_type or "") if gate else ""
        is_reduce = gate_op in (
            "risk_trim_cover", "risk_trim_sell",
            "take_profit_cover", "take_profit_sell",
            "stop_loss_cover", "stop_loss_sell",
        )
        if decision.signal == "HOLD" and not (is_reduce and gate.allowed):
            return {"status": "hold"}

        if not gate.allowed:
            self.journal.write(
                "risk_block",
                symbol=decision.symbol,
                signal=decision.signal,
                gate=gate.gate,
                reason=gate.reason,
                confidence=decision.confidence,
                **gate.metrics,
            )
            return {
                "status": "risk_block",
                "gate": gate.gate,
                "reason": gate.reason,
                **{k: round(v, 4) for k, v in gate.metrics.items()},
            }

        if self.daily_trade_limit > 0 and daily_trade_count >= self.daily_trade_limit:
            return {
                "status": "daily_trade_limit",
                "trade_count": daily_trade_count,
                "limit": self.daily_trade_limit,
            }

        # Risk-clip event (effective_size урезан гейтами).
        effective_size = gate.effective_size or 0.0
        if effective_size and effective_size < decision.size_pct - 1e-9:
            self.journal.write(
                "risk_clip",
                symbol=decision.symbol,
                requested_size=decision.size_pct,
                effective_size=effective_size,
                clipping_gate=gate.reason,
            )

        op_type = gate.op_type or ("buy" if decision.signal == "BUY" else "sell")
        # qty в action — ЛОТЫ; денежные оценки = qty × lot_size × price.
        lot_size = int(gate.metrics.get("lot_size") or risk_mod.lot_size_for(ticker))

        # ── Flip: две заявки подряд ───────────────────────────────────────
        if gate.flip_close_qty is not None and gate.flip_open_qty is not None:
            # Направление одинаковое для обеих частей (см. план):
            # long→short: close=S, open=S; short→long: close=B, open=B.
            direction = "S" if "long_to_short" in op_type else "B"
            close_qty = int(gate.flip_close_qty)
            open_qty = int(gate.flip_open_qty)
            total_qty = close_qty + open_qty
            notional = total_qty * lot_size * last_price
            estimated_commission = notional * self.commission_rate

            close_resp = self.arenago.submit_order(
                decision.symbol, direction, quantity=close_qty
            )
            if not close_resp.get("success"):
                return {
                    "status": "order_rejected",
                    "stage": "flip_close",
                    "qty": close_qty,
                    "response": close_resp,
                }
            open_resp = self.arenago.submit_order(
                decision.symbol, direction, quantity=open_qty
            )
            if not open_resp.get("success"):
                # Close прошёл, open отказан — отдаём частичный успех как close-only.
                return {
                    "status": "sell_submitted" if direction == "S" else "buy_submitted",
                    "op_type": op_type.replace("flip_long_to_short", "close_long").replace(
                        "flip_short_to_long", "cover_short"
                    ),
                    "qty": close_qty,
                    "notional": close_qty * lot_size * last_price,
                    "estimated_commission": close_qty * lot_size * last_price * self.commission_rate,
                    "response": close_resp,
                    "open_rejected": open_resp,
                }
            return {
                "status": "flip_executed",
                "op_type": op_type,
                "close_qty": close_qty,
                "open_qty": open_qty,
                "notional": notional,
                "estimated_commission": estimated_commission,
                "response": open_resp,  # final state — после open
                "close_response": close_resp,
            }

        # ── Обычная одиночная заявка ──────────────────────────────────────
        qty = int(gate.qty or 0)
        notional = float(gate.metrics.get("notional") or qty * lot_size * last_price)
        estimated_commission = float(
            gate.metrics.get("commission") or notional * self.commission_rate
        )
        total_cost = float(
            gate.metrics.get("total_cost") or notional + estimated_commission
        )
        action_extra = {
            k: round(float(gate.metrics[k]), 6)
            for k in ("profit_step", "pnl_pct", "avg_price", "close_fraction")
            if k in gate.metrics
        }
        if op_type in ("take_profit_sell", "stop_loss_sell"):
            action_extra["side"] = "long"
        elif op_type in ("take_profit_cover", "stop_loss_cover"):
            action_extra["side"] = "short"

        # Направление: для reduce-override — по суффиксу op_type (_cover→BUY,
        # _sell→SELL; приходит даже на HOLD), иначе по сигналу.
        if is_reduce:
            direction = "B" if op_type.endswith("_cover") else "S"
        else:
            direction = "S" if decision.signal == "SELL" else "B"

        if direction == "S":
            resp = self.arenago.submit_order(decision.symbol, "S", quantity=qty)
            if not resp.get("success"):
                return {"status": "order_rejected", "qty": qty, "response": resp}
            return {
                "status": "sell_submitted",
                "op_type": op_type,
                "qty": qty,
                "notional": notional,
                "estimated_commission": estimated_commission,
                "response": resp,
                **action_extra,
            }

        # BUY
        resp = self.arenago.submit_order(decision.symbol, "B", quantity=qty)
        if not resp.get("success"):
            return {"status": "order_rejected", "qty": qty, "response": resp}
        return {
            "status": "buy_submitted",
            "op_type": op_type,
            "qty": qty,
            "notional": notional,
            "estimated_commission": estimated_commission,
            "total_cost": total_cost,
            "response": resp,
            **action_extra,
        }
