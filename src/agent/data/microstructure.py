"""Algopack Super Candles + Mega Alerts → features для графа и risk.

Используется только при MARKET_DATA_SOURCE=algopack и ALGOPACK_FLOW_ENABLED=true.
При ошибке API возвращаем пустой dict — pipeline не падает.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import structlog

from agent.config import settings

log = structlog.get_logger()

# TradeStats (5m)
_FLOW_TRADE_KEYS = (
    "disb",
    "pr_change",
    "pr_vwap_b",
    "pr_vwap_s",
    "vol_b",
    "vol_s",
    "val_b",
    "val_s",
    "trades_b",
    "trades_s",
    "pr_std",
)
# OBStats (5m)
_FLOW_OB_KEYS = (
    "spread_bbo",
    "spread_1mio",
    "imbalance_vol_bbo",
    "imbalance_vol",
    "vol_b",
    "vol_s",
)
# OrderStats (5m) — префикс order_
_FLOW_ORDER_KEYS = (
    "put_orders_b",
    "put_orders_s",
    "cancel_orders_b",
    "cancel_orders_s",
    "put_vol_b",
    "put_vol_s",
)

_ALERTS_CACHE: tuple[str, list[dict]] | None = None


def flow_enabled() -> bool:
    return (
        (settings.MARKET_DATA_SOURCE or "").lower() == "algopack"
        and settings.ALGOPACK_FLOW_ENABLED
        and bool((settings.ALGOPACK_TOKEN or "").strip())
    )


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(fv) or math.isinf(fv):
        return default
    return fv


def _prefix_keys(raw: dict, prefix: str, keys: tuple[str, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in keys:
        val = raw.get(key)
        if val is None:
            continue
        out[f"{prefix}{key}"] = _safe_float(val)
    return out


def _normalize_ob_spread_features(merged: dict[str, float]) -> None:
    """MOEX OBStats отдаёт spread в базисных пунктах (bps). Дублируем в *_bps и долю."""
    for key in ("spread_bbo", "spread_1mio", "spread_lv10"):
        bps_key = f"ob_{key}_bps"
        frac_key = f"ob_{key}"
        if bps_key not in merged and frac_key not in merged:
            continue
        bps = merged.get(bps_key, merged.get(frac_key, 0.0))
        merged[f"ob_{key}_bps"] = bps
        merged[f"ob_{key}"] = bps / 10_000.0


def load_flow_features(symbol: str) -> dict[str, float]:
    """Последние 5m-метрики Algopack по тикеру (Super Candles)."""
    if not flow_enabled():
        return {}

    from agent.data.market import _get_algopack_client

    client = _get_algopack_client()
    merged: dict[str, float] = {}
    try:
        ts_row = client.get_tradestats_latest(symbol)
        merged.update(_prefix_keys(ts_row, "", _FLOW_TRADE_KEYS))
        if ts_row:
            merged["flow_val_total"] = _safe_float(ts_row.get("val_b")) + _safe_float(
                ts_row.get("val_s")
            )
    except Exception as e:
        log.warning("flow.tradestats_failed", symbol=symbol, error=str(e)[:200])

    try:
        ob_row = client.get_obstats_latest(symbol)
        merged.update(_prefix_keys(ob_row, "ob_", _FLOW_OB_KEYS))
        _normalize_ob_spread_features(merged)
    except Exception as e:
        log.warning("flow.obstats_failed", symbol=symbol, error=str(e)[:200])

    if settings.ALGOPACK_ORDERSTATS_ENABLED:
        try:
            os_row = client.get_orderstats_latest(symbol)
            merged.update(_prefix_keys(os_row, "order_", _FLOW_ORDER_KEYS))
        except Exception as e:
            log.warning("flow.orderstats_failed", symbol=symbol, error=str(e)[:200])

    if merged:
        log.debug("flow.loaded", symbol=symbol, keys=sorted(merged.keys())[:12])
    return merged


def format_flow_prompt_block(features: dict[str, float]) -> str:
    """Компактный блок для analyst/trader (только flow_* / disb / ob_*)."""
    if not features:
        return ""
    flow = {
        k: round(v, 6) if isinstance(v, float) else v
        for k, v in features.items()
        if k.startswith(("flow_", "disb", "pr_vwap", "pr_change", "ob_", "order_"))
        or k in _FLOW_TRADE_KEYS
    }
    if not flow:
        return ""
    import json

    return (
        "MOEX Algopack flow (5m Super Candles, last bar):\n"
        f"{json.dumps(flow, indent=2, default=float)}\n"
    )


def _today_iso() -> str:
    return datetime.now(UTC).astimezone().date().isoformat()


def fetch_mega_alerts_today(*, max_rows: int = 200) -> list[dict]:
    """Кэш Mega Alerts за сегодня (1 запрос на тик scheduler)."""
    global _ALERTS_CACHE
    if not flow_enabled() or not settings.ALGOPACK_MEGA_ALERT_ENABLED:
        return []

    today = _today_iso()
    if _ALERTS_CACHE and _ALERTS_CACHE[0] == today:
        return _ALERTS_CACHE[1]

    from agent.data.market import _get_algopack_client

    try:
        rows = _get_algopack_client().get_mega_alerts(date=today, latest=True)
    except Exception as e:
        log.warning("flow.alerts_failed", error=str(e)[:200])
        rows = []

    _ALERTS_CACHE = (today, rows[:max_rows])
    return _ALERTS_CACHE[1]


def mega_alert_symbols_today(symbols: tuple[str, ...] | list[str] | None = None) -> set[str]:
    """Тикеры с Mega Alert за сегодня."""
    allowed = {s.upper() for s in symbols} if symbols else None
    out: set[str] = set()
    for row in fetch_mega_alerts_today():
        secid = str(row.get("secid") or "").upper()
        if not secid:
            continue
        if allowed is not None and secid not in allowed:
            continue
        out.add(secid)
    return out


def fetch_universe_liquidity(universe: tuple[str, ...]) -> dict[str, float]:
    """Скор ликвидности: val_b + val_s из последнего TradeStats (5m)."""
    if not flow_enabled() or not settings.ALGOPACK_UNIVERSE_LIQUIDITY_RANK:
        return {sym: 1.0 for sym in universe}

    from agent.data.market import _get_algopack_client

    try:
        rows = _get_algopack_client().get_tradestats_market_latest()
    except Exception as e:
        log.warning("flow.liquidity_rank_failed", error=str(e)[:200])
        return {sym: 1.0 for sym in universe}

    scores: dict[str, float] = {}
    for row in rows:
        secid = str(row.get("secid") or "").upper()
        if secid not in universe:
            continue
        scores[secid] = _safe_float(row.get("val_b")) + _safe_float(row.get("val_s"))
    for sym in universe:
        scores.setdefault(sym, 0.0)
    return scores


def flow_context_for_reflection(symbol: str) -> str:
    """Краткий текст для trade/meta reflection."""
    feats = load_flow_features(symbol)
    if not feats:
        return ""
    parts = []
    if "disb" in feats:
        parts.append(f"disb={feats['disb']:.3f}")
    if "pr_change" in feats:
        parts.append(f"pr_change={feats['pr_change']:.2%}")
    if "ob_spread_1mio_bps" in feats:
        parts.append(f"spread_1mio={feats['ob_spread_1mio_bps']:.1f}bps")
    elif "ob_spread_bbo_bps" in feats:
        parts.append(f"spread_bbo={feats['ob_spread_bbo_bps']:.1f}bps")
    if "ob_imbalance_vol_bbo" in feats:
        parts.append(f"imbalance_bbo={feats['ob_imbalance_vol_bbo']:.0f}")
    return "Flow at close: " + ", ".join(parts) if parts else ""
