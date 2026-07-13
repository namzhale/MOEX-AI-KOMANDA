"""CLI-раннер бэктеста: грузит историю, гоняет стратегии, печатает сравнение.

Примеры:
  python -m agent.backtest.run --days 365 --interval 24
  python -m agent.backtest.run --tickers SBER,GAZP,LKOH --no-risk
"""

from __future__ import annotations

import argparse

from agent.backtest import metrics as metrics_mod
from agent.backtest.data import aligned_calendar, load_history
from agent.backtest.engine import buy_and_hold_equity, run_backtest
from agent.backtest.profit_lock import (
    format_profit_lock_activity_check,
    run_profit_lock_activity_check,
)
from agent.backtest.strategy import TechnicalStrategy
from agent.config import settings as global_settings
from agent.runtime.universe import DEFAULT_UNIVERSE, parse_universe


def _fmt_table(rows: dict[str, dict]) -> str:
    cols = [
        "total_return", "cagr", "sharpe", "sortino", "calmar",
        "max_dd", "vol", "boot_sharpe_p5", "hit_rate", "turnover",
        "n_trades", "final_equity",
    ]
    header = f"{'strategy':<16} " + " ".join(f"{c:>14}" for c in cols)
    lines = [header, "-" * len(header)]
    for name, row in rows.items():
        cells = " ".join(f"{row.get(c, ''):>14}" for c in cols)
        lines.append(f"{name:<16} {cells}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="MOEX backtest harness")
    ap.add_argument("--tickers", default="", help="CSV; пусто = весь universe")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--interval", type=int, default=24, help="24=daily, 60=hourly")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--commission", type=float, default=0.0005)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument(
        "--profit-lock-check",
        action="store_true",
        help="compare baseline vs profit-lock candidates without changing production defaults",
    )
    ap.add_argument("--no-risk", action="store_true", help="отключить Risk Officer")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    tickers = parse_universe(args.tickers) if args.tickers else DEFAULT_UNIVERSE
    ppy = metrics_mod.TRADING_DAYS if args.interval == 24 else metrics_mod.TRADING_DAYS * 8

    print(f"Loading history: {len(tickers)} tickers, {args.days}d, interval={args.interval}...")
    prices = load_history(
        tickers=tickers, interval=args.interval, days=args.days, use_cache=not args.no_cache
    )
    if not prices:
        print("No data loaded — aborting.")
        return
    ts = aligned_calendar(prices)
    print(f"Loaded {len(prices)} tickers, {len(ts)} bars.\n")

    rows: dict[str, dict] = {}

    # Бенчмарк buy-and-hold
    bh_eq = buy_and_hold_equity(prices, ts, initial_capital=args.capital)
    rows["buy_and_hold"] = metrics_mod.build_report(bh_eq, periods_per_year=ppy).as_row()

    # Технические — с риском и без, для сравнения
    for label, apply_risk in (("technical+risk", True), ("technical_raw", False)):
        try:
            res = run_backtest(
                prices,
                TechnicalStrategy(),
                initial_capital=args.capital,
                periods_per_year=ppy,
                commission_rate=args.commission,
                slippage_bps=args.slippage_bps,
                apply_risk=apply_risk and not args.no_risk,
            )
            rows[label] = res.report.as_row()
        except Exception as e:  # noqa: BLE001
            print(f"strategy {label} failed: {e}")

    print(_fmt_table(rows))
    if args.profit_lock_check:
        print("\nProfit-lock activity check:")
        activity = run_profit_lock_activity_check(
            prices,
            TechnicalStrategy(),
            settings=global_settings,
            initial_capital=args.capital,
            periods_per_year=ppy,
            commission_rate=args.commission,
            slippage_bps=args.slippage_bps,
        )
        print(format_profit_lock_activity_check(activity))
    print("\nFINSABER check: бьём ли buy_and_hold по total_return/Sharpe?")


if __name__ == "__main__":
    main()
