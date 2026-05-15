# Scoring And Rules

## Total Score

Maximum score:

```text
100 points
```

The final leaderboard uses a `70+30` score:

- 0-70 points: portfolio value score.
- 0-30 points: expert evaluation.

Portfolio points formula from the PDF:

```text
portfolio_points = 70 / best_team_asset_value * evaluated_team_asset_value
```

Final score:

```text
final_score = portfolio_points + expert_score
```

Good documentation helps expert evaluation.

## Stages

The competition has three stages:

1. Stage 1 - solution development and first trading.
2. Stage 2 - autonomous bot testing with admin keys.
3. Stage 3 - manual review, expert evaluation, rule checks, final score calculation.

Dates from the PDF:

| Stage | Dates |
| --- | --- |
| 1 | May 13 15:00 - May 27 15:00 |
| 2 | May 28 07:00 - June 10 15:00 |
| 3 | June 11 07:00 - June 30 |

The PDF does not include the year in this table. In the current project context, these dates are treated as 2026 unless organizers state otherwise.

## Stage Notes

Stage 1:

- development and manual/semi-automatic trading through API keys;
- open leaderboard by portfolio asset value on `arenago.ru`;
- lasts for two weeks.

Between Stage 1 and Stage 2:

- portfolios are reset;
- trading outside autonomous tests is blocked;
- ArenaGo API keys are replaced for autonomous trading;
- model aggregator token limits are expanded.

Stage 2:

- autonomous bot testing with new API keys;
- open leaderboard by portfolio asset value;
- final asset value is fixed at the end of the competition.

Stage 3:

- ArenaGo checks trading rule violations;
- violators may be penalized or disqualified;
- experts assign expert score;
- winners are selected by final `70+30` leaderboard.

## Restart Rules During Autonomous Tests

If the solution stops during Stage 2, the PDF describes three scenarios:

1. If failure is caused by organizer infrastructure, ArenaGo, provided servers, Polza.ai, or a mass outage, organizers restart the solution.
2. If failure is caused by the team's code, resource exhaustion, third-party hosting, third-party aggregator, or similar non-organizer issues, the solution is not restarted.
3. In rare exceptional cases, organizers may decide to restart a solution.

Important consequence:

- the bot should be able to continue correctly after restart.

## Penalty Rules

If a team violates any trading rule, it receives a 70-point penalty from portfolio points. The PDF says the result cannot go below zero.

Trading rules:

- total agent turnover during Stage 2 must exceed 10,000,000 RUB;
- artificial turnover inflation is visible to experts;
- if a team trains or fine-tunes a model, access to the used model must remain available during and after the hackathon;
- if persistent model access is impossible, the team must provide model weights, scripts, launch instructions, environment description, and training details;
- training details should include datasets, settings, and material parameters.

Turnover means total buys plus sells.

## Disqualification Rules

If a team violates any competition rule, it is disqualified.

Rules:

- no remote portfolio management during autonomous tests in Stage 2;
- all models and technologies used by the team must have licenses that permit free commercial use.

