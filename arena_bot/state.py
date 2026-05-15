import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class BotState:
    last_direction: str | None = None
    last_order_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "BotState":
        last_order_at = data.get("last_order_at")
        return cls(
            last_direction=data.get("last_direction"),
            last_order_at=datetime.fromisoformat(last_order_at) if last_order_at else None,
        )

    def to_dict(self) -> dict:
        return {
            "last_direction": self.last_direction,
            "last_order_at": self.last_order_at.isoformat() if self.last_order_at else None,
        }


class FileStateStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState()
        with self.path.open("r", encoding="utf-8") as state_file:
            return BotState.from_dict(json.load(state_file))

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.tmp")
        with temp_path.open("w", encoding="utf-8") as state_file:
            json.dump(state.to_dict(), state_file, indent=2, sort_keys=True)
            state_file.write("\n")
        temp_path.replace(self.path)


class MemoryStateStore:
    def __init__(self, state: BotState):
        self.state = state

    def load(self) -> BotState:
        return self.state

    def save(self, state: BotState) -> None:
        self.state = state
