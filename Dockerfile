FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY src ./src

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

ENV LLM_BASE_URL=https://polza.ai/api/v1
ENV POLZA_BALANCE_FAILSAFE_ENABLED=true
ENV POLZA_BALANCE_MIN_RUB=0.01
ENV POLZA_BALANCE_GRACE_MINUTES=30
ENV POLZA_BALANCE_TIMEOUT_SECONDS=5.0
# Только open-source модели с commercial-use лицензией (правило хакатона).
# DeepSeek V4 — MIT, открытые веса: https://huggingface.co/deepseek-ai
ENV LLM_MODEL=deepseek/deepseek-v4-flash
# Per-role overrides. Пусто → fallback на LLM_MODEL.
# trader получает pro-вариант для финального синтеза bull/bear + news + analyst.
ENV LLM_MODEL_ANALYST=deepseek/deepseek-v4-flash
ENV LLM_MODEL_NEWS=deepseek/deepseek-v4-flash
ENV LLM_MODEL_DEBATE=deepseek/deepseek-v4-flash
ENV LLM_MODEL_TRADER=deepseek/deepseek-v4-pro

ENV ARENAGO_BASE_URL=https://arenago.ru
ENV ARENAGO_BOT=Команда

ENV DRY_RUN=false
ENV LOG_LEVEL=INFO
ENV LOG_FORMAT=console
ENV LOG_WEBHOOK_URL=
ENV DATA_DIR=/data
ENV TRADING_COMMISSION_RATE=0.0005
ENV ARENAGO_DAILY_TRADE_LIMIT=1000

ENV AGENT_ENABLED=true
ENV AGENT_TICK_MINUTES=20
# 10-мин свечи. Фетчер algopack теперь пагинирует (тянет последнюю страницу),
# так что окно days=30 (>500 баров) больше не отдаёт протухшие цены.
ENV AGENT_INTERVAL=10
# Окно свечей: 14 дн × 10-мин ≈ 520 баров (>> MIN_BARS=50). Не тянем 30+ дней —
# лишние старые бары только тормозят фетч и портят warmup индикаторов.
ENV AGENT_CANDLE_DAYS=14
ENV AGENT_TICKERS=
ENV AGENT_RESPECT_MOEX_HOURS=false
ENV MARKET_CONTEXT_ENABLED=true
ENV MARKET_CONTEXT_FAST_MINUTES=60
ENV MARKET_CONTEXT_MID_MINUTES=240
ENV MARKET_CONTEXT_RETURN_THRESHOLD=0.0025
ENV MARKET_CONTEXT_REVERSAL_THRESHOLD=0.002
ENV MARKET_CONTEXT_BULLISH_BREADTH=0.55
ENV MARKET_CONTEXT_BEARISH_BREADTH=0.45
ENV AGENT_MAX_CONCURRENT_TICKERS=8

# Prefilter откалиброван под 10-мин (ход/EMA/MACD за бар ~√6× мельче 60-мин).
ENV AGENT_PREFILTER_RSI_LOW=45
ENV AGENT_PREFILTER_RSI_HIGH=55
ENV AGENT_PREFILTER_MACD_HIST_MAX=0.05
ENV AGENT_PREFILTER_EMA_SPREAD_MAX=0.003
# Early-exit мягче под 10-мин: пограничные flat-сетапы доходят до EV-трейдера.
ENV AGENT_EARLY_EXIT_MAX_CONFIDENCE=0.25

ENV MARKET_DATA_SOURCE=algopack
ENV ALGOPACK_BASE_URL=https://apim.moex.com/iss
ENV ALGOPACK_TOKEN=
# ALGOPACK_TOKEN можно переопределить через .env / k8s secret.

ENV ALGOPACK_FLOW_ENABLED=true
ENV ALGOPACK_PREFILTER_SPREAD_1MIO_MAX_BPS=50
ENV ALGOPACK_RISK_SPREAD_1MIO_MAX_BPS=80

