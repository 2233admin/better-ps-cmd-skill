# Research Architecture

`k-atana` is an A-share-first research terminal. The platform boundary is:

```text
PIT data -> factors -> HMC/state models -> signals -> backtest/paper -> report
```

Execution is not part of the research core. Trading adapters are boundary
outputs and remain gated by `KATANA_TRADING_MODE`.

## Core Contracts

Research contracts live in `backend/app/research/models.py`:

- `DatasetRef`: source, market, frequency, version, and optional `as_of`.
- `FactorDefinition`: named factor with explicit inputs and ownership.
- `FactorValue`: timestamped factor value with `as_of` visibility time.
- `ResearchSignal`: signal with symbol, side, timestamp, `as_of`, confidence, and reason.
- `Experiment`: reproducible research run over datasets, factors, dates, and `as_of`.
- `HMCStateEstimate`: regime/risk posterior emitted by HMC/materialist dynamics.

These models are intentionally broker-neutral. They do not contain account IDs,
order IDs, QMT constants, OKX fields, or UI state.

## Point-in-Time Rule

Research code should use `backend/app/research/pit.py` for historical data.

Current supported PIT datasets:

- `kline_daily`
- `kline_minute`

For these price-bar datasets, `as_of` clips the maximum visible bar time. That
is not enough for all A-share research, but it prevents the easiest future leak.

Datasets not yet PIT-safe:

- fundamentals and financial statements
- index constituents and industry membership
- ST status, suspension status, limit-up/limit-down state
- corporate actions and adjustment factors
- policy/news/macro release calendars

Those datasets must add publication-time or effective-time metadata before they
can enter production experiments.

## HMC Boundary

HMC/materialist dynamics belongs in `backend/app/research/hmc.py`.

Current status:

- implemented as an adapter boundary, not a full HMC engine
- consumes PIT-safe bar windows
- emits `HMCStateEstimate`
- cannot submit orders or call trading adapters

Target use:

```text
PIT A-share window -> HMC posterior/regime -> factor/signal confidence -> report
```

Non-goals:

- no direct order execution
- no QMT calls
- no hidden data fetches inside the model

## Trading Mode Gate

Research mode is the default:

```text
KATANA_TRADING_MODE=research
```

Order submission is blocked in research mode. Paper orders require:

```text
KATANA_TRADING_MODE=paper
```

Boundary live tests require:

```text
KATANA_TRADING_MODE=live_test
```

Crypto live_test also requires the OKX live-test allowlist documented in
`PRODUCT_BOUNDARY.md`.
