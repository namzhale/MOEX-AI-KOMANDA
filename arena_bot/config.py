import os
from dataclasses import dataclass
from pathlib import Path


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class BotConfig:
    api_token: str | None
    bot_name: str
    dry_run: bool
    quantity: int
    interval_hours: int
    state_path: str
    api_base_url: str = "https://arenago.ru"
    secid: str = "SBER"
    loop_forever: bool = True
    error_sleep_seconds: int = 900

    @classmethod
    def from_env(cls) -> "BotConfig":
        api_token = (
            os.getenv("ARENAGO_API_KEY")
            or os.getenv("SANDBOX_API_KEY")
            or os.getenv("ARENAGO_API_TOKEN")
        )
        return cls(
            api_token=api_token,
            bot_name=os.getenv("BOT_NAME", "Team24ArenaBot"),
            dry_run=_parse_bool(os.getenv("DRY_RUN"), True),
            quantity=int(os.getenv("ORDER_QUANTITY", "1")),
            interval_hours=int(os.getenv("INTERVAL_HOURS", "12")),
            state_path=os.getenv("STATE_PATH", "/data/state.json"),
            api_base_url=os.getenv("ARENAGO_API_BASE_URL", "https://arenago.ru"),
            secid=os.getenv("SECID", "SBER"),
            loop_forever=_parse_bool(os.getenv("LOOP_FOREVER"), True),
            error_sleep_seconds=int(os.getenv("ERROR_SLEEP_SECONDS", "900")),
        )
