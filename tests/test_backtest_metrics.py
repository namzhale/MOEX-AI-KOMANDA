"""Метрики бэктеста — детерминированные проверки на синтетике."""

from __future__ import annotations

import math

import numpy as np

from agent.backtest import metrics as m


def test_total_return() -> None:
    eq = np.array([100.0, 110.0, 121.0])
    assert math.isclose(m.total_return(eq), 0.21, abs_tol=1e-9)


def test_total_return_empty_or_zero() -> None:
    assert m.total_return(np.array([100.0])) == 0.0
    assert m.total_return(np.array([0.0, 100.0])) == 0.0


def test_to_returns() -> None:
    eq = np.array([100.0, 110.0, 99.0])
    rets = m.to_returns(eq)
    assert np.allclose(rets, [0.1, -0.1])


def test_max_drawdown() -> None:
    # пик 120, дно 90 → DD = (120-90)/120 = 0.25
    eq = np.array([100.0, 120.0, 90.0, 110.0])
    assert math.isclose(m.max_drawdown(eq), 0.25, abs_tol=1e-9)


def test_max_drawdown_monotonic_up_is_zero() -> None:
    eq = np.array([100.0, 105.0, 110.0])
    assert m.max_drawdown(eq) == 0.0


def test_sharpe_zero_vol_returns_zero() -> None:
    rets = np.array([0.01, 0.01, 0.01, 0.01])
    assert m.sharpe(rets) == 0.0


def test_sharpe_positive_for_steady_gains_with_noise() -> None:
    rng = np.random.default_rng(0)
    rets = 0.001 + rng.normal(0, 0.005, size=300)
    assert m.sharpe(rets) > 0


def test_sortino_no_downside_returns_zero() -> None:
    rets = np.array([0.01, 0.02, 0.0, 0.015])
    assert m.sortino(rets) == 0.0


def test_calmar_uses_cagr_over_maxdd() -> None:
    eq = np.array([100.0, 120.0, 90.0, 130.0])
    c = m.calmar(eq, periods_per_year=252)
    # знак положительный (итог выше старта), конечен
    assert c > 0
    assert math.isfinite(c)


def test_bootstrap_sharpe_p5_below_point_sharpe() -> None:
    rng = np.random.default_rng(1)
    rets = 0.001 + rng.normal(0, 0.01, size=500)
    point = m.sharpe(rets)
    p5 = m.bootstrap_sharpe_p5(rets, n_boot=200)
    assert p5 <= point  # 5-й перцентиль не выше точечного


def test_turnover() -> None:
    # два периода: с [1,0] на [0,1] → оборот 0.5×(1+1)=1.0
    w = np.array([[1.0, 0.0], [0.0, 1.0]])
    assert math.isclose(m.turnover(w), 1.0, abs_tol=1e-9)


def test_hit_rate() -> None:
    assert m.hit_rate([1.0, -2.0, 3.0, -1.0]) == 0.5
    assert m.hit_rate([]) == 0.0


def test_build_report_smoke() -> None:
    eq = np.array([1_000_000.0, 1_010_000.0, 1_005_000.0, 1_020_000.0])
    rep = m.build_report(eq, periods_per_year=252, trade_pnls=[100.0, -50.0])
    row = rep.as_row()
    assert row["final_equity"] == 1_020_000.0
    assert row["n_trades"] == 2
    assert "sharpe" in row and "max_dd" in row
