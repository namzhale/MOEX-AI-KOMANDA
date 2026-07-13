from __future__ import annotations

from agent.memory.retrieval import retrieve_memory_context
from agent.runtime.reflection import ReflectionJournal
from agent.schemas import ReflectionRecord
from datetime import UTC, datetime


def test_retrieve_memory_includes_journal_lessons(tmp_path, mocker) -> None:
    mocker.patch("agent.config.settings.REFLECTION_ENABLED", True)
    mocker.patch("agent.config.settings.DATA_DIR", str(tmp_path))
    mocker.patch("agent.memory.retrieval.get_memory_store", return_value=None)

    rj = ReflectionJournal(data_dir=str(tmp_path))
    rj.write_reflection(
        ReflectionRecord(
            symbol="SBER",
            trade_id="t1",
            lesson="Do not chase RSI above 70.",
            source="trade",
            timestamp=datetime.now(UTC),
        )
    )

    ctx = retrieve_memory_context("SBER", include_working=False)
    assert "Do not chase RSI" in ctx
