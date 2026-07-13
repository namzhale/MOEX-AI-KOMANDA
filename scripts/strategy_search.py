"""Перебор конфигураций стратегии на кэшированной истории.

Цель: найти конфигурацию с положительным P&L и оборотом ≥10М, и проверить её
робастность (не подгонка ли под падающий режим конкретного окна).

Запуск:
  python -m scripts.strategy_search --days 60 --interval 60
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd
import structlog

# Глушим логи движка, НЕ ломая структурный вызов log.info(event, **kw):
# фильтрующий bound logger принимает kwargs, просто отбрасывает записи ниже уровня.
logging.basicConfig(level=logging.CRITICAL)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)

from agent.backtest import metrics as metrics_mod  # noqa: E402
from agent.backtest.data import aligned_calendar, load_history  # noqa: E402
from agent.backtest.engine import buy_and_hold_equity, run_backtest  # noqa: E402
from agent.backtest.strategy import TechnicalStrategy  # noqa: E402
from agent.runtime.universe import DEFAULT_UNIVERSE, parse_universe  # noqa: E402

COLS = ["total_return", "sharpe", "sortino", "max_dd", "hit_rate", "n_trades"]


def _row(res) -> dict:
    r = res.report.as_row()
    out = {c: r.get(c, "") for c in COLS}
    out["gross_M"] = round(res.gross_traded / 1e6, 2)  # оборот в млн ₽
    return out


def _fmt(rows: dict[str, dict]) -> str:
    cols = COLS + ["gross_M"]
    header = f"{'strategy':<26} " + " ".join(f"{c:>12}" for c in cols)
    lines = [header, "-" * len(header)]
    for name, row in rows.items():
        cells = " ".join(f"{row.get(c, ''):>12}" for c in cols)
        lines.append(f"{name:<26} {cells}")
    return "\n".join(lines)


CONFIGS = [
    ("short_rsi70", dict(allow_short=True, rsi_overbought=70, rsi_oversold=30)),
    ("short_rsi60", dict(allow_short=True, rsi_overbought=60, rsi_oversold=40)),
    ("short_rsi55", dict(allow_short=True, rsi_overbought=55, rsi_oversold=45)),
    ("short_rsi50", dict(allow_short=True, rsi_overbought=50, rsi_oversold=50)),
]


def _slice(prices: dict, lo, hi) -> dict:
    out = {}
    for t, df in prices.items():
        sub = df.loc[lo:hi] if hi is not None else df.loc[lo:]
        if len(sub) > 60:
            out[t] = sub
    return out


def _run(prices, kw, ppy, args) -> dict | None:
    try:
        res = run_backtest(
            prices, TechnicalStrategy(**kw),
            initial_capital=args.capital, periods_per_year=ppy,
            commission_rate=args.commission, slippage_bps=args.slippage_bps,
            apply_risk=True,
        )
        return _row(res)
    except Exception as e:  # noqa: BLE001
        print(f"  failed: {e}")
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--commission", type=float, default=0.0005)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    args = ap.parse_args()

    tickers = parse_universe(args.tickers) if args.tickers else DEFAULT_UNIVERSE
    ppy = metrics_mod.TRADING_DAYS if args.interval == 24 else metrics_mod.TRADING_DAYS * 8

    print(f"Loading {len(tickers)} tickers, {args.days}d, interval={args.interval}...")
    prices = load_history(tickers=tickers, interval=args.interval, days=args.days, use_cache=True)
    if not prices:
        print("No data — aborting.")
        return
    ts = aligned_calendar(prices)
    print(f"Loaded {len(prices)} tickers, {len(ts)} bars.\n")

    # --- Полное окно ---
    rows: dict[str, dict] = {}
    bh = metrics_mod.build_report(
        buy_and_hold_equity(prices, ts, initial_capital=args.capital), periods_per_year=ppy
    ).as_row()
    rows["buy_and_hold"] = {c: bh.get(c, "") for c in COLS} | {"gross_M": 0.0}
    for label, kw in CONFIGS:
        r = _run(prices, kw, ppy, args)
        if r:
            rows[label] = r
    print("=== FULL WINDOW ===")
    print(_fmt(rows), "\n")

    # --- Робастность: половины окна (подгонка под режим?) ---
    mid = ts[len(ts) // 2]
    h1, h2 = _slice(prices, ts[0], mid), _slice(prices, mid, None)
    for half_name, ph in (("FIRST HALF", h1), ("SECOND HALF", h2)):
        hts = aligned_calendar(ph)
        rr: dict[str, dict] = {}
        bhh = metrics_mod.build_report(
            buy_and_hold_equity(ph, hts, initial_capital=args.capital), periods_per_year=ppy
        ).as_row()
        rr["buy_and_hold"] = {c: bhh.get(c, "") for c in COLS} | {"gross_M": 0.0}
        for label, kw in CONFIGS:
            r = _run(ph, kw, ppy, args)
            if r:
                rr[label] = r
        print(f"=== {half_name} ({len(hts)} bars from {pd.Timestamp(hts[0]).date()}) ===")
        print(_fmt(rr), "\n")


if __name__ == "__main__":
    main()
