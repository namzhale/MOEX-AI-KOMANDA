# Cheap LLM Model Eval

Короткая инструкция по дешевому сравнению LLM-моделей на истории без полного дорогого
path-dependent backtest.

## Что делает режим

`scripts.compare_llm_configs` умеет два типа micro-eval:

- `--cheap-model-eval` - старый режим: decision cases выбираются отдельно по готовым interval history.
- `--fair-interval-eval` - основной честный режим для сравнения `1m/2m/5m/10m`.

Для fair interval eval:

- загружается только `1m` история;
- `2m/5m/10m` строятся ресемплингом из `1m`;
- все variants получают общие `anchor_ts`;
- future returns считаются по общей `1m` истории на горизонтах `10/30/60` минут;
- lookback фиксируется по времени, например `240` минут, а не по одинаковому числу баров;
- новости, reflection, memory, prefilter и Algopack flow выключаются внутри eval.

Это не заменяет полноценный конкурсный backtest, но дешево показывает:

- склонность модели к `BUY/SELL/HOLD`;
- качество направления;
- качество HOLD в шуме;
- calibration confidence;
- pressure к turnover;
- сколько решений заблокировал бы Risk Officer;
- примерную стоимость полезного решения.

## Переменные окружения

Минимум для API-прогона:

```powershell
$env:POLZA_API_KEY = "<api-key>"
$env:LLM_BASE_URL = "https://api.deepseek.com"
$env:BACKTEST_MODEL_FLASH = "deepseek-v4-flash"
$env:BACKTEST_MODEL_PRO = "deepseek-v4-pro"
$env:LLM_TIMEOUT_SECONDS = "60"
$env:LLM_FORCE_JSON_OBJECT = "true"
$env:LLM_THINKING = "disabled"
```

Risk можно включать/выключать без изменения кода:

```powershell
$env:RISK_ENABLED = "false"  # проверить чистое поведение модели
$env:RISK_ENABLED = "true"   # проверить модель вместе с Risk Officer
```

Ключи не коммитить. Логи и артефакты прогонов тоже не являются частью кода.

## Smoke без API

Быстро проверить sampler/scoring/resampling без LLM:

```powershell
python -m scripts.compare_llm_configs `
  --fair-interval-eval `
  --technical-only `
  --case-budget 20 `
  --top-k 4 `
  --tickers SBER,GAZP,LKOH,ROSN,NVTK `
  --days 10 `
  --source iss `
  --base-interval 1 `
  --eval-intervals 1,2,5,10 `
  --lookback-minutes 240 `
  --horizons-minutes 10,30,60
```

## Flash-only risk-off run

Основной дешевый диагностический прогон модели без Risk Officer:

```powershell
$env:RISK_ENABLED = "false"

python -m scripts.compare_llm_configs `
  --fair-interval-eval `
  --case-budget 1000 `
  --top-k 4 `
  --tickers SBER,GAZP,LKOH,ROSN,NVTK `
  --days 14 `
  --max-bars 0 `
  --source iss `
  --base-interval 1 `
  --eval-intervals 1,2,5,10 `
  --interval-variants flash_1m,flash_2m,flash_5m,flash_10m `
  --lookback-minutes 240 `
  --horizons-minutes 10,30,60
```

Что смотреть:

- `combined_score` - общий рейтинг;
- `signal_mix` - не победила ли all-HOLD модель;
- `turnover_pressure` - насколько модель склонна торговать;
- `good_decisions` и `cost_per_good_decision` - качество на рубль.

Если sampler нашел меньше `case-budget`, это нормально: `case-budget` - верхняя граница.
Для ровных 1000 cases на каждом interval нужен отдельный action-challenge sampler.

## Risk-on winner run

После risk-off взять лучший variant и повторить только его с Risk Officer:

```powershell
$env:RISK_ENABLED = "true"

python -m scripts.compare_llm_configs `
  --fair-interval-eval `
  --case-budget 1000 `
  --top-k 1 `
  --tickers SBER,GAZP,LKOH,ROSN,NVTK `
  --days 14 `
  --max-bars 0 `
  --source iss `
  --base-interval 1 `
  --eval-intervals 2 `
  --interval-variants flash_2m `
  --lookback-minutes 240 `
  --horizons-minutes 10,30,60
```

В risk-on обязательно проверить:

- `risk_block_rate`;
- `risk_block_reason_counts`;
- насколько сильно `combined_score` просел относительно risk-off.

Если большинство блоков - `min_edge`, значит Risk Officer слишком жестко режет этот
interval/model, и надо отдельно калибровать `RISK_EDGE_VOL_MULT` или `RISK_MIN_EDGE_PCT`.

## Pro/Flash comparison

Чтобы проверить, стоит ли Pro денег, добавить только нужные Pro variants:

```powershell
python -m scripts.compare_llm_configs `
  --fair-interval-eval `
  --case-budget 240 `
  --top-k 6 `
  --tickers SBER,GAZP,LKOH,ROSN,NVTK `
  --days 10 `
  --source iss `
  --base-interval 1 `
  --eval-intervals 1,2,5,10 `
  --interval-variants flash_1m,flash_2m,flash_5m,flash_10m,pro_5m,pro_10m `
  --lookback-minutes 240 `
  --horizons-minutes 10,30,60
```

Не запускать `pro_1m/pro_2m` без явной причины: prompts длинные, стоимость быстро растет.

## Артефакты

Каждый запуск сохраняет файлы в `data/backtest_results`:

- `interval_model_anchors_<ts>.csv` - общие `1m` anchors;
- `interval_model_cases_<ts>.csv` - interval-specific snapshots/cases;
- `interval_model_micro_scores_<ts>.csv` - построчные scores;
- `interval_model_micro_summary_<ts>.json` - итоговая таблица и ranked variants.

Сравнение обычно делать по `interval_model_micro_summary_*.json`.

## Интерпретация метрик

- `combined_score` - общий score: direction, edge, hold, calibration минус turnover и risk blocks.
- `direction_score` - угадано ли направление будущего движения.
- `edge_score` - доходность направления после round-trip cost.
- `hold_quality` - насколько HOLD уместен в шуме и не пропускает сильное движение.
- `confidence_calibration` - высокая confidence должна чаще совпадать с правильным решением.
- `turnover_pressure` - склонность модели генерировать торговые действия.
- `risk_block_rate` - доля решений, которые заблокировал Risk Officer.
- `good_decisions` - количество решений с положительным direction или hold quality.
- `actual_cost_rub` - оценка стоимости API по тарифам и token shape.
- `cost_per_good_decision` - рубли на одно полезное решение.
- `signal_mix` - распределение `BUY/SELL/HOLD`.

Главное правило: all-HOLD может быть хорошим safety baseline, но не обязательно лучший
трейдер. Для конкурса победителя надо дополнительно проверять в 14-дневной автономной
симуляции с реальными cash/positions/nav и Risk Officer.

## Тесты

Перед коммитом:

```powershell
python -m pytest tests\test_interval_model_eval.py tests\test_cheap_model_eval.py -q
```

Если менялись graph prompts или LLM parsing:

```powershell
python -m pytest tests\test_interval_model_eval.py tests\test_graph.py tests\test_cheap_model_eval.py tests\test_llm_direct_deepseek.py -q
```
