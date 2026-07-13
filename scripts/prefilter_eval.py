"""Оценка качества prefilter БЕЗ LLM (чистая функция over историю).

Вопрос: когда prefilter говорит «skip» (flat, нет сетапа) — действительно ли
последующее движение меньше, чем когда он пропускает к LLM? Если да — он
корректно отсекает тихие периоды (экономит токены, не теряя движений). Если
skip-бары двигаются так же — фильтр случайный.

Запуск:
  python -m scripts.prefilter_eval --days 365 --interval 24 --fwd 3
"""

from __future__ import annotations

import argparse
import logging

import numpy as np
import structlog

logging.basicConfig(level=logging.CRITICAL)
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

from agent.backtest.data import load_history  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.features.indicators import MIN_BARS, compute_features  # noqa: E402
from agent.graph.prefilter import should_skip_prefilter  # noqa: E402
from agent.runtime.universe import DEFAULT_UNIVERSE, parse_universe  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--interval", type=int, default=24)
    ap.add_argument("--fwd", type=int, default=3, help="горизонт forward-return в барах")
    ap.add_argument("--move", type=float, default=0.005, help="порог 'значимого' хода")
    args = ap.parse_args()

    tickers = parse_universe(args.tickers) if args.tickers else DEFAULT_UNIVERSE
    prices = load_history(tickers=tickers, interval=args.interval, days=args.days, use_cache=True)
    if not prices:
        print("No data — aborting.")
        return

    skip_moves: list[float] = []   # |fwd-return| на skip-барах
    pass_moves: list[float] = []   # |fwd-return| на passed-барах
    reasons: dict[str, int] = {}
    n_bars = 0

    for tkr, df in prices.items():
        closes = df["close"].to_numpy(dtype=float)
        for t in range(MIN_BARS, len(df) - args.fwd):
            sub = df.iloc[: t + 1]
            try:
                feats = compute_features(sub)
            except Exception:
                continue
            n_bars += 1
            skip, reason = should_skip_prefilter(
                feats, current_position=0,
                rsi_low=settings.AGENT_PREFILTER_RSI_LOW,
                rsi_high=settings.AGENT_PREFILTER_RSI_HIGH,
                macd_hist_abs_max=settings.AGENT_PREFILTER_MACD_HIST_MAX,
                ema_spread_pct_max=settings.AGENT_PREFILTER_EMA_SPREAD_MAX,
            )
            reasons[reason.split(":")[0]] = reasons.get(reason.split(":")[0], 0) + 1
            c0 = closes[t]
            cf = closes[t + args.fwd]
            fwd_ret = abs(cf / c0 - 1.0) if c0 > 0 else 0.0
            (skip_moves if skip else pass_moves).append(fwd_ret)

    def stats(xs: list[float]) -> str:
        if not xs:
            return "n=0"
        a = np.asarray(xs)
        big = float((a > args.move).mean())
        return f"n={len(a):>5}  mean|fwd|={a.mean():.4%}  median={np.median(a):.4%}  P(>{args.move:.1%})={big:.1%}"

    skip_rate = len(skip_moves) / max(n_bars, 1)
    print(f"\nUniverse={len(prices)}  bars_evaluated={n_bars}  fwd={args.fwd} bars  "
          f"interval={args.interval}")
    print(f"prefilter SKIP-rate (flat position): {skip_rate:.1%}\n")
    print("reasons:", dict(sorted(reasons.items(), key=lambda kv: -kv[1])))
    print()
    print(f"SKIP  bars: {stats(skip_moves)}")
    print(f"PASS  bars: {stats(pass_moves)}")
    print()
    if skip_moves and pass_moves:
        ratio = np.mean(pass_moves) / max(np.mean(skip_moves), 1e-9)
        print(f"Качество: pass-бары двигаются в {ratio:.2f}× сильнее skip-баров.")
        print("(>1 — prefilter корректно отсекает тихие; ≈1 — фильтр случайный.)")


if __name__ == "__main__":
    main()
