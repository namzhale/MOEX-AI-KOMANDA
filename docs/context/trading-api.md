# ArenaGo Trading API Facts

## Base URL

Known base URL from chat/API examples:

```text
https://arenago.ru
```

## Authentication

Requests use an authorization token in the `Authorization` header.

Known token-related environment variables:

- `SANDBOX_API_KEY` - key injected by the platform according to the competition PDF.
- `ARENAGO_API_KEY` - local env name added by the team.
- `POLZA_API_KEY` - model aggregator key, not the trading key unless explicitly repurposed.
- `ARENAGO_API_TOKEN` - earlier local implementation name; keep as compatibility only if used.

The competition PDF says the ArenaGo key should be accessed through `SANDBOX_API_KEY`, because keys are automatically replaced between stages.

## Submit Market Order

```http
POST /api/submit_order
Content-Type: application/json
Authorization: <token>
```

Payload:

```json
{
  "direction": "B",
  "secid": "SBER",
  "quantity": 10,
  "bot": "MyTradingBot"
}
```

Fields:

- `direction`: `B` for buy, `S` for sell.
- `secid`: security code, for example `SBER`.
- `quantity`: integer number of shares.
- `bot`: portfolio/bot name.

Example success response:

```json
{
  "success": true,
  "message": "Trade submitted successfully",
  "order_value": 1269.4,
  "price": 126.94,
  "quantity": 1,
  "remaining_cash": 429371.46
}
```

Known error responses:

- `{"error": "ERROR: MARKET CLOSED"}` - trading window issue.
- `{"error": "ERROR: NOT VALID SECID"}` - unsupported instrument.
- `{"error": "ERROR: INSUFFICIENT CASH"}` - not enough cash.
- `{"error": "ERROR: BOT {bot_name} HAS REACHED DAILY TRADE LIMIT"}` - daily trade limit.

Known daily API/platform limit from chat:

- 1000 trades per day per bot.

Known trading window from chat:

- 00:00-23:50 MSK, subject to platform/exchange rules.

## Get Trades

```http
GET /api/trades/<portfolio>
Authorization: <token>
```

Returns today's trades for the requested portfolio.

Known trade fields from example:

- `tradedate`
- `tradetime`
- `direction`
- `secid`
- `quantity`
- `price`
- `bot`

## Get Positions

```http
GET /api/positions/<portfolio>
Authorization: <token>
```

Returns open positions for the requested portfolio.

Known position fields from example:

- `secid`
- `position`
- `average_price`
- `bot`

## Get Bots

```http
GET /api/bots
Authorization: <token>
```

Known bot fields from example:

- `name`
- `cash_balance`

