# Environment And Secrets

## Do Not Commit Secrets

Do not commit `.env` or API keys.

The repository should store only examples and variable names.

## Local Variables

The local `.env` file is expected at:

```text
C:\Users\namzh\Documents\New project 3\.env
```

Known local variables added by the team:

```text
POLZA_API_KEY=
ARENAGO_API_KEY=
```

Do not print their values in logs or chat.

## Platform Variables

The competition PDF says the ArenaGo trading key is automatically added to the deployment as:

```text
SANDBOX_API_KEY
```

The PDF recommends using `SANDBOX_API_KEY` because keys are automatically replaced on the next stage.

The `platform-admin` chart currently injects these keys from Kubernetes Secret `platform-secrets`:

```text
OPENROUTER_API_KEY
SANDBOX_API_KEY
```

`OPENROUTER_API_KEY` appears in the platform chart. It is not described as required by the competition PDF for this bot unless the team intentionally uses OpenRouter.

## Recommended Key Meaning

- `SANDBOX_API_KEY`: ArenaGo trading key in cloud deployment.
- `ARENAGO_API_KEY`: ArenaGo trading key for local development.
- `POLZA_API_KEY`: Polza.ai model aggregator key.

## Current Bot Runtime Defaults

Local Python default:

- `DRY_RUN=true` unless overridden.

Docker/cloud default:

- `DRY_RUN=false`;
- `SECID=SBER`;
- `ORDER_QUANTITY=1`;
- `INTERVAL_HOURS=12`;
- `STATE_PATH=/data/state.json`.

This means the deployed container is intended to submit one SBER market order every 12 hours when `SANDBOX_API_KEY` is present.

## Logging Rules

Logs may mention whether a key is present.

Logs must not include:

- full API key values;
- authorization headers;
- raw `.env` contents;
- request headers containing tokens.
