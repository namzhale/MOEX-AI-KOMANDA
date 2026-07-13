import os
import sys
from pathlib import Path

import pytest

# Гарантируем src/ в sys.path при запуске pytest из корня
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Безопасные дефолты для тестов: без реальных API-вызовов
os.environ.setdefault("POLZA_API_KEY", "test-key")
os.environ.setdefault("SANDBOX_API_KEY", "test-key")
os.environ.setdefault("DRY_RUN", "true")
# Scheduler в тестах ВЫКЛЮЧЕН — он стартовал бы фоновый asyncio-цикл, который
# полез бы в ArenaGo и polza.ai с тестовыми ключами.
os.environ.setdefault("AGENT_ENABLED", "false")
os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parent / ".tmp_data"))

# langchain-core 0.3.x ожидает атрибуты langchain.debug/verbose; пакет langchain
# не в requirements — подставляем минимальный shim, иначе LangGraph.invoke падает.
try:
    import langchain  # type: ignore[import-untyped]

    if not hasattr(langchain, "debug"):
        langchain.debug = False  # type: ignore[attr-defined]
    if not hasattr(langchain, "verbose"):
        langchain.verbose = False  # type: ignore[attr-defined]
except ImportError:
    pass


@pytest.fixture(autouse=True)
def _no_moex_lot_fetch(monkeypatch):
    """Отключаем MOEX ISS fetch во всех тестах — детерминируем lot_sizes из
    LOT_SIZE_BY_TICKER. Иначе тесты зависели бы от живого ISS, и при изменении
    биржевых лотов начали бы случайно падать.

    Тесты, которым важно поведение самого fetch'а — патчат отдельно.
    """
    try:
        from agent.runtime import scheduler as scheduler_mod
    except Exception:
        return
    monkeypatch.setattr(scheduler_mod, "fetch_lot_sizes", lambda *_a, **_kw: {})
