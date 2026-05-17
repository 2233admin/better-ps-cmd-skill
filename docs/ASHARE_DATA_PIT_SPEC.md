# A-Share Data And PIT Specification

This document is the boundary for A-share data work in `k-atana`.

The target is not "more data". The target is data that can survive research
audit. Any agent or human change that bypasses these rules is out of bounds.

## Pipeline

```text
source/raw -> normalized -> point-in-time -> research feature view -> experiment
```

Only point-in-time datasets may feed factors, HMC state models, signals, or
backtests.

Research experiments must reference immutable dataset versions, not mutable
table names. See [Experiment Manifest Specification](EXPERIMENT_MANIFEST_SPEC.md).

## Dataset Tiers

`raw`

- Direct dump from AkShare, pytdx, Tushare, Eastmoney, Sina, QMT exports, or files.
- May contain vendor field names, duplicates, revised values, or missing release times.
- Cannot be used by research code.

`normalized`

- Canonical symbol, market, timestamp, and numeric fields.
- Deduplicated and type-checked.
- Still cannot be used by research code unless visibility metadata exists.

`pit`

- Has explicit visibility semantics.
- Can answer: "what did the strategy know at `as_of`?"
- May be used by factors, HMC, signals, and backtests.

## Mandatory Time Columns

Every production research dataset must define these concepts:

- `event_time`: when the market/fundamental/policy event happened.
- `available_at`: when this row became visible to the research system.
- `source_updated_at`: when the vendor/source record was fetched or updated.

For pure price bars, `event_time` is the bar date/time. `available_at` may be
derived conservatively:

- daily bar: next exchange session open, unless the source proves earlier safe availability
- minute bar: bar close time
- tick: tick receive time

For fundamentals, index membership, ST status, suspensions, corporate actions,
policy, and news, `available_at` must come from an explicit release/effective
calendar. If not available, the dataset is not PIT-safe.

## Canonical Identity

Symbols must be normalized before research use:

```text
600000.SH
000001.SZ
110000.SH
```

Market codes used by pytdx are implementation details. They may exist in
storage, but research contracts should use canonical symbols.

## Minimum PIT Schemas

`ashare.kline_daily_pit`

```text
symbol
market
event_time
available_at
source_updated_at
open
high
low
close
volume
amount
```

`ashare.kline_minute_pit`

```text
symbol
market
event_time
available_at
source_updated_at
open
high
low
close
volume
amount
```

`ashare.instrument_status_pit`

```text
symbol
event_time
available_at
source_updated_at
is_trading
is_st
is_suspended
limit_up
limit_down
```

`ashare.adjustment_factor_pit`

```text
symbol
event_time
available_at
source_updated_at
adjustment_type
factor
```

## Blocked Until PIT Metadata Exists

These datasets must not feed production experiments until they have explicit
`available_at` semantics:

- financial statements
- fundamentals and valuation ratios
- index constituents
- industry classification
- ST status
- suspension status
- limit-up/limit-down state
- corporate actions and adjustment factors
- policy/news/macro release calendars
- northbound flow if release timestamp is missing

## Query Rule

A point-in-time query must apply:

```sql
event_time <= requested_end
available_at <= as_of
```

For current legacy `kline_daily` and `kline_minute`, `PointInTimeStore` provides
a conservative transitional facade by clipping the requested end to `as_of`.
That is accepted only for price bars. It is not accepted for fundamentals,
membership, status, or corporate-action datasets.

The A-share pipeline must apply the visibility rule before snapshotting, factor
calculation, backtesting, and morning-package generation. Future rows rejected
by `available_at > as_of` are counted in `morning_package/control_report.json`;
they are not part of the run-local `visible_kline_daily_pit.parquet` evidence
snapshot.

## Factor Rule

Every factor value must carry:

- `factor`
- `symbol`
- `timestamp`
- `as_of`
- `value`

`as_of` must be greater than or equal to `timestamp`, and the input data used to
compute the factor must satisfy `available_at <= as_of`.

## HMC Rule

HMC/materialist dynamics consumes only PIT feature windows and emits only
research state:

```text
PIT window -> HMCStateEstimate -> ResearchSignal/report
```

HMC code must not:

- fetch hidden data
- import trading adapters
- submit orders
- read QMT/OKX account state

## Backtest Rule

A-share backtests must eventually model:

- T+1 sellability
- limit-up/limit-down execution constraints
- suspension/non-trading days
- ST status
- fees, stamp tax, slippage
- corporate-action adjusted and raw prices

Until those constraints are modeled, results must be labeled research-only.
