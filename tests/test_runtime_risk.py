"""Risk Officer gates. Без сети, без MOEX/ArenaGo — чистые юнит-тесты на evaluate()."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.runtime import scheduler as scheduler_mod
from agent.runtime.hours import MSK
from agent.runtime.journal import JsonlJournal
from agent.runtime.risk import RiskContext, evaluate, load_nav_history
from agent.runtime.scheduler import TradingScheduler
from agent.schemas import AnalystOutput, Decision


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        RISK_ENABLED=True,
        RISK_MIN_CONFIDENCE=0.35,
        RISK_MAX_INSTRUMENT_WEIGHT=0.15,
        RISK_MAX_SECTOR_WEIGHT=0.35,
        RISK_MAX_VAR_PCT=0.04,
        RISK_MAX_DRAWDOWN=0.10,
        RISK_MAX_DAILY_LOSS=0.03,
        RISK_CASH_BUFFER=0.02,
        RISK_VAR_LOOKBACK=60,
        RISK_NAV_HISTORY_DAYS=5,
        # 0 — выключить tick_allocation в большинстве юнит-тестов; включаем
        # явно только в тестах, которые этот гейт проверяют.
        RISK_MAX_TICK_BUY_PCT=0.0,
        TRADING_COMMISSION_RATE=0.0,
        ARENAGO_DAILY_TRADE_LIMIT=200,
        # risk_trim по умолчанию ВЫКЛ в юнит-тестах гейтов (иначе крупная позиция
        # ловит trim вместо проверяемого гейта). Включаем явно в trim-тестах.
        RISK_TRIM_ENABLED=False,
        RISK_TRIM_BAND=0.10,
        RISK_TRIM_LOSS_TOLERANCE=0.0,
        RISK_TRIM_STOP_PCT=0.03,
        RISK_TRIM_MAX_PCT_PER_TICK=0.0,
        # no-flip по умолчанию (как в проде); flip-тесты включают явно.
        AGENT_ALLOW_FLIP=False,
        # TP/SL по умолчанию ВЫКЛ в гейт-тестах (иначе прибыльная/убыточная
        # позиция ловит pnl_exit вместо проверяемого гейта). Вкл. в pnl-тестах.
        RISK_TAKE_PROFIT_PCT=0.0,
        RISK_STOP_LOSS_PCT=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _decision(
    symbol: str = "SBER", signal: str = "BUY", size_pct: float = 0.10, confidence: float = 0.7
) -> Decision:
    return Decision(
        symbol=symbol,
        signal=signal,
        size_pct=size_pct if signal != "HOLD" else 0.0,
        confidence=confidence,
        reasoning="test",
        analyst_output=AnalystOutput(
            trend="up", momentum="weak_up", volatility="normal",
            summary="ok", confidence=confidence,
        ),
        timestamp=datetime.now(UTC),
    )


def _ctx(
    cash: float = 100_000,
    nav: float = 100_000,
    positions: list[dict] | None = None,
    last_price: float = 300.0,
    lot_sizes: dict[str, int] | None = None,
    returns: list[float] | None = None,
    nav_history: list[tuple[datetime, float]] | None = None,
    settings: SimpleNamespace | None = None,
    tick_buy_spent: float = 0.0,
    tick_open_nav: float = 0.0,
    profit_steps_done: set[str] | None = None,
) -> RiskContext:
    return RiskContext(
        cash=cash,
        positions=positions or [],
        nav=nav,
        last_price=last_price,
        lot_sizes=lot_sizes or {},
        returns_window=returns or [],
        nav_history=nav_history or [],
        settings=settings or _settings(),
        tick_buy_spent=tick_buy_spent,
        tick_open_nav=tick_open_nav,
        profit_steps_done=profit_steps_done or set(),
    )


# ── Гейт-уровневые тесты ─────────────────────────────────────────────────────


def test_hold_skipped_no_evaluation() -> None:
    res = evaluate(_decision(signal="HOLD"), _ctx())
    assert res.allowed
    assert res.gate == "hold"


def test_low_confidence_blocks_buy() -> None:
    res = evaluate(_decision(confidence=0.2), _ctx())
    assert not res.allowed
    assert res.gate == "sanity_confidence"


def test_low_confidence_sell_on_long_closes_without_opening_sanctions() -> None:
    positions = [{"secid": "SBER", "position": 10, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(signal="SELL", size_pct=0.10, confidence=0.01),
        _ctx(positions=positions, last_price=300.0, lot_sizes={"SBER": 1}),
    )
    assert res.allowed
    assert res.op_type == "close_long"
    assert res.qty == 10
    assert res.gate != "sanity_confidence"


def test_low_confidence_buy_on_short_covers_without_opening_sanctions() -> None:
    positions = [{"secid": "SBER", "position": -10, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(signal="BUY", size_pct=0.10, confidence=0.01),
        _ctx(positions=positions, last_price=300.0, lot_sizes={"SBER": 1}),
    )
    assert res.allowed
    assert res.op_type == "cover_short"
    assert res.qty == 10
    assert res.gate != "sanity_confidence"


def test_low_confidence_flip_signal_closes_only_without_opening_reverse() -> None:
    positions = [{"secid": "SBER", "position": 10, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(signal="SELL", size_pct=0.10, confidence=0.01),
        _ctx(
            positions=positions,
            last_price=300.0,
            lot_sizes={"SBER": 1},
            settings=_settings(AGENT_ALLOW_FLIP=True),
        ),
    )
    assert res.allowed
    assert res.op_type == "close_long"
    assert res.qty == 10
    assert res.flip_open_qty is None
    assert res.gate == "sanity_confidence"


def test_instrument_concentration_clips_size() -> None:
    # 50 ЛОТОВ SBER × lot_size=1 × 300 ₽ = 15 000 ₽ позиция. cash=100k → NAV=115k.
    # Текущий вес SBER = 15k/115k ≈ 13.04%. cap 15% → комната ~1.96%.
    positions = [{"secid": "SBER", "position": 50, "average_price": 300, "bot": "t24"}]
    nav = 100_000 + 50 * 300
    res = evaluate(
        _decision(size_pct=0.10),
        _ctx(
            cash=100_000,
            nav=nav,
            positions=positions,
            last_price=300.0,
            lot_sizes={"SBER": 1},
        ),
    )
    assert res.allowed
    assert res.effective_size is not None
    assert res.effective_size < 0.10
    assert math.isclose(res.effective_size, 0.15 - 50 * 300 / nav, abs_tol=1e-6)


def test_instrument_concentration_blocks_at_cap() -> None:
    # 100 лотов SBER × lot=1 × 300 = 30 000 ₽. cash=10k → NAV=40k. SBER=75% > 15%.
    positions = [{"secid": "SBER", "position": 100, "average_price": 300, "bot": "t24"}]
    nav = 10_000 + 100 * 300
    res = evaluate(
        _decision(size_pct=0.05),
        _ctx(
            cash=10_000, nav=nav, positions=positions, last_price=300.0,
            lot_sizes={"SBER": 1},
        ),
    )
    assert not res.allowed
    assert res.gate == "instrument_concentration"


def test_sector_concentration_blocks() -> None:
    # 3 нефтянки уже забрали 30% NAV в OG → 4-я с 10% не пролезет.
    positions = [
        {"secid": "LKOH", "position": 100, "average_price": 100, "bot": "t24"},  # 10k
        {"secid": "ROSN", "position": 100, "average_price": 100, "bot": "t24"},  # 10k
        {"secid": "GAZP", "position": 100, "average_price": 150, "bot": "t24"},  # 15k
    ]
    nav = 100_000 + 35_000  # 135k; OG = 35k/135k ≈ 25.9%
    # Поднимем чуть выше cap'а: средние цены = 150 + 150 + 150 → пусть 50% в OG.
    # LKOH lot=1, ROSN lot=10 (по таблице). 100 лотов LKOH × 1 × 200 = 20k.
    # 100 лотов ROSN × 10 × 200 = 200k. cash=50k. nav = 50k + 20k + 200k = 270k.
    # Используем lot=1 для обоих чтобы тест был самоописательным.
    positions = [
        {"secid": "LKOH", "position": 100, "average_price": 200, "bot": "t24"},
        {"secid": "ROSN", "position": 100, "average_price": 200, "bot": "t24"},
    ]
    # Override lot=1 для упрощения арифметики теста.
    nav = 50_000 + 40_000  # 90k; OG = 40k/90k = 44.4% > 35% cap
    res = evaluate(
        _decision(symbol="NVTK", size_pct=0.05),
        _ctx(
            cash=50_000, nav=nav, positions=positions, last_price=100.0,
            lot_sizes={"LKOH": 1, "ROSN": 1, "NVTK": 1},
        ),
    )
    assert not res.allowed
    assert res.gate == "sector_concentration"


def test_var_gate_blocks_high_vol_ticker() -> None:
    # Целим σ ≈ 0.2 (экстремально высокая для теста); при size=0.15 и ρ=1:
    # VaR = 1.645 · 0.15 · 0.2 ≈ 4.93% > порога 4% → block.
    high_vol_returns = [0.2, -0.2] * 40
    res = evaluate(
        _decision(size_pct=0.15),
        _ctx(cash=100_000, nav=100_000, last_price=100.0, returns=high_vol_returns),
    )
    assert not res.allowed
    assert res.gate == "var_gate"


def test_mdd_killswitch_blocks_buys() -> None:
    # Журнал: peak NAV 1_000_000, текущий 850_000 → DD 15% > порога 10%.
    # ВАЖНО: с self-healing peak_nav peak сбрасывается на current если позиций
    # нет. Для срабатывания killswitch нужны открытые позиции — иначе peak=current.
    history = [
        (datetime.now(UTC) - timedelta(days=2), 1_000_000.0),
        (datetime.now(UTC) - timedelta(days=1), 950_000.0),
    ]
    positions = [{"secid": "GAZP", "position": 100, "average_price": 100.0, "bot": "t24"}]
    res = evaluate(
        _decision(),
        _ctx(
            cash=100_000, nav=850_000, last_price=300.0,
            nav_history=history, positions=positions,
        ),
    )
    assert not res.allowed
    assert res.gate == "kill_switch_mdd"


def test_mdd_killswitch_resets_when_flat() -> None:
    """Self-healing: при пустом портфеле peak_nav = current_nav, killswitch не срабатывает."""
    history = [
        (datetime.now(UTC) - timedelta(days=2), 1_800_000.0),  # фантомный пик
        (datetime.now(UTC) - timedelta(days=1), 1_500_000.0),
    ]
    res = evaluate(
        _decision(),
        _ctx(
            cash=1_000_000, nav=1_000_000, last_price=300.0,
            nav_history=history, positions=[],  # ← всё в кэше
        ),
    )
    # peak=current, DD=0%, killswitch не срабатывает → переходим к остальным гейтам.
    assert res.metrics.get("dd_pct", 0) == 0.0


def test_mdd_killswitch_allows_sells() -> None:
    history = [(datetime.now(UTC) - timedelta(days=1), 1_000_000.0)]
    # 10 лотов SBER lot=1 → 3000 ₽ позиция, 850k NAV → крошечный вес.
    # SELL c size_pct=0.10 на 100k cash → desired=33 лота, current=10 → flip_long_to_short.
    # При DD=15% killswitch блокирует open-часть → close-only fallback, qty=10.
    positions = [{"secid": "SBER", "position": 10, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(signal="SELL"),
        _ctx(
            cash=100_000, nav=850_000, last_price=300.0,
            positions=positions, nav_history=history,
            lot_sizes={"SBER": 1},
        ),
    )
    assert res.allowed
    assert res.qty == 10  # close-only fallback


def test_daily_loss_killswitch_blocks_buys() -> None:
    # Сегодняшний open NAV 100k (по MSK), сейчас 96k → -4% > порога 3%
    today_msk = datetime.now(MSK).replace(hour=10, minute=0, second=0, microsecond=0)
    history = [(today_msk, 100_000.0)]
    res = evaluate(
        _decision(),
        _ctx(cash=50_000, nav=96_000, last_price=300.0, nav_history=history),
    )
    assert not res.allowed
    assert res.gate == "kill_switch_daily_loss"


def test_cash_buffer_blocks_oversized_buy() -> None:
    # Кэш 1000, qty 1 SBER по 990 = nominal 990, > 1000*0.98=980.
    # Чтобы пройти concentration: effective_size = 0.99 (но конкретнее не важно,
    # тут проверяем что qty*price > cash*(1-buffer)).
    res = evaluate(
        _decision(size_pct=0.99),
        _ctx(cash=1000.0, nav=1000.0, last_price=990.0),
    )
    assert not res.allowed
    assert res.gate == "sanity_qty_cash"


def test_cash_check_includes_estimated_commission() -> None:
    res = evaluate(
        _decision(size_pct=1.0),
        _ctx(
            cash=10_000.0,
            nav=10_000.0,
            last_price=100.0,
            lot_sizes={"SBER": 1},
            settings=_settings(
                RISK_MAX_INSTRUMENT_WEIGHT=2.0,
                RISK_MAX_SECTOR_WEIGHT=2.0,
                RISK_CASH_BUFFER=0.0,
                TRADING_COMMISSION_RATE=0.0005,
            ),
        ),
    )
    assert not res.allowed
    assert res.gate == "sanity_qty_cash"
    assert res.metrics["commission"] == 5.0


def test_buy_quantity_returns_lots_not_shares() -> None:
    # 929_944 × 10% / (122.62 × 10) ≈ 75.8 → 75 ЛОТОВ. Реально на ArenaGo это 750 акций
    # = 91 965 ₽. ArenaGo трактует quantity в submit_order как лоты.
    res = evaluate(
        _decision(symbol="GAZP", size_pct=0.10),
        _ctx(
            cash=929_944,
            nav=929_944,
            last_price=122.62,
            lot_sizes={"GAZP": 10},
        ),
    )
    assert res.allowed
    assert res.qty == 75  # ЛОТЫ, не акции
    assert math.isclose(res.metrics["notional"], 91_965.0)  # 75 × 10 × 122.62


def test_unknown_ticker_falls_back_to_lot_size_one() -> None:
    # Неизвестный тикер не должен блокироваться — fallback lot=1.
    res = evaluate(
        _decision(symbol="XXXX", size_pct=0.10),
        _ctx(cash=100_000, nav=100_000, last_price=100.0, lot_sizes={}),
    )
    assert res.allowed
    assert res.qty == 100  # 100k × 10% / (100 × 1) = 100 лотов = 100 акций
    assert res.metrics["lot_size"] == 1.0


def test_tick_allocation_blocks_when_budget_exceeded() -> None:
    # NAV 100k, бюджет 40% = 40k. Уже потратили 35k → попытка купить ещё на
    # ~10k = 10% × 100k должна получить tick_allocation block.
    res = evaluate(
        _decision(size_pct=0.10),
        _ctx(
            cash=100_000,
            nav=100_000,
            last_price=100.0,
            lot_sizes={"SBER": 1},
            settings=_settings(RISK_MAX_TICK_BUY_PCT=0.40),
            tick_open_nav=100_000.0,
            tick_buy_spent=35_000.0,
        ),
    )
    assert not res.allowed
    assert res.gate == "tick_allocation"


def test_tick_allocation_allows_when_under_budget() -> None:
    res = evaluate(
        _decision(size_pct=0.05),
        _ctx(
            cash=100_000,
            nav=100_000,
            last_price=100.0,
            lot_sizes={"SBER": 1},
            settings=_settings(RISK_MAX_TICK_BUY_PCT=0.40),
            tick_open_nav=100_000.0,
            tick_buy_spent=10_000.0,
        ),
    )
    assert res.allowed
    assert res.qty == 50
    assert math.isclose(res.metrics["tick_buy_spent"], 10_000.0)


def test_tick_allocation_disabled_when_pct_zero() -> None:
    # RISK_MAX_TICK_BUY_PCT=0 → гейт пропускает любые spend'ы.
    res = evaluate(
        _decision(size_pct=0.10),
        _ctx(
            cash=1_000_000,
            nav=1_000_000,
            last_price=100.0,
            lot_sizes={"SBER": 1},
            settings=_settings(
                RISK_MAX_TICK_BUY_PCT=0.0,
                RISK_MAX_INSTRUMENT_WEIGHT=2.0,
            ),
            tick_open_nav=1_000_000.0,
            tick_buy_spent=900_000.0,
        ),
    )
    assert res.allowed


def test_sell_passes_when_killswitch_active() -> None:
    # pos=25 лотов (lot=1), SELL size=0.10, cash=100k, price=305 → desired=32.
    # 32 > 25 → flip; killswitch блокирует open-часть → close-only fallback qty=25.
    history = [(datetime.now(UTC) - timedelta(days=1), 1_000_000.0)]
    positions = [{"secid": "SBER", "position": 25, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(signal="SELL"),
        _ctx(
            cash=100_000, nav=850_000, last_price=305.0,
            positions=positions, nav_history=history,
            lot_sizes={"SBER": 1},
        ),
    )
    assert res.allowed
    assert res.qty == 25


# ── Short-selling: sign-aware semantics ──────────────────────────────────────


def test_sell_with_zero_position_opens_short() -> None:
    """SELL без позиции → открыть шорт. qty в ЛОТАХ. lot=1 для простоты."""
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.10),
        _ctx(cash=100_000, nav=100_000, last_price=300.0, lot_sizes={"SBER": 1}),
    )
    assert res.allowed
    assert res.op_type == "open_short"
    # 100k × 0.10 / (300 × 1) = 33 лота
    assert res.qty == 33


def test_sell_extending_short_clipped_by_concentration() -> None:
    """Шорт уже на ~13% NAV → room 1.96% → клип effective_size."""
    positions = [{"secid": "SBER", "position": -50, "average_price": 300, "bot": "t24"}]
    nav = 100_000 + 50 * 300  # 115k (cash включает выручку от шорта)
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.10),
        _ctx(
            cash=100_000, nav=nav, positions=positions, last_price=300.0,
            lot_sizes={"SBER": 1},
        ),
    )
    assert res.allowed
    assert res.op_type == "add_short"
    assert res.effective_size is not None
    assert res.effective_size < 0.10
    # 15% - 13.04% room ≈ 1.96%
    assert math.isclose(res.effective_size, 0.15 - 50 * 300 / nav, abs_tol=1e-6)


def test_sell_extending_short_blocked_at_cap() -> None:
    """|вес| шорта уже ≥ 15% → instrument_concentration block."""
    positions = [{"secid": "SBER", "position": -100, "average_price": 300, "bot": "t24"}]
    nav = 10_000 + 100 * 300  # 40k, |вес| = 75% (lot=1)
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.05),
        _ctx(
            cash=10_000, nav=nav, positions=positions, last_price=300.0,
            lot_sizes={"SBER": 1},
        ),
    )
    assert not res.allowed
    assert res.gate == "instrument_concentration"


def test_buy_covers_existing_short_partially() -> None:
    """BUY на шорте → cover. desired_qty <= |short| → partial cover, bypass."""
    positions = [{"secid": "SBER", "position": -100, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(symbol="SBER", signal="BUY", size_pct=0.10),
        _ctx(
            cash=100_000, nav=100_000, positions=positions, last_price=300.0,
            lot_sizes={"SBER": 1}, settings=_settings(AGENT_ALLOW_FLIP=True),
        ),
    )
    assert res.allowed
    assert res.op_type == "cover_short"
    # desired = 100k × 0.10 / (300 × 1) = 33 лота, < 100 → partial cover
    assert res.qty == 33


def test_short_position_counted_in_sector_concentration() -> None:
    """Шорты добавляют |вес| в sector_weights. Используем lot=1 для упрощения."""
    # LKOH -100 lots × 1 × 200 = 20k. GAZP -150 × 1 × 150 = 22.5k. NAV = 50k + 42.5k = 92.5k.
    # OG sector weight = 42.5/92.5 ≈ 45.9% > 35% cap.
    positions = [
        {"secid": "LKOH", "position": -100, "average_price": 200, "bot": "t24"},
        {"secid": "GAZP", "position": -150, "average_price": 150, "bot": "t24"},
    ]
    nav = 50_000 + 42_500
    res = evaluate(
        _decision(symbol="NVTK", signal="SELL", size_pct=0.05),
        _ctx(
            cash=50_000, nav=nav, positions=positions, last_price=100.0,
            lot_sizes={"LKOH": 1, "GAZP": 1, "NVTK": 1},
        ),
    )
    assert not res.allowed
    assert res.gate == "sector_concentration"


def test_flip_long_to_short_with_strong_signal() -> None:
    """pos=+10 лотов SBER, SELL size_pct=0.10, lot=1 → desired=33 > current=10 → flip."""
    positions = [{"secid": "SBER", "position": 10, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.10),
        _ctx(
            cash=100_000, nav=100_000, positions=positions, last_price=300.0,
            lot_sizes={"SBER": 1}, settings=_settings(AGENT_ALLOW_FLIP=True),
        ),
    )
    assert res.allowed
    assert res.op_type == "flip_long_to_short"
    assert res.flip_close_qty == 10
    # desired = 33 лота, close = 10, open = 23
    assert res.flip_open_qty == 23


def test_flip_short_to_long_with_strong_signal() -> None:
    """pos=-10 лотов SBER, BUY size_pct=0.10, lot=1 → desired=33 > |pos|=10 → flip."""
    positions = [{"secid": "SBER", "position": -10, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(symbol="SBER", signal="BUY", size_pct=0.10),
        _ctx(
            cash=100_000, nav=100_000, positions=positions, last_price=300.0,
            lot_sizes={"SBER": 1}, settings=_settings(AGENT_ALLOW_FLIP=True),
        ),
    )
    assert res.allowed
    assert res.op_type == "flip_short_to_long"
    assert res.flip_close_qty == 10
    assert res.flip_open_qty == 23


def test_flip_falls_back_to_close_only_when_killswitch_active() -> None:
    """Killswitch блочит open-часть flip'а → отправляем только close-часть."""
    today_msk = datetime.now(MSK).replace(hour=10, minute=0, second=0, microsecond=0)
    history = [
        (today_msk - timedelta(days=2), 1_000_000.0),
        (today_msk, 1_000_000.0),  # session open
    ]
    positions = [{"secid": "SBER", "position": 10, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.10),
        _ctx(
            cash=100_000, nav=850_000,  # DD 15% > 10% cap
            positions=positions, last_price=300.0,
            nav_history=history, lot_sizes={"SBER": 1},
            settings=_settings(AGENT_ALLOW_FLIP=True),
        ),
    )
    assert res.allowed
    # Close-only fallback: qty = current_qty (10 лотов), no flip_open_qty
    assert res.qty == 10
    assert res.flip_open_qty is None
    assert res.op_type == "close_long"


