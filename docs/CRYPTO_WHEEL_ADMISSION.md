# Crypto Wheel Admission

Crypto external repos and packages are not trusted architecture. They are parts
suppliers. The canonical policy is the adjacent
`CRYPTO_WHEEL_ADMISSION.json`.

## Default

Unknown crypto wheels are rejected until they have an admission record.

Allowed roles are limited to market-data SDKs, exchange REST/WebSocket SDKs,
indicator libraries, and transport/serialization libraries. Strategy frameworks,
backtest frameworks, grid bots, arbitrage bots, and portfolio optimizers are
reference-only unless a later architecture decision promotes a small component.

## Hard Bans

- Custody or storage of private keys.
- Order placement without a `k-atana` intent.
- Any route from research/backtest code to an exchange private API.
- Bypassing `KATANA_TRADING_MODE=live_test`,
  `KATANA_ENABLE_CRYPTO_LIVE_TEST=1`, max-notional, and allowlist gates.
- Closed-source live execution cores.
- Unbounded autonomous trading.

## Integration Shape

```text
external wheel -> adapter/normalizer -> crypto PIT schema -> factors/signals
approved intent -> paper/sim reconciliation -> OKXBridge.live_test
```

Research code consumes PIT rows only. Execution code consumes approved intents
only. Anything else is a leak.
