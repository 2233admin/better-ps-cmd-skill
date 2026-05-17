# Research Architecture

`k-atana` is an A-share-first research terminal. The platform boundary is:

```text
PIT data -> factors -> HMC/state models -> signals -> backtest/paper -> report
```

Execution is not part of the research core. Trading adapters are boundary
outputs and remain gated by `KATANA_TRADING_MODE`.

Crypto research adds a control layer before any result can be promoted:

```text
PIT data -> factors/signals -> ledger backtest -> control_report -> session_package
```

A-share research follows the same control shape but keeps its morning package:

```text
PIT data -> factors/signals -> ledger backtest -> control_report -> morning_package
```

The control report is the local feedback loop: it compares observed state
against invariants and emits `promote`, `observe`, `reject`, or `halt`.

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

The controlled agent entry is `backend/app/research/agent.py`. It is the local
boundary for RD-Agent/AgentQuant-style loops and may only produce
`FactorDefinition`, `Experiment`, and `ResearchSignal` objects. It must use
PIT-safe datasets and must not import trading adapters.

## Point-in-Time Rule

Research code should use `backend/app/research/pit.py` for historical data.
The A-share data rules are defined in
[A-Share Data And PIT Specification](ASHARE_DATA_PIT_SPEC.md).
Experiment reproducibility is defined in
[Experiment Manifest Specification](EXPERIMENT_MANIFEST_SPEC.md).

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

## Control Decisions

`promote` means research evidence is complete enough for human review. It does
not mean automatic trading.

`observe` means the run is mechanically valid but evidence is too weak, such as
short samples, no signals, or no completed trades.

`reject` means the strategy evidence failed a performance control, such as
drawdown above the configured limit.

`halt` means an invariant failed and the run is not trustworthy, such as future
data, duplicate events, missing manifest identity, or execution boundary drift.

A-share control reports also track morning-specific and market-specific state:
visible PIT rows, future rows rejected before snapshot/backtest, T+1 ledger
behavior, rejected orders from ST/suspension/limit constraints, position cap,
and the final `morning_package` decision.
