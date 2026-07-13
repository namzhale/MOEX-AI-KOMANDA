"""Метрики качества бэктеста — чистые функции над рядами доходностей/equity.

Все функции принимают numpy-массивы и не делают I/O. periods_per_year
позволяет считать одинаково для дневных (≈252) и часовых (≈252×8) баров.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

TRADING_DAYS = 252


def to_returns(equity: np.ndarray) -> np.ndarray:
    """Периодические простые доходности из кривой equity."""
    equity = np.asarray(equity, dtype=float)
    if equity.size < 2:
        return np.array([], dtype=float)
    prev = equity[:-1]
    # защита от деления на 0 / отрицательного equity
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.where(prev != 0, (equity[1:] - prev) / prev, 0.0)
    return np.nan_to_num(rets, nan=0.0, posinf=0.0, neginf=0.0)


def total_return(equity: np.ndarray) -> float:
    equity = np.asarray(equity, dtype=float)
    if equity.size < 2 or equity[0] == 0:
        return 0.0
    return float(equity[-1] / equity[0] - 1.0)


def cagr(equity: np.ndarray, periods_per_year: int = TRADING_DAYS) -> float:
    equity = np.asarray(equity, dtype=float)
    n = equity.size
    if n < 2 or equity[0] <= 0 or equity[-1] <= 0:
        return 0.0
    years = (n - 1) / periods_per_year
    if years <= 0:
        return 0.0
    return float((equity[-1] / equity[0]) ** (1.0 / years) - 1.0)


def volatility(returns: np.ndarray, periods_per_year: int = TRADING_DAYS) -> float:
    returns = np.asarray(returns, dtype=float)
    if returns.size < 2:
        return 0.0
    return float(np.std(returns, ddof=1) * math.sqrt(periods_per_year))


def sharpe(
    returns: np.ndarray,
    periods_per_year: int = TRADING_DAYS,
    risk_free: float = 0.0,
) -> float:
    """Annualized Sharpe. risk_free — годовая ставка (0 по умолчанию)."""
    returns = np.asarray(returns, dtype=float)
    if returns.size < 2:
        return 0.0
    rf_period = risk_free / periods_per_year
    excess = returns - rf_period
    sd = np.std(excess, ddof=1)
    if sd == 0:
        return 0.0
    return float(np.mean(excess) / sd * math.sqrt(periods_per_year))


def sortino(
    returns: np.ndarray,
    periods_per_year: int = TRADING_DAYS,
    risk_free: float = 0.0,
) -> float:
    """Annualized Sortino — как Sharpe, но в знаменателе только downside dev."""
    returns = np.asarray(returns, dtype=float)
    if returns.size < 2:
        return 0.0
    rf_period = risk_free / periods_per_year
    excess = returns - rf_period
    downside = excess[excess < 0]
    if downside.size == 0:
        return 0.0
    dd = math.sqrt(float(np.mean(downside**2)))
    if dd == 0:
        return 0.0
    return float(np.mean(excess) / dd * math.sqrt(periods_per_year))


def max_drawdown(equity: np.ndarray) -> float:
    """Максимальная просадка (положительная доля, 0.10 = 10%)."""
    equity = np.asarray(equity, dtype=float)
    if equity.size < 2:
        return 0.0
    running_peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(running_peak > 0, (running_peak - equity) / running_peak, 0.0)
    return float(np.nan_to_num(dd, nan=0.0).max())


def calmar(equity: np.ndarray, periods_per_year: int = TRADING_DAYS) -> float:
    mdd = max_drawdown(equity)
    if mdd == 0:
        return 0.0
    return float(cagr(equity, periods_per_year) / mdd)


def bootstrap_sharpe_p5(
    returns: np.ndarray,
    periods_per_year: int = TRADING_DAYS,
    n_boot: int = 200,
    seed: int = 42,
) -> float:
    """5-й перцентиль Sharpe по n_boot бутстрэп-ресэмплам (с возвращением).
    Защита от «удачного» прогона: если p5 заметно ниже точечного Sharpe —
    результат хрупкий. (AgentQuant pattern.)"""
    returns = np.asarray(returns, dtype=float)
    if returns.size < 10:
        return 0.0
    rng = np.random.default_rng(seed)
    n = returns.size
    sharpes = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        sample = returns[rng.integers(0, n, size=n)]
        sharpes[i] = sharpe(sample, periods_per_year)
    return float(np.percentile(sharpes, 5))


def turnover(weights: np.ndarray) -> float:
    """Средний оборот: 0.5 × Σ|w_t − w_{t-1}| усреднённый по периодам.
    weights — матрица [T × N] долей по инструментам."""
    weights = np.asarray(weights, dtype=float)
    if weights.ndim != 2 or weights.shape[0] < 2:
        return 0.0
    diffs = np.abs(np.diff(weights, axis=0)).sum(axis=1) * 0.5
    return float(np.mean(diffs))


def hit_rate(trade_pnls: list[float]) -> float:
    """Доля прибыльных закрытых сделок."""
    if not trade_pnls:
        return 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls)


@dataclass
class BacktestReport:
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    volatility: float
    bootstrap_sharpe_p5: float
    hit_rate: float
    turnover: float
    n_trades: int
    final_equity: float

    def as_row(self) -> dict[str, float]:
        return {
            "total_return": round(self.total_return, 4),
            "cagr": round(self.cagr, 4),
            "sharpe": round(self.sharpe, 3),
            "sortino": round(self.sortino, 3),
            "calmar": round(self.calmar, 3),
            "max_dd": round(self.max_drawdown, 4),
            "vol": round(self.volatility, 4),
            "boot_sharpe_p5": round(self.bootstrap_sharpe_p5, 3),
            "hit_rate": round(self.hit_rate, 3),
            "turnover": round(self.turnover, 4),
            "n_trades": self.n_trades,
            "final_equity": round(self.final_equity, 2),
        }


def build_report(
    equity: np.ndarray,
    *,
    periods_per_year: int = TRADING_DAYS,
    trade_pnls: list[float] | None = None,
    weights: np.ndarray | None = None,
    n_trades: int | None = None,
) -> BacktestReport:
    equity = np.asarray(equity, dtype=float)
    rets = to_returns(equity)
    trade_pnls = trade_pnls or []
    return BacktestReport(
        total_return=total_return(equity),
        cagr=cagr(equity, periods_per_year),
        sharpe=sharpe(rets, periods_per_year),
        sortino=sortino(rets, periods_per_year),
        calmar=calmar(equity, periods_per_year),
        max_drawdown=max_drawdown(equity),
        volatility=volatility(rets, periods_per_year),
        bootstrap_sharpe_p5=bootstrap_sharpe_p5(rets, periods_per_year),
        hit_rate=hit_rate(trade_pnls),
        turnover=turnover(weights) if weights is not None else 0.0,
        n_trades=n_trades if n_trades is not None else len(trade_pnls),
        final_equity=float(equity[-1]) if equity.size else 0.0,
    )