def test_risk_disabled_bypasses_all_gates() -> None:
    # Confidence 0.0 — обычно блокируется sanity_confidence, но при RISK_ENABLED=false проходит.
    res = evaluate(
        _decision(confidence=0.0),
        _ctx(settings=_settings(RISK_ENABLED=False)),
    )
    assert res.allowed
    assert res.gate == "disabled"


def test_sanity_price_blocks() -> None:
    res = evaluate(_decision(), _ctx(last_price=0.0))
    assert not res.allowed
    assert res.gate == "sanity_price"


# ── Журнал → load_nav_history ────────────────────────────────────────────────


def test_load_nav_history_reads_versioned_tick_events(tmp_path: Path) -> None:
    from agent.runtime.risk import NAV_CALC_VERSION

    j = JsonlJournal(tmp_path / "decisions.jsonl")
    now = datetime.now(UTC)
    # tick без маркера версии (старый багнутый) — должен игнорироваться;
    # tick_skipped — игнор; два tick с текущим маркером — учитываются.
    j.write("tick", nav=1_800_000.0)  # фантом без nav_calc → не считаем
    j.write("tick_skipped", reason="weekend")
    j.write("tick", nav=1_000_000.0, nav_calc=NAV_CALC_VERSION)
    j.write("tick", nav=1_010_000.0, nav_calc=NAV_CALC_VERSION)
    history = load_nav_history(j, lookback_days=5)
    assert len(history) == 2  # фантомный 1.8M отфильтрован
    assert history[0][1] == 1_000_000.0
    assert history[1][1] == 1_010_000.0
    _ = now


