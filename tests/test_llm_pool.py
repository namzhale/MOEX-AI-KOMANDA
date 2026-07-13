"""Per-role LLM pool: resolution + deduplication."""

from __future__ import annotations

import pytest

from agent.config import settings
from agent.graph.build import _build_llm_pool


def test_model_for_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setattr(settings, "LLM_MODEL_TRADER", "")
    monkeypatch.setattr(settings, "LLM_MODEL_NEWS", "")
    assert settings.model_for("trader") == "openai/gpt-4o-mini"
    assert settings.model_for("news") == "openai/gpt-4o-mini"


def test_model_for_uses_override_when_present(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setattr(settings, "LLM_MODEL_TRADER", "anthropic/claude-3-5-sonnet")
    # Явно очищаем остальные override'ы, чтобы тест был детерминистичен
    # вне зависимости от .env / Dockerfile-наследия в окружении.
    monkeypatch.setattr(settings, "LLM_MODEL_ANALYST", "")
    monkeypatch.setattr(settings, "LLM_MODEL_NEWS", "")
    monkeypatch.setattr(settings, "LLM_MODEL_DEBATE", "")
    assert settings.model_for("trader") == "anthropic/claude-3-5-sonnet"
    assert settings.model_for("analyst") == "openai/gpt-4o-mini"


def test_pool_deduplicates_same_model(monkeypatch) -> None:
    """4 роли на одной модели → один объект LLMClient переиспользуется."""
    monkeypatch.setattr(settings, "LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setattr(settings, "LLM_MODEL_ANALYST", "")
    monkeypatch.setattr(settings, "LLM_MODEL_NEWS", "")
    monkeypatch.setattr(settings, "LLM_MODEL_DEBATE", "")
    monkeypatch.setattr(settings, "LLM_MODEL_TRADER", "")
    pool = _build_llm_pool(default_llm=None)
    # Все 4 роли указывают на один и тот же объект
    unique_ids = {id(client) for client in pool.values()}
    assert len(unique_ids) == 1


def test_pool_creates_separate_clients_per_unique_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setattr(settings, "LLM_MODEL_ANALYST", "")  # fallback
    monkeypatch.setattr(settings, "LLM_MODEL_NEWS", "anthropic/claude-3-5-sonnet")
    monkeypatch.setattr(settings, "LLM_MODEL_DEBATE", "")  # fallback
    monkeypatch.setattr(settings, "LLM_MODEL_TRADER", "anthropic/claude-3-5-sonnet")
    pool = _build_llm_pool(default_llm=None)
    # analyst и debate — на mini (один объект); news и trader — на sonnet (другой)
    assert pool["analyst"] is pool["debate"]
    assert pool["news"] is pool["trader"]
    assert pool["analyst"] is not pool["news"]
    # Каждый клиент знает свою роль (для логирования)
    assert pool["analyst"].model == "openai/gpt-4o-mini"
    assert pool["trader"].model == "anthropic/claude-3-5-sonnet"


def test_pool_uses_default_llm_when_provided() -> None:
    """Тесты передают свой мок — пул должен использовать его для всех ролей."""
    class _MockLLM:
        model = "mock"

    mock = _MockLLM()
    pool = _build_llm_pool(default_llm=mock)
    for role in ("analyst", "news", "debate", "trader"):
        assert pool[role] is mock
