# Risk Manager v2 Flash 10m Eval

Date: 2026-05-23
Baseline: `f15637d try new settings`
Candidate: `c7d71f3 fix(prompt): share live decision context` (`origin/develop_2` at eval time)
Goal: compare pre-v2 risk layer vs latest `develop_2` with `deepseek-v4-flash` everywhere and 10-minute candles only.

## Method

- Variant: `flash_10m`
- Models forced to Flash:
  - `BACKTEST_MODEL_FLASH=deepseek/deepseek-v4-flash`
  - `BACKTEST_MODEL_PRO=deepseek/deepseek-v4-flash`
  - `LLM_MODEL_* = deepseek/deepseek-v4-flash`
- Interval: 10 minutes only
- Universe: `SBER, GAZP, LKOH, ROSN, NVTK`
- History: 14 calendar days from MOEX ISS cache/load
- Cases: 100 sampled decision cases
- Horizons: 1/3/6 bars = 10/30/60 minutes
- Transaction assumption in scoring: `round_trip_cost=0.0014` (0.14%)
- LLM cache policy:
  - baseline generated decisions via Polza;
  - candidate reused the same cached LLM decisions;
  - this isolates risk/config behavior from prompt/model drift.

## Summary

| Metric | Baseline `f15637d` | Latest `c7d71f3` | Delta |
|---|---:|---:|---:|
| combined_score | 0.037648 | 0.089648 | +0.052000 |
| risk_block_rate | 18.0% | 5.0% | -13.0 pp |
| risk blocks | 18 / 100 | 5 / 100 | -13 |
| direction_score | 0.1050 | 0.1050 | 0 |
| edge_score | -0.000628 | -0.000628 | 0 |
| hold_quality | 0.3800 | 0.3800 | 0 |
| confidence_calibration | 0.2990 | 0.2990 | 0 |
| turnover_pressure | 0.0265 | 0.0265 | 0 |
| good_decisions | 57 | 57 | 0 |
| signal mix | BUY 19 / SELL 13 / HOLD 68 | BUY 19 / SELL 13 / HOLD 68 | same |

## Risk Gate Diagnostics

Baseline blocked:

| Gate | Count |
|---|---:|
| `min_edge` | 12 |
| `sanity_confidence` | 6 |

Latest blocked:

| Gate | Count |
|---|---:|
| `sanity_confidence` | 3 |
| `sanity_qty_cash` | 2 |

Allowed gate distribution:

| Gate | Baseline | Latest |
|---|---:|---:|
| `hold` | 62 | 62 |
| `risk_trim` | 13 | 13 |
| `all_passed` | 7 | 20 |

## Gate Diff

- Changed gate behavior: 15 cases.
- Newly unblocked by v2: 13 cases.
- Newly blocked by v2: 0 cases.
- Unblocked source gates:
  - `min_edge`: 10 cases.
  - `sanity_confidence`: 3 cases.
- Unblocked actions:
  - BUY: 8
  - SELL: 5
- Min-edge unblocked trades were size-haircut:
  - average original `size_pct`: 8.70%
  - average effective `size_pct`: 5.77%
  - average size reduction: 33.7%

Quality of unblocked cases:

| Bucket | Count |
|---|---:|
| positive direction_score | 2 |
| neutral direction_score | 10 |
| negative direction_score | 1 |

Average edge score of unblocked cases: `-0.001224`.

## Verdict

Risk Manager v2 is better for the intended objective: it stops hard-blocking most weak-but-not-dangerous Flash 10m signals. Block rate fell from 18% to 5%, mainly because `min_edge` became a sizing haircut instead of a hard veto.

This eval does not prove better realized PnL yet. Most newly unblocked cases are neutral after cost in the micro-eval, and the scoring does not fully account for v2 effective size/qty haircuts. Treat the result as `KEEP_SHADOW` or “safe to continue testing”, not as final promotion proof.

## Limitations

- 100 cases only; enough for direction, not final acceptance.
- This is micro-eval, not a full portfolio backtest.
- Candidate reused cached baseline LLM decisions, intentionally isolating risk layer impact.
- Latest prompt/live-context changes are not measured here as an independent effect.
- News is disabled in this backtest path.
- Turnover pressure is based on raw LLM `size_pct`, not post-risk effective size.

## Artifacts

- Baseline metadata: `reports/eval/risk_manager_v2_flash_10m/pre_risk_v2_flash_10m_f15637d/metadata_20260523_171043.json`
- Baseline scores: `reports/eval/risk_manager_v2_flash_10m/pre_risk_v2_flash_10m_f15637d/model_micro_scores_20260523_171043.csv`
- Baseline risk gates: `reports/eval/risk_manager_v2_flash_10m/pre_risk_v2_flash_10m_f15637d/risk_gate_details.json`
- Candidate metadata: `reports/eval/risk_manager_v2_flash_10m/latest_develop2_flash_10m_c7d71f3/metadata_20260523_174042.json`
- Candidate scores: `reports/eval/risk_manager_v2_flash_10m/latest_develop2_flash_10m_c7d71f3/model_micro_scores_20260523_174042.csv`
- Candidate risk gates: `reports/eval/risk_manager_v2_flash_10m/latest_develop2_flash_10m_c7d71f3/risk_gate_details.json`
- Gate diff: `reports/eval/risk_manager_v2_flash_10m/risk_manager_v2_gate_diff.json`

## Recommended Next Eval

1. Run the same setup with `case_budget=1000` overnight.
2. Add post-risk effective size/qty into scoring so turnover and edge are measured after v2 haircuts.
3. Run full portfolio replay/backtest with the same 10m interval and Flash-only model policy.
4. Separately evaluate latest prompt/live-context changes without LLM cache reuse.