def test_load_nav_history_ignores_phantom_without_marker(tmp_path: Path) -> None:
    """Старый журнал с багнутым NAV (без nav_calc) → пустая история → фантома нет."""
    j = JsonlJournal(tmp_path / "decisions.jsonl")
    j.write("tick", nav=1_800_000.0)  # старая запись без маркера
    j.write("tick", nav=1_750_000.0)
    history = load_nav_history(j, lookback_days=5)
    assert history == []


# ── Интеграционный: scheduler пишет risk_block в журнал ─────────────────────


class _GraphFixed:
    def __init__(self, state: dict) -> None:
        self._state = state

    def invoke(self, _input):
        return self._state


class _ArenagoStub:
    def __init__(self, cash: float, positions: list[dict]) -> None:
        self._cash = cash
        self._positions = positions
        self.submitted: list[dict] = []
        self.bot = "t24"

    def get_portfolio(self) -> dict:
        return {"bot": self.bot, "cash": self._cash, "positions": self._positions}

    def get_trades(self) -> list[dict]:
        return []

    def submit_order(self, secid, direction, quantity):
        self.submitted.append({"secid": secid, "direction": direction, "quantity": quantity})
        return {"success": True, "status": "DRY_RUN"}

    def close(self) -> None:
        pass


def _scheduler_settings(tmp_path: Path, **risk) -> SimpleNamespace:
    return SimpleNamespace(
        AGENT_TICKERS="SBER",
        AGENT_TICK_MINUTES=30,
        AGENT_INTERVAL=60,
        AGENT_RESPECT_MOEX_HOURS=False,
        DRY_RUN=True,
        DATA_DIR=str(tmp_path),
        ARENAGO_DAILY_TRADE_LIMIT=200,
        **{
            **dict(
                RISK_ENABLED=True,
                RISK_MIN_CONFIDENCE=0.35,
                RISK_MAX_INSTRUMENT_WEIGHT=0.15,
                RISK_MAX_SECTOR_WEIGHT=0.35,
                RISK_MAX_VAR_PCT=0.04,
                RISK_MAX_DRAWDOWN=0.10,
                RISK_MAX_DAILY_LOSS=0.03,
                RISK_CASH_BUFFER=0.02,
                RISK_VAR_LOOKBACK=60,
                RISK_NAV_HISTORY_DAYS=5,
                RISK_MAX_TICK_BUY_PCT=0.0,
                TRADING_COMMISSION_RATE=0.0,
                RISK_TRIM_ENABLED=False,
                RISK_TRIM_BAND=0.10,
                RISK_TRIM_LOSS_TOLERANCE=0.0,
                RISK_TRIM_STOP_PCT=0.03,
                RISK_TRIM_MAX_PCT_PER_TICK=0.0,
                AGENT_ALLOW_FLIP=False,
                RISK_TAKE_PROFIT_PCT=0.0,
                RISK_STOP_LOSS_PCT=0.0,
            ),
            **risk,
        },
    )


