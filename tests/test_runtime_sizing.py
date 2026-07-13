"""Position sizing для BUY и подсчёт текущей позиции."""

from __future__ import annotations

from agent.runtime.sizing import position_for, quantity_for_buy
from agent.runtime.universe import DEFAULT_UNIVERSE, parse_universe


def test_quantity_for_buy_lot1_basic() -> None:
    # 100 000 ₽, 10% → 10 000 ₽; цена 305, lot=1 → 32 ЛОТА (= 32 акции)
    assert quantity_for_buy(cash=100_000, size_pct=0.10, last_price=305.0) == 32


def test_quantity_for_buy_returns_lots_for_high_lot_ticker() -> None:
    # ArenaGo трактует quantity как лоты. На GAZP (lot=10) при 929_944 × 10% / 122.62 ≈ 758
    # акций мы хотим отправить 75 ЛОТОВ (что = 750 акций фактически у ArenaGo).
    assert quantity_for_buy(
        cash=929_944,
        size_pct=0.10,
        last_price=122.62,
        lot_size=10,
    ) == 75


def test_quantity_for_buy_lot100() -> None:
    # SNGSP lot=100. cash 10М × 5% = 500k / (37.5 × 100) = 133 лота
    assert quantity_for_buy(
        cash=10_000_000, size_pct=0.05, last_price=37.5, lot_size=100
    ) == 133


def test_quantity_for_buy_zero_on_bad_inputs() -> None:
    assert quantity_for_buy(0, 0.1, 100) == 0
    assert quantity_for_buy(100, 0, 100) == 0
    assert quantity_for_buy(100, 0.1, 0) == 0
    assert quantity_for_buy(-100, 0.1, 100) == 0
    assert quantity_for_buy(100, 0.1, 100, lot_size=0) == 0
    assert quantity_for_buy(100, 0.1, 100, lot_size=-5) == 0


def test_quantity_floor_not_round() -> None:
    # 1.99 лота → 1
    assert quantity_for_buy(cash=199, size_pct=1.0, last_price=100.0, lot_size=1) == 1


def test_position_for_finds_match() -> None:
    positions = [
        {"secid": "SBER", "position": 50, "average_price": 305.0, "bot": "t24"},
        {"secid": "GAZP", "position": 100, "average_price": 150.0, "bot": "t24"},
    ]
    assert position_for(positions, "SBER") == 50
    assert position_for(positions, "GAZP") == 100
    assert position_for(positions, "LKOH") == 0  # нет позиции


def test_universe_default_has_all_20_tickers() -> None:
    expected = {
        "LKOH", "SBER", "ROSN", "GAZP", "VTBR", "YDEX", "PLZL", "T", "NVTK",
        "X5", "GMKN", "MGNT", "ALRS", "AFLT", "CHMF", "NLMK", "MOEX", "SNGSP",
        "MTSS", "PIKK",
    }
    assert set(DEFAULT_UNIVERSE) == expected
    assert len(DEFAULT_UNIVERSE) == len(expected) == 20
    # PIKK подтверждён в финальном списке конкурса ArenaGo (20 тикеров).
    assert "PIKK" in DEFAULT_UNIVERSE


def test_universe_parse_csv() -> None:
    assert parse_universe("SBER,gazp, lkoh") == ("SBER", "GAZP", "LKOH")
    assert parse_universe("SBER,gazp,SBER, GAZP") == ("SBER", "GAZP")
    assert parse_universe("") == DEFAULT_UNIVERSE
    assert parse_universe("  ") == DEFAULT_UNIVERSE
    assert parse_universe(None) == DEFAULT_UNIVERSE  # type: ignore[arg-type]
