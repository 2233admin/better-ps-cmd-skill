# Product Boundary

`k-atana` is a multi-market quantitative research platform terminal.

```text
data ingestion -> factors/features -> strategy/signals -> backtest -> paper/live_test -> reports/frontend
```

It is not a default autonomous live trading system. Live execution paths are
explicitly gated and treated as boundary adapters.

## Trading Modes

`KATANA_TRADING_MODE` controls the maximum allowed execution level:

```text
research   research, backtest, and signals only. This is the default.
paper      simulated execution only.
live_test  limited crypto real-order testing only.
live       reserved; not enabled by default.
```

Definitions:

- `research`: data loading, feature generation, signal generation, reports, and terminal views.
- `backtest`: historical simulation against stored or fetched market data.
- `paper`: simulated order handling inside `k-atana`; no broker adapter may be called.
- `live_test`: explicitly enabled, small-size real-order testing for crypto only.
- `live`: reserved for a future governed release and rejected by current defaults.

## Market Scope

`crypto` is active for research, backtest, paper trading, and restricted OKX
`live_test` execution.

`ashare` is active for research, backtest, signals, manual operator workflows,
and QMT/EasyXT boundary testing. QMT is an optional execution boundary, not a
default runtime dependency.

`futures` is legacy. It remains available for reference under `legacy/`, but it
is outside the default development and test gate.

## Crypto Live Test Gate

Crypto real-order testing requires all of these:

```text
KATANA_TRADING_MODE=live_test
KATANA_ENABLE_CRYPTO_LIVE_TEST=1
KATANA_OKX_MAX_ORDER_USDT=<number>
KATANA_OKX_ALLOWED_PAIRS=BTC-USDT,ETH-USDT
```

If any value is missing, malformed, or violated, the OKX adapter must reject the
order before calling the OKX client.

External crypto GitHub repositories and packages must pass the admission policy
in `CRYPTO_WHEEL_ADMISSION.json` before runtime use. Unknown wheels are rejected
by default; strategy/bot frameworks are reference-only unless separately
promoted by an architecture decision.

## A-Share Execution Boundary

A-share execution defaults to manual review:

```text
KATANA_ASHARE_EXECUTION=manual
```

Only `KATANA_ASHARE_EXECUTION=easyxt_bridge` may submit a broker-neutral intent
to the external EasyXT bridge. Application research and API code must not import
or call `xtquant` directly. Any QMT-specific SDK interaction belongs behind the
external bridge or explicit opt-in integration tests.

## Migration Rule

Active trading code lives under `backend/app/trading/`. The old
`backend/app/trade/` package is a compatibility wrapper for one migration cycle.
New imports should use `app.trading.*`; old imports must keep working until the
wrapper removal is scheduled and covered by an import audit.
