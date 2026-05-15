# Competition Brief

## Goal

Build an autonomous AI agent for exchange trading in the ArenaGo test trading contour for Moscow Exchange assets.

The agent should:
- manage a portfolio without manual control during autonomous tests;
- analyze market data such as price, volume, indicators, and other factors;
- decide whether to buy, sell, or hold;
- trade through ArenaGo;
- maximize final portfolio value.

The solution must be deployed on Yandex-provided servers.

## Trading Venue

ArenaGo is an interactive trading platform for virtual-money trading.

Trading availability follows the real exchange schedule.

## Available Assets

The competition PDF lists these available instruments:

- `LKOH`
- `SBER`
- `ROSN`
- `GAZP`
- `VTBR`
- `YDEX`
- `PLZL`
- `T`
- `NVTK`
- `X5`
- `GMKN`
- `MGNT`
- `ALRS`
- `AFLT`
- `CHMF`
- `NLMK`
- `MOEX`
- `SNGSP`
- `MTSS`
- `PIKK`

The portfolio screenshot/chat also mentioned these observed tickers:

- `AFKS`
- `HEAD`
- `SVCB`
- `TATN`
- `SMLT`
- `SPBE`
- `SNGS`
- `TRNFP`
- `POSI`
- `VKCO`
- `MTLR`
- `FLOT`
- `SIBN`
- `SBERP`
- `RNFT`
- `UPRO`

Treat PDF-listed instruments as the primary confirmed trading universe until the API or platform confirms otherwise.

