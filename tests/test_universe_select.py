from __future__ import annotations

from agent.runtime.universe_select import select_llm_tickers


def test_select_llm_tickers_prioritizes_positions() -> None:
    universe = ("SBER", "GAZP", "LKOH", "ROSN", "VTBR")
    positions = [{"secid": "LKOH", "position": 5}]
    selected = select_llm_tickers(universe, positions, max_per_tick=2)
    assert selected[0] == "LKOH"
    assert len(selected) == 2


def test_select_all_when_cap_zero() -> None:
    universe = ("SBER", "GAZP")
    selected = select_llm_tickers(universe, [], max_per_tick=0)
    assert selected == universe
