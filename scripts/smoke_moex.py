"""Smoke-проверка: скачать свечи из ISS API.

Usage: PYTHONPATH=src python scripts/smoke_moex.py SBER 60
"""

from __future__ import annotations

import sys

from agent.data.moex import get_candles
from agent.logging import configure_logging


def main() -> None:
    configure_logging("INFO")
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SBER"
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    df = get_candles(symbol, interval=interval, days=10)
    print(df.tail())


if __name__ == "__main__":
    main()
