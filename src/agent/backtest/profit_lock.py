from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from types import SimpleNamespace

from agent.backtest.engine import run_backtest
from agent.backtest.strategy import Strategy


MIN_ACTIVITY_RATIO = 0.70
TP_CONTINUATION_WARN_RATE = 0.60
TP_CONTINUATION_MIN_EXITS = 3


PROFIT_LOCK_CANDIDATES = {
    "baseline": {
        "RISK_PROFIT_TAKE_ENABLED": False,
        "RISK_TAKE_PROFIT_PCT": 0.015,
    },
    "bracket_tp20": {
        "RISK_PROFIT_TAKE_ENABLED": False,
        "RISK_TAKE_PROFIT_PCT": 0.020,
    },
    "candidate_a": {
        "RISK_PROFIT_TAKE_ENABLED": True,
        "RISK_TAKE_PROFIT_PCT": 0.0,
        "RISK_PROFIT_LOCK_PCT": 0.007,
        "RISK_PROFIT_PARTIAL_PCT": 0.012,
        "RISK_PROFIT_FULL_PCT": 0.020,
        "RISK_PROFIT_LOCK_FRACTION": 0.50,
        "RISK_PROFIT_PARTIAL_FRACTION": 0.50,
    },
    "candidate_b": {
        "RISK_PROFIT_TAKE_ENABLED": True,
        "RISK_TAKE_PROFIT_PCT": 0.0,
        "RISK_PROFIT_LOCK_PCT": 0.0,
        "RISK_PROFIT_PARTIAL_PCT": 0.010,
        "RISK_PROFIT_FULL_PCT": 0.020,
        "RISK_PROFIT_LOCK_FRACTION": 0.0,
        "RISK_PROFIT_PARTIAL_FRACTION": 0.50,
    },
    "candidate_c": {
        "RISK_PROFIT_TAKE_ENABLED": True,
        "RISK_TAKE_PROFIT_PCT": 0.0,
        "RISK_PROFIT_LOCK_PCT": 0.0,
        "RISK_PROFIT_PARTIAL_PCT": 0.0,
        "RISK_PROFIT_FULL_PCT": 0.007,
        "RISK_PROFIT_LOCK_FRACTION": 0.0,
        "RISK_PROFIT_PARTIAL_FRACTION": 0.0,
    },
}


def settings_for_profit_lock_candidate(settings, name: str):
    if name not in PROFIT_LOCK_CANDIDATES:
        raise KeyError(f"unknown profit-lock candidate: {name}")
    out = copy(settings)
    if isinstance(settings, SimpleNamespace):
        out = SimpleNamespace(**vars(settings))
    for key, value in PROFIT_LOCK_CANDIDATES[name].items():
        setattr(out, key, value)
    return out


@dataclass(frozen=True)
class ProfitLockActivityRow:
    candidate: str
    total_return: float
    pnl_after_commission: float
    trades_per_day: float
    gross_turnover_per_day: float
    avg_exposure_pct: float
    flat_time_pct: float
    avg_holding_bars: float
    tp_exit_count: int
    tp_continuation_05_rate: float
    tp_continuation_10_rate: float
    delta_pnl_after_commission: float = 0.0
    delta_trades_per_day: float = 0.0
    delta_gross_turnover_per_day: float = 0.0
    delta_avg_exposure_pct: float = 0.0
    delta_flat_time_pct: float = 0.0
    activity_check: str = "baseline"

    def as_row(self) -> dict[str, float | int | str]:
        return {
            "candidate": self.candidate,
            "total_return": self.total_return,
            "pnl_after_commission": self.pnl_after_commission,
            "trades_per_day": self.trades_per_day,
            "gross_turnover_per_day": self.gross_turnover_per_day,
            "avg_exposure_pct": self.avg_exposure_pct,
            "flat_time_pct": self.flat_time_pct,
            "avg_holding_bars": self.avg_holding_bars,
            "tp_exit_count": self.tp_exit_count,
            "tp_continuation_05_rate": self.tp_continuation_05_rate,
            "tp_continuation_10_rate": self.tp_continuation_10_rate,
            "delta_pnl_after_commission": self.delta_pnl_after_commission,
            "delta_trades_per_day": self.delta_trades_per_day,
            "delta_gross_turnover_per_day": self.delta_gross_turnover_per_day,
            "delta_avg_exposure_pct": self.delta_avg_exposure_pct,
            "delta_flat_time_pct": self.delta_flat_time_pct,
            "activity_check": self.activity_check,
        }


