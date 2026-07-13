"""Проверка Algopack Promo: свежесть свечей + Super Candles (tradestats/obstats).

Запуск из корня team-24-develop_2 (токен в ENV ALGOPACK_TOKEN):

  export ALGOPACK_TOKEN=...
  export MARKET_DATA_SOURCE=algopack
  python -m scripts.algopack_freshness

Альтернатива: PYTHONPATH=src python scripts/algopack_freshness.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Корень репо = parent(scripts/); src/ добавляем до import agent.*
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import structlog

logging.basicConfig(level=logging.CRITICAL)
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

from agent.data.algopack import AlgopackClient  # noqa: E402
from agent.data.microstructure import load_flow_features  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")


def main() -> None:
    import os

    os.environ.setdefault("MARKET_DATA_SOURCE", "algopack")
    os.environ.setdefault("ALGOPACK_FLOW_ENABLED", "true")

    client = AlgopackClient()
    now = datetime.now(MSK)
    print(f"now (MSK): {now:%Y-%m-%d %H:%M:%S}")
    print(f"token present: {bool(client.token)}  base_url: {client.base_url}\n")

    for interval in (1, 10, 60):
        try:
            df = client.get_candles(symbol="SBER", interval=interval, days=1)
        except Exception as e:  # noqa: BLE001
            print(f"interval={interval:>3}: FAILED — {str(e)[:160]}")
            continue
        if df.empty:
            print(f"interval={interval:>3}: empty")
            continue
        last_begin = df.index[-1]
        last_close = df["close"].iloc[-1] if "close" in df.columns else "?"
        # begin tz-naive (MSK по сути) → локализуем для разницы
        lb = last_begin.tz_localize(MSK) if last_begin.tzinfo is None else last_begin
        lag_min = (now - lb).total_seconds() / 60.0
        end_col = ""
        if "end" in df.columns:
            end_col = f"  end={df['end'].iloc[-1]}"
        print(
            f"interval={interval:>3}: rows={len(df):>4}  "
            f"last_begin={last_begin}  close={last_close}{end_col}  "
            f"lag={lag_min:.1f} min"
        )
    print("\n--- Super Candles (SBER, latest) ---")
    for label, fn in (
        ("tradestats", client.get_tradestats_latest),
        ("obstats", client.get_obstats_latest),
    ):
        try:
            row = fn("SBER")
            keys = ("disb", "pr_change", "spread_1mio", "imbalance_vol_bbo")
            sample = {k: row.get(k) for k in keys if k in row}
            if label == "obstats":
                sample = {
                    k: row.get(k)
                    for k in ("spread_1mio", "spread_bbo", "imbalance_vol_bbo")
                    if row.get(k) is not None
                }
                sample = {f"{k}_bps": v for k, v in sample.items()}
            print(f"{label}: OK  {sample}")
        except Exception as e:  # noqa: BLE001
            print(f"{label}: FAILED — {str(e)[:200]}")

    print("\n--- load_flow_features (integration) ---")
    try:
        feats = load_flow_features("SBER")
        print(f"flow keys: {sorted(feats.keys())[:12]}{'...' if len(feats) > 12 else ''}")
    except Exception as e:  # noqa: BLE001
        print(f"load_flow_features: FAILED — {str(e)[:200]}")

    client.close()


if __name__ == "__main__":
    main()
