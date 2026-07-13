from __future__ import annotations

from datetime import UTC, datetime

from agent.memory.retrieval import format_memory_block
from agent.runtime.reflection import (
    ReflectionJournal,
    is_closing_operation,
    reflect_on_trade,
)
from agent.schemas import AnalystOutput, Decision, ReflectionRecord


def test_is_closing_operation() -> None:
    assert is_closing_operation("close_long")
    assert is_closing_operation("cover_short")
    assert not is_closing_operation("open_long")


def test_reflect_on_trade_writes_journal(tmp_path, mocker) -> None:
    mocker.patch("agent.config.settings.REFLECTION_ENABLED", True)

    from agent.runtime.reflection import ReflectionLesson

    fake = ReflectionLesson(
        lesson="Avoid chasing flat RSI.",
        tags=["hold", "flat"],
        importance=0.7,
    )
    mocker.patch("agent.runtime.reflection.LLMClient.complete_json", return_value=fake)

    decision = Decision(
        symbol="SBER",
        signal="SELL",
        size_pct=0.1,
        confidence=0.6,
        reasoning="Take profit.",
        analyst_output=AnalystOutput(
            trend="up",
            momentum="weak_up",
            volatility="normal",
            summary="Uptrend fading.",
            confidence=0.5,
        ),
        timestamp=datetime.now(UTC),
    )

    journal = ReflectionJournal(data_dir=str(tmp_path))
    record = reflect_on_trade(
        symbol="SBER",
        decision=decision,
        op_type="close_long",
        action_summary={"status": "sell_submitted"},
        journal=journal,
    )
    assert record is not None
    assert "SBER" in record.trade_id

    mocker.patch("agent.config.settings.DATA_DIR", str(tmp_path))
    mocker.patch("agent.memory.retrieval.get_memory_store", return_value=None)
    block = format_memory_block("SBER")
    assert "Avoid chasing" in block


def test_reflect_skips_non_closing(tmp_path, mocker) -> None:
    mocker.patch("agent.config.settings.REFLECTION_ENABLED", True)
    decision = Decision(
        symbol="SBER",
        signal="BUY",
        size_pct=0.05,
        confidence=0.5,
        reasoning="Open.",
        analyst_output=AnalystOutput(
            trend="up",
            momentum="weak_up",
            volatility="normal",
            summary="x",
            confidence=0.5,
        ),
        timestamp=datetime.now(UTC),
    )
    journal = ReflectionJournal(data_dir=str(tmp_path))
    assert reflect_on_trade(
        symbol="SBER",
        decision=decision,
        op_type="open_long",
        action_summary={},
        journal=journal,
    ) is None
