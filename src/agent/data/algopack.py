from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from agent.config import settings
from agent.data.news import _is_retryable_http

log = structlog.get_logger()

VALID_INTERVALS = {1, 10, 60, 24, 7, 31}

# base_url уже .../iss — путь без повторного /iss/
_DATASHOP_EQ = "/datashop/algopack/eq"


def _parse_iss_table(payload: dict, *block_names: str) -> list[dict]:
    """Разбирает ISS JSON (columns + data) в list[dict] с lowercase-ключами."""
    candidates = list(block_names) + list(payload.keys())
    for name in candidates:
        block = payload.get(name)
        if not isinstance(block, dict):
            continue
        columns = block.get("columns") or []
        rows = block.get("data") or []
        if not columns or not rows:
            continue
        cols = [str(c).lower() for c in columns]
        return [dict(zip(cols, row, strict=False)) for row in rows]
    return []


def _row_for_symbol(rows: list[dict], symbol: str) -> dict:
    sym = symbol.upper()
    for row in rows:
        secid = str(row.get("secid") or row.get("ticker") or "").upper()
        if secid == sym:
            return row
    return rows[-1] if len(rows) == 1 else {}


class AlgopackClient:
    """MOEX ALGOPACK (data.moex.com) — премиум источник OHLCV + микроструктуры.

    Использует тот же ISS-протокол что публичный iss.moex.com, но:
      - хост apim.moex.com (HTTPS gateway)
      - Bearer JWT-токен в Authorization (срок ~1-2 года)
      - 15-минутная задержка на СТАРТОВОМ тарифе, онлайн на PROMO

    На СТАРТОВОМ тарифе хакатона свечей и сделок достаточно.
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.token = (token or settings.ALGOPACK_TOKEN).strip()
        self.base_url = (base_url or settings.ALGOPACK_BASE_URL).strip().rstrip("/")
        if not self.token:
            log.warning("algopack.no_token")
        headers = {
            "Accept": "application/json",
            "User-Agent": "team-24-agent/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception(_is_retryable_http),
    )
    def _http_get(self, path: str, params: dict) -> dict:
        r = self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def get_candles(
        self,
        symbol: str,
        interval: int = 60,
        days: int = 30,
        board: str = "TQBR",
    ) -> pd.DataFrame:
        if interval not in VALID_INTERVALS:
            raise ValueError(
                f"interval must be one of {sorted(VALID_INTERVALS)}, got {interval}"
            )

        end = datetime.now(UTC).date()
        begin = end - timedelta(days=days)
        path = (
            f"/engines/stock/markets/shares/boards/{board}/securities/{symbol}/candles.json"
        )
        log.info(
            "algopack.candles.fetch",
            symbol=symbol,
            interval=interval,
            start=str(begin),
            end=str(end),
        )

        # Пагинация: ISS candles.json отдаёт ≤500 строк/запрос (oldest→newest).
        # Без цикла start=0 вернул бы САМУЮ СТАРУЮ страницу → протухший last_price
        # на больших окнах (10-мин × 30д ≈ 1700 баров). Идём до конца окна.
        _PAGE = 500  # ISS candles.json отдаёт ≤500 строк/запрос
        columns: list[str] = []
        all_rows: list = []
        offset = 0
        for _ in range(40):  # safety cap (40×500 = 20000 баров)
            params = {
                "interval": interval,
                "from": str(begin),
                "till": str(end),
                "start": offset,
            }
            payload = self._http_get(path, params=params)
            block = payload.get("candles") or {}
            cols = block.get("columns") or []
            rows = block.get("data") or []
            if cols and not columns:
                columns = cols
            if not rows:
                break
            all_rows.extend(rows)
            offset += len(rows)
            if len(rows) < _PAGE:  # неполная страница = последняя
                break

        if not columns or not all_rows:
            raise RuntimeError(
                f"No candles for {symbol} on {board} via algopack "
                f"(interval={interval}, days={days})"
            )

        df = pd.DataFrame(all_rows, columns=columns)
        # Нормализация колонок к нижнему регистру (ALGOPACK возвращает UPPER).
        df.columns = [c.lower() for c in df.columns]
        if "begin" in df.columns:
            df["begin"] = pd.to_datetime(df["begin"])
            df = df.set_index("begin")
        if "end" in df.columns:
            df["end"] = pd.to_datetime(df["end"])

        # Freshness-guard: последний бар не должен быть старше нескольких дней —
        # иначе данные/пагинация сломаны (как было с 10-мин × 30д). Лог в Loki.
        last_end = df["end"].iloc[-1] if ("end" in df.columns and len(df)) else (
            df.index[-1] if len(df) else None
        )
        stale_days = None
        if last_end is not None:
            try:
                stale_days = (
                    pd.Timestamp.now().normalize() - pd.Timestamp(last_end).normalize()
                ).days
            except Exception:
                stale_days = None
        log.info(
            "algopack.candles.ok", symbol=symbol, rows=len(df), last=str(last_end)
        )
        if stale_days is not None and stale_days > 4:
            log.warning(
                "algopack.candles.stale",
                symbol=symbol,
                interval=interval,
                last=str(last_end),
                stale_days=stale_days,
            )
        return df

    def _datashop_get(
        self,
        dataset: str,
        symbol: str | None = None,
        *,
        date: str | None = None,
        latest: bool = True,
    ) -> list[dict]:
        """Super Candles / Mega Alerts через datashop (Promo)."""
        day = date or datetime.now(UTC).astimezone().date().isoformat()
        params: dict[str, str | int] = {"date": day}
        if latest:
            params["latest"] = 1
        paths: list[str] = []
        if symbol:
            paths.append(f"{_DATASHOP_EQ}/{dataset}/{symbol.upper()}.json")
        paths.append(f"{_DATASHOP_EQ}/{dataset}.json")
        last_err: Exception | None = None
        for path in paths:
            try:
                payload = self._http_get(path, params=params)
                rows = _parse_iss_table(payload, dataset, "data", f"{dataset}_data")
                if rows:
                    return rows
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        return []

    def get_tradestats_latest(self, symbol: str, *, date: str | None = None) -> dict:
        rows = self._datashop_get("tradestats", symbol, date=date, latest=True)
        if not rows:
            raise RuntimeError(f"No tradestats for {symbol}")
        row = _row_for_symbol(rows, symbol)
        log.info("algopack.tradestats.ok", symbol=symbol.upper())
        return row

    def get_tradestats_market_latest(self, *, date: str | None = None) -> list[dict]:
        rows = self._datashop_get("tradestats", None, date=date, latest=True)
        log.info("algopack.tradestats.market", rows=len(rows))
        return rows

    def get_obstats_latest(self, symbol: str, *, date: str | None = None) -> dict:
        rows = self._datashop_get("obstats", symbol, date=date, latest=True)
        if not rows:
            raise RuntimeError(f"No obstats for {symbol}")
        row = _row_for_symbol(rows, symbol)
        log.info("algopack.obstats.ok", symbol=symbol.upper())
        return row

    def get_orderstats_latest(self, symbol: str, *, date: str | None = None) -> dict:
        rows = self._datashop_get("orderstats", symbol, date=date, latest=True)
        if not rows:
            raise RuntimeError(f"No orderstats for {symbol}")
        row = _row_for_symbol(rows, symbol)
        log.info("algopack.orderstats.ok", symbol=symbol.upper())
        return row

    def get_mega_alerts(
        self,
        *,
        date: str | None = None,
        latest: bool = True,
        symbol: str | None = None,
    ) -> list[dict]:
        rows = self._datashop_get("alerts", symbol, date=date, latest=latest)
        log.info("algopack.alerts.ok", rows=len(rows), symbol=symbol or "all")
        return rows

    def close(self) -> None:
        self._client.close()
