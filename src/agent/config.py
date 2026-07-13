from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        # CRLF в .env на Windows иначе ломает httpx-заголовок (Illegal header value).
        str_strip_whitespace=True,
    )

    SANDBOX_API_KEY: str = ""
    ARENAGO_BASE_URL: str = "https://arenago.ru"
    ARENAGO_BOT: str = "team-24"

    # Имя ENV фиксировано чартом организаторов (k8s-секрет инжектится по этому ключу).
    # Внутри может быть как OpenRouter-ключ, так и polza.ai — определяет LLM_BASE_URL.
    POLZA_API_KEY: str = ""
    LLM_BASE_URL: str = "https://polza.ai/api/v1"
    POLZA_BALANCE_FAILSAFE_ENABLED: bool = True
    POLZA_BALANCE_MIN_RUB: float = 0.01
    POLZA_BALANCE_GRACE_MINUTES: int = 30
    POLZA_BALANCE_TIMEOUT_SECONDS: float = 5.0
    LLM_MODEL: str = "openai/gpt-4o-mini"
    # Per-role overrides. Пусто → используется LLM_MODEL.
    # Industry pattern (TradingAgents, FinMem): preprocessing на дешёвой,
    # synthesis на сильной. По умолчанию всё mini — переключение через ENV.
    LLM_MODEL_ANALYST: str = ""
    LLM_MODEL_NEWS: str = ""
    LLM_MODEL_DEBATE: str = ""
    LLM_MODEL_TRADER: str = ""

    def model_for(self, role: str) -> str:
        override = getattr(self, f"LLM_MODEL_{role.upper()}", "") or ""
        return (override or self.LLM_MODEL).strip()

    DRY_RUN: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"
    LOG_WEBHOOK_URL: str = ""
    LOG_WEBHOOK_EVENTS: str = (
        "agent.*,arenago.submit_order.*,arenago.dry_run.submit_order,"
        "risk_*,scheduler.ticker.failed,journal.write_failed"
    )
    LOG_WEBHOOK_TIMEOUT_SECONDS: float = 1.0
    LOG_WEBHOOK_MAX_QUEUE: int = 1000
    DATA_DIR: str = "/data"
    TRADING_COMMISSION_RATE: float = 0.0005
    ARENAGO_DAILY_TRADE_LIMIT: int = 1000

    AGENT_ENABLED: bool = True
    AGENT_TICK_MINUTES: int = 20
    AGENT_INTERVAL: int = 10
    # Глубина окна свечей (календарные дни). На 10-мин 14 дн ≈ 520 баров —
    # с запасом над MIN_BARS=50 и без лишних «миллион лет назад» данных,
    # которые только тормозят фетч/пагинацию и засоряют warmup индикаторов.
    AGENT_CANDLE_DAYS: int = 14
    AGENT_TICKERS: str = ""
    AGENT_RESPECT_MOEX_HOURS: bool = False
    MARKET_CONTEXT_ENABLED: bool = True
    MARKET_CONTEXT_FAST_MINUTES: int = 60
    MARKET_CONTEXT_MID_MINUTES: int = 240
    MARKET_CONTEXT_RETURN_THRESHOLD: float = 0.0025
    MARKET_CONTEXT_REVERSAL_THRESHOLD: float = 0.002
    MARKET_CONTEXT_BULLISH_BREADTH: float = 0.55
    MARKET_CONTEXT_BEARISH_BREADTH: float = 0.45
    # Сколько тикеров обрабатываем параллельно в LLM-фазе одного тика.
    # Risk Officer остаётся sync-точкой — он видит обновлённое cash/positions.
    AGENT_MAX_CONCURRENT_TICKERS: int = 8

    AGENT_DEBATE_ENABLED: bool = True
    AGENT_DEBATE_ROUNDS: int = 1

    # Single-tick flip через ноль (short→long за один тик). false = no-flip
    # дисциплина: пересечение нуля только закрывает до флэта, обратная сторона
    # открывается отдельным решением на следующем тике (анти-churn).
    AGENT_ALLOW_FLIP: bool = False

    # Turnover-pace монитор (observability): floor оборота конкурса и ожидаемое
    # число торговых дней → дневной таргет = FLOOR/DAYS. Только логирование.
    AGENT_TURNOVER_FLOOR_RUB: float = 10_000_000.0
    AGENT_TURNOVER_DAYS: int = 10

    # --- Экономия токенов (см. IMPLEMENTATION.md) ---
    AGENT_PREFILTER_ENABLED: bool = True
    # Пороги откалиброваны под 10-мин свечу: за бар движение/расхождение EMA
    # и MACD в ~√6 раз мельче, чем на 60-мин, поэтому абсолютные пороги ниже —
    # иначе prefilter всё считает «нейтральным» и режет в HOLD без LLM.
    # RSI-полоса уже: внутридневной RSI болтается у 50, широкая полоса = лишние skip.
    AGENT_PREFILTER_RSI_LOW: float = 45.0
    AGENT_PREFILTER_RSI_HIGH: float = 55.0
    # MACD_hist в ЦЕНОВЫХ единицах (не нормирован) → масштаб-зависим. 0.05 под 10-мин.
    AGENT_PREFILTER_MACD_HIST_MAX: float = 0.05
    # EMA20/50 спред нормирован на close (%). 0.3% под 10-мин (было 0.8% для 60-мин).
    AGENT_PREFILTER_EMA_SPREAD_MAX: float = 0.003

    AGENT_EARLY_EXIT_ENABLED: bool = True
    # Early exit только если analyst.confidence <= этого порога и trend=flat.
    # 0.25: на 10-мин analyst часто «flat, conf 0.3» — при 0.35 это резалось до
    # трейдера; пускаем пограничные сетапы дальше, финальное слово у EV-трейдера.
    AGENT_EARLY_EXIT_MAX_CONFIDENCE: float = 0.25

    REFLECTION_ENABLED: bool = True
    # Узел LangGraph после trader: гипотеза до исполнения (short-term memory).
    REFLECTION_IN_GRAPH: bool = False

    # FinMem score weights: α·sim + β·recency + γ·importance
    MEMORY_SCORE_ALPHA: float = 0.5
    MEMORY_SCORE_BETA: float = 0.3
    MEMORY_SCORE_GAMMA: float = 0.2

    # Защита POST /scheduler/tick. Пусто → ручка отключена (503).
    API_TOKEN: str = ""

    # Qdrant long-term memory (кусок 10). При false — только JSONL.
    # В docker-compose с сервисом qdrant можно включить true.
    QDRANT_ENABLED: bool = False
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "team24_reflections"
    EMBEDDING_MODEL: str = "openai/text-embedding-3-small"

    # Meta-reflection: один раз в день после основной сессии MOEX.
    META_REFLECTION_ENABLED: bool = False

    # Сколько тикеров гонять через LLM за один тик (остальные → HOLD без LLM).
    AGENT_LLM_TICKERS_PER_TICK: int = 0  # 0 = весь universe

    # Режим приложения: trading (FastAPI+scheduler) | short_check (one-shot S→B).
    AGENT_MODE: str = "trading"

    WEBHOOK_SOURCE: str = "team-24"

    # Short-check (ArenaGo верификация short). Запуск: python -m agent.runtime.short_check_main
    SHORT_CHECK_SECID: str = "AUTO"
    SHORT_CHECK_CANDIDATES: str = ""
    SHORT_CHECK_QUANTITY: int = 1
    SHORT_CHECK_DELAY_SECONDS: int = 300

    # MOEX market data: iss (public, default, 15-min delayed) | algopack (premium, requires token)
    MARKET_DATA_SOURCE: str = "algopack"
    ALGOPACK_TOKEN: str = ""
    ALGOPACK_BASE_URL: str = "https://apim.moex.com/iss"
    # Super Candles + Mega Alerts (Promo). Работает только при MARKET_DATA_SOURCE=algopack.
    ALGOPACK_FLOW_ENABLED: bool = True
    ALGOPACK_ORDERSTATS_ENABLED: bool = False
    ALGOPACK_MEGA_ALERT_ENABLED: bool = True
    ALGOPACK_MEGA_ALERT_SKIP: bool = True
    ALGOPACK_UNIVERSE_LIQUIDITY_RANK: bool = True
    # Prefilter: |disb| <= порога → нет направленного потока (дополнение к RSI/MACD).
    ALGOPACK_PREFILTER_DISB_ABS_MAX: float = 0.15
    # ob_spread_1mio_bps (MOEX Algopack: базисные пункты). SBER ~2 bps → 50 = редкий skip.
    ALGOPACK_PREFILTER_SPREAD_1MIO_MAX_BPS: float = 50.0
    # Risk: блок opening при spread_1mio_bps выше порога (0 = выкл).
    ALGOPACK_RISK_SPREAD_1MIO_MAX_BPS: float = 80.0

    AGENT_NEWS_ENABLED: bool = True
    NEWS_SOURCES: str = "tass,interfax"
    NEWS_LOOKBACK_HOURS: int = 24
    NEWS_CACHE_TTL_SECONDS: int = 300
    NEWS_MAX_ITEMS_PER_TICKER: int = 5
    NEWS_HTTP_TIMEOUT: float = 10.0
    TASS_RSS_URL: str = "https://tass.ru/rss/v2.xml"
    INTERFAX_RSS_URL: str = "https://www.interfax.ru/rss.asp"
    EDISCLOSURE_BASE_URL: str = "https://gateway.e-disclosure.ru/api/v1"
    EDISCLOSURE_SEARCH_PATH: str = "/search"

    RISK_ENABLED: bool = True
    RISK_MIN_CONFIDENCE: float = 0.35
    RISK_MAX_INSTRUMENT_WEIGHT: float = 0.15
    RISK_MAX_SECTOR_WEIGHT: float = 0.35
    RISK_MAX_VAR_PCT: float = 0.04
    RISK_MAX_DRAWDOWN: float = 0.18
    RISK_MAX_DAILY_LOSS: float = 0.08
    RISK_CASH_BUFFER: float = 0.02
    RISK_VAR_LOOKBACK: int = 60
    RISK_NAV_HISTORY_DAYS: int = 2
    # Не больше N% от NAV (на начало тика) уходит в BUY-заявки за один прогон.
    # Защита от случая, когда трейдер выдаёт 10-15% size_pct на всех тикерах
    # и в сумме съедает 100% кэша за один тик. 0 — гейт выключен.
    RISK_MAX_TICK_BUY_PCT: float = 0.30
    # Минимальный ожидаемый edge для прохождения min_edge gate.
    # 0.0015 ≈ round-trip commission (0.10%) + небольшой запас. 0 — гейт выключен.
    RISK_MIN_EDGE_PCT: float = 0.0015
    # Множитель для vol-grounded оценки edge в min_edge gate:
    # estimated_edge = confidence × σ(per-bar) × RISK_EDGE_VOL_MULT.
    # 3.0 ≈ √(горизонт/бар) для 10-мин режима, иначе σ per-bar мала → min_edge
    # over-block.
    RISK_EDGE_VOL_MULT: float = 3.0

    # Risk-initiated trim: раздутую позицию (|вес| > cap) принудительно сокращаем
    # к кэпу ВЫШЕ сигнала LLM (даже на HOLD). Политика profit-gated + стоп:
    # режем, если позиция в плюсе/безубытке ИЛИ просадка ≥ RISK_TRIM_STOP_PCT.
    RISK_TRIM_ENABLED: bool = False
    RISK_TRIM_BAND: float = 0.10          # триггер при |вес| > cap×(1+band)
    RISK_TRIM_LOSS_TOLERANCE: float = 0.0  # «безубыток»: режем если pnl ≥ -tol
    RISK_TRIM_STOP_PCT: float = 0.03       # стоп: режем в минусе, если просадка ≥ этого
    RISK_TRIM_MAX_PCT_PER_TICK: float = 0.0  # 0 = резать весь излишек за тик

    # Fixed TP/SL bracket: автозакрытие позиции по нереализованному P&L
    # относительно avg_price, ВЫШЕ сигнала LLM (даже на HOLD). Stateless.
    # 0 = соответствующая ветка выключена.
    RISK_TAKE_PROFIT_PCT: float = 0.02   # фиксируем прибыль при +2%
    RISK_STOP_LOSS_PCT: float = 0.02     # режем убыток при -2%
    # Soft profit-lock for short-term trading. Disabled by default in code/tests unless
    # enabled by env; stop-loss remains active regardless.
    RISK_PROFIT_TAKE_ENABLED: bool = False
    RISK_PROFIT_LOCK_PCT: float = 0.007
    RISK_PROFIT_PARTIAL_PCT: float = 0.012
    RISK_PROFIT_FULL_PCT: float = 0.020
    RISK_PROFIT_LOCK_FRACTION: float = 0.50
    RISK_PROFIT_PARTIAL_FRACTION: float = 0.50


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
