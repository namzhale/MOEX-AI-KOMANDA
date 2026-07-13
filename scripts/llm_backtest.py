"""Офлайн-бэктест БОЕВОГО LLM-движка (analyst→[debate]→trader) — закрывает
слепую зону: проверяем, бьёт ли реальный решатель buy-and-hold и не овертрейдит
ли он (FINSABER), а не только технический прокси.

ВНИМАНИЕ ПО СТОИМОСТИ: каждый бар × тикер = (analyst+debate+trader) LLM-вызовов.
Гонять на МАЛЕНЬКОМ сэмпле. Пример: 1 тикер × 60 дневных баров × ~3 вызова ≈
180 вызовов. Требует POLZA_API_KEY и сеть. News по умолчанию ВЫКЛ (нет
исторических новостей → иначе lookahead).

Запуск:
  POLZA_API_KEY=... python -m scripts.llm_backtest --tickers SBER --days 120 --interval 24
"""

from __future__ import annotations

import argparse
import logging
import sys

# Windows-консоль по умолчанию cp1251 → structlog падает с UnicodeEncodeError на
# не-ASCII в промптах (≥, —, кириллица), tenacity это глотает → LLM «недоступен»
# → HOLD на каждом баре. Форсируем UTF-8, чтобы вывод не ломал LLM-вызовы.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import structlog

logging.basicConfig(level=logging.CRITICAL)
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

from agent.backtest import metrics as metrics_mod  # noqa: E402
from agent.backtest.data import aligned_calendar, load_history  # noqa: E402
from agent.backtest.engine import buy_and_hold_equity, run_backtest  # noqa: E402
from agent.backtest.strategy import LLMStrategy, TechnicalStrategy  # noqa: E402
from agent.runtime.universe import parse_universe  # noqa: E402

COLS = ["total_return", "sharpe", "max_dd", "hit_rate", "n_trades"]


def _row(res) -> dict:
    r = res.report.as_row()
    out = {c: r.get(c, "") for c in COLS}
    out["gross_M"] = round(res.gross_traded / 1e6, 2)
    return out


def _fmt(rows: dict[str, dict]) -> str:
    cols = COLS + ["gross_M"]
    header = f"{'strategy':<16} " + " ".join(f"{c:>13}" for c in cols)
    lines = [header, "-" * len(header)]
    for name, row in rows.items():
        lines.append(f"{name:<16} " + " ".join(f"{row.get(c, ''):>13}" for c in cols))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="SBER", help="CSV; держи МАЛЕНЬКИМ (стоимость!)")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--interval", type=int, default=24, help="24=daily (дёшево), 60=hourly")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--commission", type=float, default=0.0005)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--debate", action="store_true", help="включить bull/bear (дороже)")
    ap.add_argument("--apply-risk", action="store_true", help="прогон через Risk Officer")
    args = ap.parse_args()

    tickers = parse_universe(args.tickers)
    ppy = metrics_mod.TRADING_DAYS if args.interval == 24 else metrics_mod.TRADING_DAYS * 8

    print(f"Loading {len(tickers)} tickers, {args.days}d, interval={args.interval}...")
    prices = load_history(tickers=tickers, interval=args.interval, days=args.days, use_cache=True)
    if not prices:
        print("No data — aborting.")
        return
    ts = aligned_calendar(prices)
    n_bars = max(len(ts) - 51, 0)
    est_calls = n_bars * len(prices) * (3 if args.debate else 2)
    print(f"Loaded {len(prices)} tickers, {len(ts)} bars.")
    print(f"~{est_calls} LLM calls ожидается (бары×тикеры×узлы). Ctrl-C чтобы прервать.\n")

    rows: dict[str, dict] = {}
    bh = metrics_mod.build_report(
        buy_and_hold_equity(prices, ts, initial_capital=args.capital), periods_per_year=ppy
    ).as_row()
    rows["buy_and_hold"] = {c: bh.get(c, "") for c in COLS} | {"gross_M": 0.0}

    tech = run_backtest(
        prices, TechnicalStrategy(), initial_capital=args.capital, periods_per_year=ppy,
        commission_rate=args.commission, slippage_bps=args.slippage_bps, apply_risk=args.apply_risk,
    )
    rows["technical"] = _row(tech)

    llm_strat = LLMStrategy(interval=args.interval, news_enabled=False, debate_enabled=args.debate)
    llm = run_backtest(
        prices, llm_strat, initial_capital=args.capital, periods_per_year=ppy,
        commission_rate=args.commission, slippage_bps=args.slippage_bps, apply_risk=args.apply_risk,
    )
    rows["llm"] = _row(llm)

    print(_fmt(rows))
    print("\nВопрос FINSABER: бьёт ли llm строку buy_and_hold по total_return при разумном n_trades?")


if __name__ == "__main__":
    main()
