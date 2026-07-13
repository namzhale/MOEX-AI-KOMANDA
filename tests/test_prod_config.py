from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _docker_env(name: str) -> str:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(rf"^ENV {re.escape(name)}=(.*)$", text, flags=re.MULTILINE)
    assert match is not None, f"missing ENV {name}"
    return match.group(1).strip()


def _settings_default(name: str) -> str:
    text = (ROOT / "src" / "agent" / "config.py").read_text(encoding="utf-8")
    match = re.search(
        rf"^\s+{re.escape(name)}:\s*[^=]+=\s*([^\n#]+)",
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing Settings default {name}"
    value = match.group(1).strip()
    return value.strip('"').strip("'")


def test_pro_model_is_used_only_by_trader_in_prod_image() -> None:
    assert _docker_env("LLM_MODEL") == "deepseek/deepseek-v4-flash"
    assert _docker_env("LLM_MODEL_ANALYST") == "deepseek/deepseek-v4-flash"
    assert _docker_env("LLM_MODEL_NEWS") == "deepseek/deepseek-v4-flash"
    assert _docker_env("LLM_MODEL_DEBATE") == "deepseek/deepseek-v4-flash"
    assert _docker_env("LLM_MODEL_TRADER") == "deepseek/deepseek-v4-pro"


def test_prod_risk_sanity_values_are_stabilized() -> None:
    assert _docker_env("ARENAGO_DAILY_TRADE_LIMIT") == "1000"
    assert _docker_env("REFLECTION_IN_GRAPH") == "false"
    assert _docker_env("META_REFLECTION_ENABLED") == "false"
    assert _docker_env("RISK_MAX_TICK_BUY_PCT") == "0.30"
    assert _docker_env("RISK_EDGE_VOL_MULT") == "3.0"
    assert _docker_env("RISK_MIN_EDGE_PCT") == "0.0015"
    assert _docker_env("RISK_TAKE_PROFIT_PCT") == "0.02"
    assert _docker_env("RISK_STOP_LOSS_PCT") == "0.02"
    assert _docker_env("AGENT_INTERVAL") == "10"
    assert _docker_env("AGENT_CANDLE_DAYS") == "14"
    assert _docker_env("RISK_PROFIT_TAKE_ENABLED") == "false"


def test_prod_docker_and_settings_defaults_do_not_drift() -> None:
    synced = {
        "ARENAGO_DAILY_TRADE_LIMIT": "1000",
        "AGENT_TICK_MINUTES": "20",
        "AGENT_INTERVAL": "10",
        "AGENT_CANDLE_DAYS": "14",
        "MARKET_CONTEXT_ENABLED": "true",
        "MARKET_CONTEXT_FAST_MINUTES": "60",
        "MARKET_CONTEXT_MID_MINUTES": "240",
        "MARKET_CONTEXT_RETURN_THRESHOLD": "0.0025",
        "MARKET_CONTEXT_REVERSAL_THRESHOLD": "0.002",
        "MARKET_CONTEXT_BULLISH_BREADTH": "0.55",
        "MARKET_CONTEXT_BEARISH_BREADTH": "0.45",
        "AGENT_DEBATE_ROUNDS": "1",
        "MARKET_DATA_SOURCE": "algopack",
        "RISK_MAX_DRAWDOWN": "0.18",
        "RISK_MAX_DAILY_LOSS": "0.08",
        "RISK_NAV_HISTORY_DAYS": "2",
        "RISK_MAX_TICK_BUY_PCT": "0.30",
        "RISK_MIN_EDGE_PCT": "0.0015",
        "RISK_TRIM_ENABLED": "false",
        "RISK_TAKE_PROFIT_PCT": "0.02",
        "RISK_STOP_LOSS_PCT": "0.02",
    }
    for name, expected in synced.items():
        assert _docker_env(name).lower() == expected.lower()
        assert _settings_default(name).lower() == expected.lower()


def test_prod_polza_balance_failsafe_is_enabled() -> None:
    assert _docker_env("POLZA_BALANCE_FAILSAFE_ENABLED") == "true"
    assert _docker_env("POLZA_BALANCE_GRACE_MINUTES") == "30"
    assert _docker_env("POLZA_BALANCE_MIN_RUB") == "0.01"
