from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger()


class JsonlJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields) -> None:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **fields,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            log.warning("journal.write_failed", path=str(self.path), error=str(e)[:200])

    def tail(self, n: int = 50) -> list[dict]:
        """Безопасно читает последние N записей. Битые/нечитаемые строки
        пропускает (на проде pod может рестартнуться в момент записи →
        обрезанный последний line, не валим бота)."""
        if not self.path.exists():
            return []
        try:
            with self.path.open("rb") as f:
                # errors="replace" — даже если файл частично битый по UTF-8,
                # читаем что можем.
                raw = f.read().decode("utf-8", errors="replace")
        except Exception:
            log.exception("journal.tail.read_failed", path=str(self.path))
            return []
        lines = raw.splitlines()
        out: list[dict] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
        return out
