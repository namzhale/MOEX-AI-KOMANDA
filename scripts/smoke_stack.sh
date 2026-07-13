#!/usr/bin/env bash
# Быстрая проверка стека перед DRY_RUN / продом.
# Запуск из корня team-24-develop_2:
#   cp .env.example .env   # заполнить ключи
#   ./scripts/smoke_stack.sh

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== 1. Algopack freshness ==="
python -m scripts.algopack_freshness

echo ""
echo "=== 2. Unit tests (algopack + core) ==="
python -m pytest tests/test_algopack_flow.py tests/test_data_market.py -q

echo ""
echo "=== 3. API health (optional, needs running agent) ==="
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  curl -s http://localhost:8000/health
  echo ""
  if [[ -n "${API_TOKEN:-}" ]]; then
    echo "POST /scheduler/tick ..."
    curl -s -X POST http://localhost:8000/scheduler/tick \
      -H "X-API-Token: ${API_TOKEN}"
    echo ""
  else
    echo "Set API_TOKEN in .env to test scheduler tick"
  fi
else
  echo "Agent not running — skip. Start: docker compose up"
fi

echo ""
echo "Done."
