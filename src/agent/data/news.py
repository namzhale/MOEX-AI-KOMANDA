from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Iterable, Protocol

import feedparser
import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from agent.config import settings
from agent.runtime.universe import EMITTER_NAMES_BY_TICKER

log = structlog.get_logger()


def _is_retryable_http(exc: BaseException) -> bool:
    """Ретраим только 5xx и network — на 4xx сразу падаем без задержек."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return 500 <= exc.response.status_code < 600
        except Exception:
            return False
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


_ZERO_WIDTH_CHARS = "".join(["​", "‌", "‍", "⁠", "﻿"])
_ZERO_WIDTH_TRANS = str.maketrans({c: "" for c in _ZERO_WIDTH_CHARS})

# Эвристические подсказки prompt injection — если встречаем в тексте, логируем.
_INJECTION_HINTS = (
    "ignore previous",
    "ignore prior",
    "system prompt",
    "system:",
    "you must",
    "забудь предыдущие",
    "игнорируй инструкции",
    "отмени инструкции",
)


@dataclass
class NewsItem:
    id: str
    source: str  # "tass" | "interfax" | "edisclosure" | любой кастомный
    published_at: datetime
    tickers: list[str] = field(default_factory=list)
    type: str = "general"
    title: str = ""
    body: str = ""
    url: str = ""
    language: str = "ru"


class NewsSource(Protocol):
    name: str

    def fetch_recent(self, lookback_hours: int) -> list[NewsItem]: ...


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(s: str) -> str:
    p = _HTMLStripper()
    try:
        p.feed(s)
    except Exception:
        return s
    return p.text()


def sanitize(text: str, max_chars: int = 2000) -> str:
    """HTML strip + NFC normalize + zero-width clean + truncate."""
    if not text:
        return ""
    cleaned = strip_html(text)
    cleaned = unicodedata.normalize("NFC", cleaned)
    cleaned = cleaned.translate(_ZERO_WIDTH_TRANS)
    cleaned = " ".join(cleaned.split())  # схлопываем whitespace
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "…"
    return cleaned


def looks_like_injection(text: str) -> bool:
    low = text.lower()
    return any(hint in low for hint in _INJECTION_HINTS)


def filter_for_ticker(items: Iterable[NewsItem], symbol: str) -> list[NewsItem]:
    """Отбираем item'ы, в которых упомянут эмитент или сам тикер."""
    needles = (symbol.lower(), *(n.lower() for n in EMITTER_NAMES_BY_TICKER.get(symbol, ())))
    if not needles:
        return []
    out: list[NewsItem] = []
    for it in items:
        if symbol in it.tickers:
            out.append(it)
            continue
        hay = (it.title + " " + it.body).lower()
        if any(n in hay for n in needles):
            it = NewsItem(**{**it.__dict__, "tickers": [*it.tickers, symbol]})
            out.append(it)
    return out


# ── Sources ──────────────────────────────────────────────────────────────────


class RSSFetcher:
    """Универсальный RSS-парсер. Один экземпляр на источник (name, url)."""

    def __init__(
        self,
        name: str,
        url: str,
        timeout: float | None = None,
    ) -> None:
        self.name = name
        self.url = url.strip()
        self.timeout = timeout or settings.NEWS_HTTP_TIMEOUT
        self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception(_is_retryable_http),
    )
    def _http_get(self) -> bytes:
        r = self._client.get(self.url, headers={"User-Agent": "team-24-agent/1.0"})
        r.raise_for_status()
        return r.content

    def fetch_recent(self, lookback_hours: int) -> list[NewsItem]:
        raw = self._http_get()
        feed = feedparser.parse(raw)
        cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
        out: list[NewsItem] = []
        for entry in feed.entries:
            published = _parse_feed_time(entry) or datetime.now(UTC)
            if published < cutoff:
                continue
            raw_title = entry.get("title", "")
            raw_body = entry.get("summary", "") or entry.get("description", "")
            item = NewsItem(
                id=f"{self.name}:{entry.get('id') or entry.get('link', '')}",
                source=self.name,
                published_at=published,
                tickers=[],
                type="general",
                title=sanitize(raw_title, max_chars=300),
                body=sanitize(raw_body, max_chars=2000),
                url=entry.get("link", ""),
                language="ru",
            )
            out.append(item)
        return out

    def close(self) -> None:
        self._client.close()


def TassRSSFetcher(url: str | None = None, timeout: float | None = None) -> RSSFetcher:
    """Backwards-compat alias — некоторые тесты ещё конструируют TassRSSFetcher."""
    return RSSFetcher(name="tass", url=(url or settings.TASS_RSS_URL), timeout=timeout)


def InterfaxRSSFetcher(url: str | None = None, timeout: float | None = None) -> RSSFetcher:
    return RSSFetcher(name="interfax", url=(url or settings.INTERFAX_RSS_URL), timeout=timeout)