def run_profit_lock_activity_check(
    prices,
    strategy: Strategy,
    *,
    settings,
    initial_capital: float = 1_000_000.0,
    periods_per_year: int = 252,
    commission_rate: float = 0.0005,
    slippage_bps: float = 2.0,
    warmup: int = 50,
) -> dict[str, ProfitLockActivityRow]:
    rows: dict[str, ProfitLockActivityRow] = {}
    for name in PROFIT_LOCK_CANDIDATES:
        candidate_settings = settings_for_profit_lock_candidate(settings, name)
        result = run_backtest(
            prices,
            strategy,
            settings=candidate_settings,
            initial_capital=initial_capital,
            periods_per_year=periods_per_year,
            commission_rate=commission_rate,
            slippage_bps=slippage_bps,
            apply_risk=True,
            warmup=warmup,
        )
        rows[name] = ProfitLockActivityRow(
            candidate=name,
            total_return=result.report.total_return,
            pnl_after_commission=float(result.equity[-1] - initial_capital),
            trades_per_day=result.trades_per_day,
            gross_turnover_per_day=result.gross_turnover_per_day,
            avg_exposure_pct=result.avg_exposure_pct,
            flat_time_pct=result.flat_time_pct,
            avg_holding_bars=result.avg_holding_bars,
            tp_exit_count=result.tp_exit_count,
            tp_continuation_05_rate=result.tp_continuation_05_rate,
            tp_continuation_10_rate=result.tp_continuation_10_rate,
        )
    return _with_baseline_deltas(rows)


def _with_baseline_deltas(
    rows: dict[str, ProfitLockActivityRow],
) -> dict[str, ProfitLockActivityRow]:
    baseline = rows.get("baseline")
    if baseline is None:
        return rows
    out: dict[str, ProfitLockActivityRow] = {}
    for name, row in rows.items():
        if name == "baseline":
            out[name] = row
            continue
        reasons: list[str] = []
        if (
            baseline.avg_exposure_pct > 0
            and row.avg_exposure_pct < baseline.avg_exposure_pct * MIN_ACTIVITY_RATIO
        ):
            reasons.append("low_exposure")
        if (
            baseline.gross_turnover_per_day > 0
            and row.gross_turnover_per_day
            < baseline.gross_turnover_per_day * MIN_ACTIVITY_RATIO
        ):
            reasons.append("low_turnover")
        if (
            row.tp_exit_count >= TP_CONTINUATION_MIN_EXITS
            and row.tp_continuation_10_rate > TP_CONTINUATION_WARN_RATE
        ):
            reasons.append("cuts_winners")
        activity_check = "ok" if not reasons else "no_go:" + ",".join(reasons)
        out[name] = ProfitLockActivityRow(
            candidate=row.candidate,
            total_return=row.total_return,
            pnl_after_commission=row.pnl_after_commission,
            trades_per_day=row.trades_per_day,
            gross_turnover_per_day=row.gross_turnover_per_day,
            avg_exposure_pct=row.avg_exposure_pct,
            flat_time_pct=row.flat_time_pct,
            avg_holding_bars=row.avg_holding_bars,
            tp_exit_count=row.tp_exit_count,
            tp_continuation_05_rate=row.tp_continuation_05_rate,
            tp_continuation_10_rate=row.tp_continuation_10_rate,
            delta_pnl_after_commission=(
                row.pnl_after_commission - baseline.pnl_after_commission
            ),
            delta_trades_per_day=row.trades_per_day - baseline.trades_per_day,
            delta_gross_turnover_per_day=(
                row.gross_turnover_per_day - baseline.gross_turnover_per_day
            ),
            delta_avg_exposure_pct=row.avg_exposure_pct - baseline.avg_exposure_pct,
            delta_flat_time_pct=row.flat_time_pct - baseline.flat_time_pct,
            activity_check=activity_check,
        )
    return out


def format_profit_lock_activity_check(rows: dict[str, ProfitLockActivityRow]) -> str:
    cols = [
        "total_return",
        "pnl_after_commission",
        "trades_per_day",
        "gross_turnover_per_day",
        "avg_exposure_pct",
        "flat_time_pct",
        "avg_holding_bars",
        "tp_exit_count",
        "tp_continuation_05_rate",
        "tp_continuation_10_rate",
        "delta_pnl_after_commission",
        "delta_trades_per_day",
        "delta_gross_turnover_per_day",
        "delta_avg_exposure_pct",
        "delta_flat_time_pct",
        "activity_check",
    ]
    header = f"{'candidate':<14} " + " ".join(f"{c:>24}" for c in cols)
    lines = [header, "-" * len(header)]
    for name, row in rows.items():
        data = row.as_row()
        cells = " ".join(_format_cell(data.get(c, "")) for c in cols)
        lines.append(f"{name:<14} {cells}")
    return "\n".join(lines)


def _format_cell(value) -> str:
    if isinstance(value, float):
        return f"{value:>24.6g}"
    return f"{value!s:>24}"
