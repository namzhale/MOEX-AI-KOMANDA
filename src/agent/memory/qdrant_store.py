from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import structlog

from agent.config import settings
from agent.schemas import ReflectionRecord

log = structlog.get_logger()

_VECTOR_SIZE = 1536  # text-embedding-3-small


def _embedding(text: str) -> list[float]:
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.POLZA_API_KEY.strip() or "missing",
        base_url=settings.LLM_BASE_URL.strip(),
    )
    resp = client.embeddings.create(
        model=settings.EMBEDDING_MODEL.strip(),
        input=text[:8000],
    )
    return list(resp.data[0].embedding)


class MemoryStore:
    """Qdrant + FinMem-style score: similarity × recency × importance."""

    def __init__(self) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._client = QdrantClient(url=settings.QDRANT_URL.strip())
        self._collection = settings.QDRANT_COLLECTION.strip()
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        from qdrant_client.models import Distance, VectorParams
        names = {c.name for c in self._client.get_collections().collections}
        if self._collection not in names:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
            )
            log.info("memory.qdrant.collection_created", collection=self._collection)

    def upsert_reflection(self, record: ReflectionRecord) -> None:
        from qdrant_client.models import PointStruct

        ts = record.timestamp or datetime.now(UTC)
        vector = _embedding(record.lesson)
        point_id = abs(hash(record.trade_id)) % (2**63 - 1)
        payload: dict[str, Any] = {
            "symbol": record.symbol,
            "trade_id": record.trade_id,
            "lesson": record.lesson,
            "tags": record.tags,
            "importance": record.importance,
            "source": record.source,
            "outcome": record.outcome,
            "sector": record.sector,
            "ts": ts.isoformat(),
        }
        self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        log.info("memory.qdrant.upsert", symbol=record.symbol, trade_id=record.trade_id)

    def search_lessons(self, symbol: str, *, k: int = 3) -> list[str]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        query_vec = _embedding(f"MOEX ticker {symbol} trading lessons")
        now = time.time()
        hits = self._client.search(
            collection_name=self._collection,
            query_vector=query_vec,
            query_filter=Filter(
                must=[FieldCondition(key="symbol", match=MatchValue(value=symbol))]
            ),
            limit=max(k * 3, 5),
            with_payload=True,
        )
        scored: list[tuple[float, str]] = []
        for hit in hits:
            payload = hit.payload or {}
            lesson = str(payload.get("lesson") or "").strip()
            if not lesson:
                continue
            importance = float(payload.get("importance") or 0.5)
            ts_raw = payload.get("ts")
            recency = 0.5
            if ts_raw:
                try:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    age_days = max((now - ts.timestamp()) / 86400.0, 0.0)
                    recency = 1.0 / (1.0 + age_days)
                except ValueError:
                    pass
            sim = float(hit.score or 0.0)
            score = (
                settings.MEMORY_SCORE_ALPHA * sim
                + settings.MEMORY_SCORE_BETA * recency
                + settings.MEMORY_SCORE_GAMMA * importance
            )
            scored.append((score, lesson))
        scored.sort(key=lambda x: x[0], reverse=True)
        seen: set[str] = set()
        out: list[str] = []
        for _, lesson in scored:
            if lesson in seen:
                continue
            seen.add(lesson)
            out.append(lesson)
            if len(out) >= k:
                break
        return out


_store: MemoryStore | None = None


def get_memory_store() -> MemoryStore | None:
    global _store
    if not settings.QDRANT_ENABLED:
        return None
    if not settings.POLZA_API_KEY.strip():
        log.warning("memory.qdrant.disabled_no_api_key")
        return None
    if _store is None:
        try:
            _store = MemoryStore()
        except Exception as e:
            log.warning("memory.qdrant.init_failed", error=str(e)[:200])
            return None
    return _store