def _make_state(symbol: str, signal: str, confidence: float, size_pct: float, last_price: float):
    candle = SimpleNamespace(close=last_price)
    snapshot = SimpleNamespace(candles=[candle])
    return {
        "decision": _decision(symbol, signal, size_pct, confidence),
        "snapshot": snapshot,
    }


@pytest.mark.asyncio
async def test_journal_emits_risk_block_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scheduler_mod, "is_tradable", lambda **k: True)
    arenago = _ArenagoStub(cash=100_000, positions=[])
    # Confidence 0.2 → должен сработать sanity_confidence.
    state = _make_state("SBER", "BUY", confidence=0.2, size_pct=0.1, last_price=300.0)
    s = TradingScheduler(
        graph=_GraphFixed(state),
        arenago=arenago,
        settings=_scheduler_settings(tmp_path),
    )
    await s.run_once()
    assert arenago.submitted == []
    records = s.journal.tail(20)
    blocks = [r for r in records if r["event"] == "risk_block"]
    assert blocks, "expected at least one risk_block event in journal"
    assert blocks[0]["gate"] == "sanity_confidence"
    assert blocks[0]["symbol"] == "SBER"


# ── risk-initiated trim (profit-gated + стоп) ────────────────────────────────


def _trim_settings(**overrides):
    base = dict(RISK_TRIM_ENABLED=True, RISK_MAX_INSTRUMENT_WEIGHT=0.15)
    base.update(overrides)
    return _settings(**base)


