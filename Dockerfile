FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DRY_RUN=false
ENV BOT_NAME=Team24ArenaBot
ENV SECID=SBER
ENV ORDER_QUANTITY=1
ENV INTERVAL_HOURS=12
ENV ERROR_SLEEP_SECONDS=900
ENV STATE_PATH=/data/state.json
ENV LOG_FILE=/data/bot.log
ENV LOOP_FOREVER=true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY arena_bot ./arena_bot

RUN mkdir -p /data

CMD ["python", "-m", "arena_bot.main"]