ENV AGENT_TICKER_TIMEOUT_SEC=360
ENV AGENT_DEBATE_ENABLED=true
ENV AGENT_DEBATE_ROUNDS=1
ENV REFLECTION_IN_GRAPH=false
ENV META_REFLECTION_ENABLED=false
# false = no-flip: пересечение нуля только закрывает до флэта (анти-churn).
ENV AGENT_ALLOW_FLIP=false
# Turnover-pace монитор (observability): floor 10М / число торговых дней конкурса.
ENV AGENT_TURNOVER_FLOOR_RUB=10000000
ENV AGENT_TURNOVER_DAYS=10

ENV AGENT_NEWS_ENABLED=true
ENV NEWS_SOURCES=tass,interfax
ENV NEWS_LOOKBACK_HOURS=24
ENV NEWS_CACHE_TTL_SECONDS=300
ENV NEWS_MAX_ITEMS_PER_TICKER=5
ENV NEWS_HTTP_TIMEOUT=10.0
ENV TASS_RSS_URL=https://tass.ru/rss/v2.xml
ENV INTERFAX_RSS_URL=https://www.interfax.ru/rss.asp
ENV EDISCLOSURE_BASE_URL=https://gateway.e-disclosure.ru/api/v1
ENV EDISCLOSURE_SEARCH_PATH=/search

ENV RISK_ENABLED=true
ENV RISK_MIN_CONFIDENCE=0.35
ENV RISK_MAX_INSTRUMENT_WEIGHT=0.15
ENV RISK_MAX_SECTOR_WEIGHT=0.35
ENV RISK_MAX_VAR_PCT=0.04
ENV RISK_MAX_DRAWDOWN=0.18
ENV RISK_MAX_DAILY_LOSS=0.08
ENV RISK_CASH_BUFFER=0.02
ENV RISK_VAR_LOOKBACK=60
ENV RISK_NAV_HISTORY_DAYS=2
ENV RISK_MAX_TICK_BUY_PCT=0.30
# 0.0015 ≈ round-trip commission (0.10%) + небольшой запас. Было 0.003 (3× cost) —
# слишком жёстко, резало ~4 из 5 сделок → угроза floor оборота 10М.
ENV RISK_MIN_EDGE_PCT=0.0015
# estimated_edge = confidence × σ(per-bar) × MULT. На 10-мин σ per-bar в ~√6×
# мельче 60-мин, а required_edge фиксирован → min_edge молча резал conf<~0.6.
# MULT=3 ≈ √(горизонт ~1.5ч / 10мин) приводит σ к ожидаемому ходу за сделку.
# ВАЖНО: значение под interval=10. Сменишь интервал — пересчитай (√(90/interval)).
ENV RISK_EDGE_VOL_MULT=3.0
ENV RISK_TRIM_ENABLED=false
ENV RISK_TRIM_BAND=0.10
ENV RISK_TRIM_LOSS_TOLERANCE=0.0
ENV RISK_TRIM_STOP_PCT=0.03
ENV RISK_TRIM_MAX_PCT_PER_TICK=0.0
# Fixed TP/SL bracket: фиксируем прибыль на +2%, режем убыток на -2% (по avg_price).
ENV RISK_TAKE_PROFIT_PCT=0.02
ENV RISK_STOP_LOSS_PCT=0.02
ENV RISK_PROFIT_TAKE_ENABLED=false
ENV RISK_PROFIT_LOCK_PCT=0.007
ENV RISK_PROFIT_PARTIAL_PCT=0.012
ENV RISK_PROFIT_FULL_PCT=0.020
ENV RISK_PROFIT_LOCK_FRACTION=0.50
ENV RISK_PROFIT_PARTIAL_FRACTION=0.50

EXPOSE 8000

CMD ["uvicorn", "agent.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--timeout-graceful-shutdown", "10", \
     "--timeout-keep-alive", "5"]