def test_risk_trim_oversized_short_in_profit_covers_to_cap() -> None:
    # Шорт −500 лотов SBER (lot 1) @ avg 300, цена упала до 270 → шорт в плюсе.
    # weight = 500×270/nav; nav=100k+135k=235k → 57% >> cap×1.1=16.5% → trim.
    pos = [{"secid": "SBER", "position": -500, "average_price": 300.0}]
    nav = 100_000 + 500 * 270
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(cash=100_000, nav=nav, positions=pos, last_price=270.0,
             settings=_trim_settings()),
    )
    assert res.allowed
    assert res.gate == "risk_trim"
    assert res.op_type == "risk_trim_cover"
    cap_qty = int(0.15 * nav / 270.0)
    assert res.qty == 500 - cap_qty
    assert res.metrics["trim_pnl_pct"] > 0  # в плюсе


def test_risk_trim_overrides_hold() -> None:
    # Ключевой кейс GMKN: LLM сказал HOLD, но позиция раздута → trim перехватывает.
    pos = [{"secid": "SBER", "position": -500, "average_price": 300.0}]
    nav = 100_000 + 500 * 270
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(cash=100_000, nav=nav, positions=pos, last_price=270.0,
             settings=_trim_settings()),
    )
    assert res.gate == "risk_trim"  # не "hold"


