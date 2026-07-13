"""Юнит-тесты LLM-клиента — без сетевых вызовов, OpenAI SDK замокан."""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from agent.llm.client import LLMClient


class _Reply(BaseModel):
    text: str


def _make_parse_response(parsed: _Reply) -> SimpleNamespace:
    choice = SimpleNamespace(message=SimpleNamespace(parsed=parsed, content=None))
    return SimpleNamespace(choices=[choice], usage=None)


def _make_json_response(content: str) -> SimpleNamespace:
    choice = SimpleNamespace(message=SimpleNamespace(content=content, parsed=None))
    return SimpleNamespace(choices=[choice], usage=None)


def test_complete_json_uses_parse_path(mocker) -> None:
    client = LLMClient(api_key="x")
    fake_parse = mocker.patch.object(
        client.client.beta.chat.completions,
        "parse",
        return_value=_make_parse_response(_Reply(text="ok")),
    )
    out = client.complete_json("sys", "user", _Reply)
    assert isinstance(out, _Reply)
    assert out.text == "ok"
    fake_parse.assert_called_once()


def test_complete_json_falls_back_to_json_object(mocker) -> None:
    client = LLMClient(api_key="x")
    mocker.patch.object(
        client.client.beta.chat.completions,
        "parse",
        side_effect=RuntimeError("parse not supported"),
    )
    fake_create = mocker.patch.object(
        client.client.chat.completions,
        "create",
        return_value=_make_json_response('{"text": "fallback"}'),
    )
    out = client.complete_json("sys", "user", _Reply)
    assert out.text == "fallback"
    fake_create.assert_called_once()
