"""Smoke-проверка polza.ai: попросить структурированный greeting.

Usage: PYTHONPATH=src python scripts/smoke_llm.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.llm.client import LLMClient
from agent.logging import configure_logging


class Greeting(BaseModel):
    message: str = Field(description="Friendly hello")
    lang: str = Field(description="ISO-639-1 code, например 'ru' или 'en'")


def main() -> None:
    configure_logging("INFO")
    out = LLMClient().complete_json(
        system="You are friendly and concise.",
        user="Поприветствуй меня по-русски одним предложением.",
        schema=Greeting,
    )
    print(out.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