def test_risk_trim_waits_on_shallow_loss() -> None:
    # Шорт в мелком минусе (−2%, между −tol=0 и −stop=3%) → НЕ режем, ждём.
    pos = [{"secid": "SBER", "position": -500, "average_price": 300.0}]
    nav = 100_000 + 500 * 306
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(cash=100_000, nav=nav, positions=pos, last_price=306.0,
             settings=_trim_settings()),
    )
    assert res.gate == "hold"  # trim не сработал


def test_risk_trim_fires_on_stop_loss() -> None:
    # Шорт в просадке −4% (≥ stop 3%) → режем несмотря на минус (стоп-лосс).
    pos = [{"secid": "SBER", "position": -500, "average_price": 300.0}]
    nav = 100_000 + 500 * 312
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(cash=100_000, nav=nav, positions=pos, last_price=312.0,
             settings=_trim_settings()),
    )
    assert res.gate == "risk_trim"
    assert res.op_type == "risk_trim_cover"
    assert res.metrics["trim_pnl_pct"] < 0  # режем в минусе по стопу


def test_risk_trim_oversized_long_sells_to_cap() -> None:
    # Лонг +500 @ avg 100, цена 110 → в плюсе, раздут → SELL к кэпу.
    pos = [{"secid": "SBER", "position": 500, "average_price": 100.0}]
    nav = 100_000 + 500 * 110
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(cash=100_000, nav=nav, positions=pos, last_price=110.0,
             settings=_trim_settings()),
    )
    assert res.gate == "risk_trim"
    assert res.op_type == "risk_trim_sell"


def test_risk_trim_skips_within_cap() -> None:
    # Позиция −100 лотов ×300 = 30k, nav 130k → 23%... делаем меньше: −30 ×300=9k
    # nav=109k → 8.3% < 16.5% → trim не трогает, обычная логика.
    pos = [{"secid": "SBER", "position": -30, "average_price": 300.0}]
    nav = 100_000 + 30 * 300
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(cash=100_000, nav=nav, positions=pos, last_price=300.0,
             settings=_trim_settings()),
    )
    assert res.gate == "hold"


