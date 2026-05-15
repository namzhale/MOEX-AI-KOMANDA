# Team 24 Arena Bot

Minimal trading bot for the hackathon cloud platform. Local Python runs are safe by default through `DRY_RUN=true`; the Docker image is configured for cloud trading through the platform `SANDBOX_API_KEY`.

## What It Does

- Wakes up every minute and checks whether `INTERVAL_HOURS` has elapsed.
- Once per interval, prepares one market order for `SECID`, default `SBER`.
- Alternates direction: first `B`, then `S`, then `B` again.
- Stores cycle state in `/data/state.json`, which matches the platform PVC mount.
- Sends real orders only when `DRY_RUN=false` and an API token is present.

## Local Run

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

One dry-run cycle:

```bash
$env:DRY_RUN="true"
$env:LOOP_FOREVER="false"
$env:STATE_PATH="./state.json"
python -m arena_bot.main
```

## Configuration

Copy `.env.example` values into your runtime environment.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DRY_RUN` | `true` | Keeps the bot from sending real orders. |
| `ARENAGO_API_BASE_URL` | `https://arenago.ru` | Trading API base URL. |
| `ARENAGO_API_KEY` | empty | Local token for live ArenaGo order submission. |
| `SANDBOX_API_KEY` | empty | Platform-provided ArenaGo token. |
| `POLZA_API_KEY` | empty | Polza.ai model aggregator token. |
| `BOT_NAME` | `Team24ArenaBot` | Portfolio/bot name sent to the API. |
| `SECID` | `SBER` | Security code to trade. |
| `ORDER_QUANTITY` | `1` | Number of shares per cycle. |
| `INTERVAL_HOURS` | `12` | Minimum time between orders. |
| `ERROR_SLEEP_SECONDS` | `900` | Backoff after failed API/network cycle. |
| `STATE_PATH` | `/data/state.json` | Persistent state path. |
| `LOG_FILE` | empty locally, `/data/bot.log` in Docker | Optional persistent log copy. |
| `LOG_WEBHOOK_URL` | empty | Optional webhook for startup/cycle/error events. |

## Cloud Deploy

The repository includes `.gitlab-ci.yml` that imports the centralized pipeline from `hackathon/platform-admin`.

The platform chart deploys this container into Kubernetes and mounts persistent storage at `/data`. Manual pipeline jobs can start, stop, restart, and toggle storage for the bot.

Live trading in the cloud uses the platform-provided `SANDBOX_API_KEY`. Local development can use `ARENAGO_API_KEY` in `.env`; keep `DRY_RUN=true` locally unless you intentionally want to send an order.