class EDisclosureFetcher:
    name = "edisclosure"

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.EDISCLOSURE_BASE_URL).strip().rstrip("/")
        self.timeout = timeout or settings.NEWS_HTTP_TIMEOUT
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "team-24-agent/1.0", "Accept": "application/json"},
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

    def fetch_recent(self, lookback_hours: int) -> list[NewsItem]:
        cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
        params = {"limit": 50, "dateFrom": cutoff.date().isoformat()}
        search_path = settings.EDISCLOSURE_SEARCH_PATH
        try:
            payload = self._http_get(search_path, params=params)
        except httpx.HTTPStatusError as e:
            # 404 = endpoint не настроен в этом окружении. Не WARN, а DEBUG —
            # это известная ситуация на hackathon stand. NEWS_SOURCES=tass обходит.
            status = e.response.status_code if e.response else 0
            if status == 404:
                log.debug(
                    "news.edisclosure.endpoint_missing",
                    path=search_path,
                    hint="check EDISCLOSURE_SEARCH_PATH or set NEWS_SOURCES=tass",
                )
            else:
                log.warning("news.edisclosure.http_error", status=status, error=str(e)[:200])
            return []
        except Exception as e:
            log.warning("news.edisclosure.unavailable", error=str(e)[:200])
            return []

        items_raw = payload.get("items") or payload.get("data") or []
        out: list[NewsItem] = []
        for row in items_raw:
            published = _parse_iso(row.get("publishedAt") or row.get("date"))
            if not published or published < cutoff:
                continue
            tickers = _extract_tickers(row)
            doc_type = str(row.get("type") or row.get("documentType") or "disclosure")
            out.append(
                NewsItem(
                    id=f"edisclosure:{row.get('id') or row.get('uid') or row.get('url', '')}",
                    source="edisclosure",
                    published_at=published,
                    tickers=tickers,
                    type=doc_type,
                    title=sanitize(row.get("title", ""), max_chars=300),
                    body=sanitize(row.get("description", "") or row.get("summary", ""), max_chars=2000),
                    url=row.get("url", ""),
                    language="ru",
                )
            )
        return out

    def close(self) -> None:
        self._client.close()


def _parse_feed_time(entry) -> datetime | None:
    raw = entry.get("published_parsed") or entry.get("updated_parsed")
    if raw:
        try:
            return datetime(*raw[:6], tzinfo=UTC)
        except (TypeError, ValueError):
            return None
    return _parse_iso(entry.get("published") or entry.get("updated"))


def _parse_iso(raw) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _extract_tickers(row: dict) -> list[str]:
    """Эмитент из e-disclosure → найти подходящий тикер по карте."""
    tickers_raw = row.get("tickers") or row.get("securities") or []
    out: list[str] = []
    if isinstance(tickers_raw, list):
        for t in tickers_raw:
            if isinstance(t, str) and t.upper() in EMITTER_NAMES_BY_TICKER:
                out.append(t.upper())
    emitter = (row.get("emitter") or row.get("issuer") or "").lower()
    if emitter:
        for ticker, names in EMITTER_NAMES_BY_TICKER.items():
            if any(name.lower() in emitter for name in names):
                if ticker not in out:
                    out.append(ticker)
    return out


# ── Aggregator with TTL cache ───────────────────────────────────────────────


_cache: dict[str, tuple[float, list[NewsItem]]] = {}


def clear_cache() -> None:
    _cache.clear()


class NewsAggregator:
    """Объединяет источники, дедуплицирует по id, кеширует на TTL."""

    def __init__(
        self,
        sources: list[NewsSource] | None = None,
        cache_ttl_seconds: int | None = None,
        lookback_hours: int | None = None,
    ) -> None:
        self.cache_ttl = cache_ttl_seconds or settings.NEWS_CACHE_TTL_SECONDS
        self.lookback_hours = lookback_hours or settings.NEWS_LOOKBACK_HOURS
        self.sources = sources if sources is not None else _build_default_sources()

    def fetch_all(self) -> list[NewsItem]:
        now = time.monotonic()
        merged: dict[str, NewsItem] = {}
        for src in self.sources:
            cached = _cache.get(src.name)
            if cached and now - cached[0] < self.cache_ttl:
                items = cached[1]
                log.info(
                    "news.fetch.ok",
                    source=src.name,
                    items_fetched=len(items),
                    cache_hit=True,
                    elapsed_ms=0,
                )
            else:
                t0 = time.monotonic()
                log.info("news.fetch.start", source=src.name, lookback_hours=self.lookback_hours)
                try:
                    items = src.fetch_recent(self.lookback_hours)
                    _cache[src.name] = (now, items)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    log.info(
                        "news.fetch.ok",
                        source=src.name,
                        items_fetched=len(items),
                        cache_hit=False,
                        elapsed_ms=elapsed_ms,
                    )
                except Exception as e:
                    log.warning("news.fetch.failed", source=src.name, error=str(e)[:200])
                    items = []
            for it in items:
                merged.setdefault(it.id, it)
        log.debug(
            "news.aggregate.merged",
            total=len(merged),
            by_source={s.name: sum(1 for v in merged.values() if v.source == s.name) for s in self.sources},
            sample_titles=[v.title[:100] for v in list(merged.values())[:5]],
        )
        return list(merged.values())

    def fetch_for_ticker(self, symbol: str) -> list[NewsItem]:
        all_items = self.fetch_all()
        filtered = filter_for_ticker(all_items, symbol)
        max_items = settings.NEWS_MAX_ITEMS_PER_TICKER
        if len(filtered) > max_items:
            filtered.sort(key=lambda x: x.published_at, reverse=True)
            filtered = filtered[:max_items]
        log.debug(
            "news.filter.result",
            symbol=symbol,
            total_aggregated=len(all_items),
            matched=len(filtered),
            titles=[it.title[:100] for it in filtered],
        )
        return filtered


def _build_default_sources() -> list[NewsSource]:
    """Источники строятся по CSV в settings.NEWS_SOURCES.

    Поддерживаемые имена: tass, interfax, edisclosure.
    Любое другое имя игнорируется с предупреждением.
    """
    names = [s.strip().lower() for s in settings.NEWS_SOURCES.split(",") if s.strip()]
    out: list[NewsSource] = []
    for name in names:
        if name == "tass":
            out.append(TassRSSFetcher())
        elif name == "interfax":
            out.append(InterfaxRSSFetcher())
        elif name == "edisclosure":
            out.append(EDisclosureFetcher())
        else:
            log.warning("news.source.unknown", name=name)
    return out