def test_risk_trim_disabled_falls_through() -> None:
    pos = [{"secid": "SBER", "position": -500, "average_price": 300.0}]
    nav = 100_000 + 500 * 270
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(cash=100_000, nav=nav, positions=pos, last_price=270.0,
             settings=_trim_settings(RISK_TRIM_ENABLED=False)),
    )
    assert res.gate == "hold"  # выключено → обычный HOLD


# ── min_edge (vol-grounded) ──────────────────────────────────────────────────


def _edge_settings(**o):
    base = dict(RISK_MIN_EDGE_PCT=0.003, RISK_EDGE_VOL_MULT=1.0,
                RISK_MAX_VAR_PCT=1.0)  # VaR фактически off, чтобы не мешал
    base.update(o)
    return _settings(**base)


def test_min_edge_blocks_low_volatility() -> None:
    # σ ≈ 0.0001 → estimated_edge = 0.7×0.0001 ≈ 0.00007 < required 0.003 → блок.
    returns = [0.0001, -0.0001] * 5
    res = evaluate(
        _decision(signal="BUY", confidence=0.7),
        _ctx(returns=returns, settings=_edge_settings()),
    )
    assert not res.allowed
    assert res.gate == "min_edge"


def test_min_edge_passes_high_volatility() -> None:
    # σ ≈ 0.03 → estimated_edge = 0.7×0.03 = 0.021 >> 0.003 → min_edge не блокирует.
    returns = [0.03, -0.03] * 5
    res = evaluate(
        _decision(signal="BUY", confidence=0.7),
        _ctx(returns=returns, settings=_edge_settings()),
    )
    assert res.gate != "min_edge"


def test_min_edge_skipped_on_insufficient_history() -> None:
    # < 5 точек истории → гейт не применяется (не блокируем на пустоте).
    res = evaluate(
        _decision(signal="BUY", confidence=0.7),
        _ctx(returns=[0.0001, -0.0001], settings=_edge_settings()),
    )
    assert res.gate != "min_edge"


# ── no-flip discipline ───────────────────────────────────────────────────────


def test_noflip_sell_on_long_closes_to_flat() -> None:
    # pos +30, SELL size_pct=0.10 @300 cash 100k → desired=33 > 30; no-flip →
    # close_long до флэта qty=30, обратная сторона не открывается.
    positions = [{"secid": "SBER", "position": 30, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.10),
        _ctx(cash=100_000, nav=100_000, positions=positions, last_price=300.0,
             lot_sizes={"SBER": 1}),  # _settings по умолчанию AGENT_ALLOW_FLIP=False
    )
    assert res.allowed
    assert res.op_type == "close_long"
    assert res.qty == 30
    assert res.flip_open_qty is None


def test_noflip_buy_on_short_covers_to_flat() -> None:
    positions = [{"secid": "SBER", "position": -20, "average_price": 300, "bot": "t24"}]
    res = evaluate(
        _decision(symbol="SBER", signal="BUY", size_pct=0.10),
        _ctx(cash=100_000, nav=100_000, positions=positions, last_price=300.0,
             lot_sizes={"SBER": 1}),
    )
    assert res.allowed
    assert res.op_type == "cover_short"
    assert res.qty == 20
    assert res.flip_open_qty is None


# ── fixed TP/SL bracket ──────────────────────────────────────────────────────


def _pnl_settings(**o):
    base = dict(RISK_TAKE_PROFIT_PCT=0.02, RISK_STOP_LOSS_PCT=0.03)
    base.update(o)
    return _settings(**base)


def _profit_lock_settings(**o):
    base = dict(
        RISK_TAKE_PROFIT_PCT=0.0,
        RISK_STOP_LOSS_PCT=0.02,
        RISK_PROFIT_TAKE_ENABLED=True,
        RISK_PROFIT_LOCK_PCT=0.007,
        RISK_PROFIT_PARTIAL_PCT=0.012,
        RISK_PROFIT_FULL_PCT=0.020,
        RISK_PROFIT_LOCK_FRACTION=0.50,
        RISK_PROFIT_PARTIAL_FRACTION=0.50,
    )
    base.update(o)
    return _settings(**base)


def test_take_profit_short_in_profit() -> None:
    # шорт -50 @ avg 300, цена 290 → pnl=(300-290)/300=+3.33% ≥ 2% → take_profit_cover.
    pos = [{"secid": "SBER", "position": -50, "average_price": 300.0}]
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(positions=pos, last_price=290.0, settings=_pnl_settings()),
    )
    assert res.allowed and res.gate == "take_profit"
    assert res.op_type == "take_profit_cover"
    assert res.qty == 50


def test_take_profit_long_in_profit() -> None:
    # лонг +50 @ avg 100, цена 103 → +3% ≥ 2% → take_profit_sell.
    pos = [{"secid": "SBER", "position": 50, "average_price": 100.0}]
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(positions=pos, last_price=103.0, settings=_pnl_settings()),
    )
    assert res.gate == "take_profit"


def test_profit_lock_long_supportive_signal_does_not_close() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="BUY", size_pct=0.05),
        _ctx(positions=pos, last_price=100.8, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.op_type != "take_profit_sell"


def test_profit_lock_long_hold_closes_partial_once() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="HOLD"),
        _ctx(positions=pos, last_price=100.8, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "take_profit"
    assert res.op_type == "take_profit_sell"
    assert res.qty == 5
    assert res.metrics["profit_step"] == 0.007
    assert res.metrics["close_fraction"] == 0.5


def test_profit_lock_zero_fraction_does_not_force_one_lot_close() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="HOLD"),
        _ctx(
            positions=pos,
            last_price=100.8,
            settings=_profit_lock_settings(RISK_PROFIT_LOCK_FRACTION=0.0),
        ),
    )

    assert res.allowed
    assert res.gate == "hold"


def test_profit_lock_long_opposite_signal_closes_partial_once() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.05),
        _ctx(positions=pos, last_price=100.8, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "take_profit"
    assert res.op_type == "take_profit_sell"
    assert res.qty == 5


def test_profit_lock_short_hold_covers_partial_once() -> None:
    pos = [{"secid": "SBER", "position": -10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="HOLD"),
        _ctx(positions=pos, last_price=99.2, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "take_profit"
    assert res.op_type == "take_profit_cover"
    assert res.qty == 5
    assert res.metrics["profit_step"] == 0.007


def test_profit_lock_short_opposite_signal_covers_partial_once() -> None:
    pos = [{"secid": "SBER", "position": -10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="BUY", size_pct=0.05),
        _ctx(positions=pos, last_price=99.2, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "take_profit"
    assert res.op_type == "take_profit_cover"
    assert res.qty == 5


def test_profit_partial_closes_despite_supportive_signal() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="BUY", size_pct=0.05),
        _ctx(positions=pos, last_price=101.3, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "take_profit"
    assert res.op_type == "take_profit_sell"
    assert res.qty == 5
    assert res.metrics["profit_step"] == 0.012


def test_profit_partial_zero_fraction_does_not_force_one_lot_close() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="HOLD"),
        _ctx(
            positions=pos,
            last_price=101.3,
            settings=_profit_lock_settings(
                RISK_PROFIT_LOCK_PCT=0.0,
                RISK_PROFIT_PARTIAL_FRACTION=0.0,
            ),
        ),
    )

    assert res.allowed
    assert res.gate == "hold"


def test_profit_partial_short_closes_despite_supportive_signal() -> None:
    pos = [{"secid": "SBER", "position": -10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.05),
        _ctx(positions=pos, last_price=98.7, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "take_profit"
    assert res.op_type == "take_profit_cover"
    assert res.qty == 5
    assert res.metrics["profit_step"] == 0.012


def test_profit_full_closes_entire_position() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="BUY", size_pct=0.05),
        _ctx(positions=pos, last_price=102.1, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "take_profit"
    assert res.op_type == "take_profit_sell"
    assert res.qty == 10
    assert res.metrics["profit_step"] == 0.02
    assert res.metrics["close_fraction"] == 1.0


def test_profit_full_short_closes_entire_position() -> None:
    pos = [{"secid": "SBER", "position": -10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="SELL", size_pct=0.05),
        _ctx(positions=pos, last_price=97.9, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "take_profit"
    assert res.op_type == "take_profit_cover"
    assert res.qty == 10
    assert res.metrics["profit_step"] == 0.02
    assert res.metrics["close_fraction"] == 1.0


def test_profit_partial_step_does_not_repeat() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="HOLD"),
        _ctx(
            positions=pos,
            last_price=100.8,
            settings=_profit_lock_settings(),
            profit_steps_done={"profit_lock"},
        ),
    )

    assert res.allowed
    assert res.gate == "hold"


def test_profit_lock_stop_loss_closes_entire_position() -> None:
    pos = [{"secid": "SBER", "position": 10, "average_price": 100.0}]
    res = evaluate(
        _decision(symbol="SBER", signal="BUY", size_pct=0.05),
        _ctx(positions=pos, last_price=97.9, settings=_profit_lock_settings()),
    )

    assert res.allowed
    assert res.gate == "stop_loss"
    assert res.op_type == "stop_loss_sell"
    assert res.qty == 10
    assert res.metrics["close_fraction"] == 1.0


def test_stop_loss_fires() -> None:
    # шорт -50 @ avg 300, цена 315 → pnl=(300-315)/300=-5% ≤ -3% → stop_loss_cover.
    pos = [{"secid": "SBER", "position": -50, "average_price": 300.0}]
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(positions=pos, last_price=315.0, settings=_pnl_settings()),
    )
    assert res.gate == "stop_loss"
    assert res.op_type == "stop_loss_cover"


def test_pnl_exit_holds_between_thresholds() -> None:
    # шорт -50 @ 300, цена 297 → pnl=+1% (между 0 и TP 2%, выше -SL) → не срабатывает.
    pos = [{"secid": "SBER", "position": -50, "average_price": 300.0}]
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(positions=pos, last_price=297.0, settings=_pnl_settings()),
    )
    assert res.gate == "hold"


def test_pnl_exit_overrides_hold() -> None:
    # На HOLD-решении TP всё равно закрывает (reduce-override выше сигнала LLM).
    pos = [{"secid": "SBER", "position": -50, "average_price": 300.0}]
    res = evaluate(
        _decision(signal="HOLD"),
        _ctx(positions=pos, last_price=290.0, settings=_pnl_settings()),
    )
    assert res.gate == "take_profit"
