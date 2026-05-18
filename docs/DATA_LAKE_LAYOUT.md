# Multi-Asset Data Lake Layout

`DATA/` is split by asset domain. Shared governance indexes live once at the
top level; research datasets and manifests stay inside each asset domain.

```text
DATA/
  _registry/
    data_sources.json
    dataset_versions.json
  Ashare/
    Aquant.duckdb
    raw/
      xtdata/
    normalized/
      xtdata/
      tdx/
    pit/
      kline_daily_pit.parquet
      index_daily_pit.parquet
      market_cap_daily_pit/
      industry_daily_pit/
      share_float_event_pit/
      tradability_status_pit/
      adjustment_factor_pit.parquet
      corporate_action_pit.parquet
      kline_minute_pit.parquet
      tick_trade_pit.parquet
    features/
      backtest_daily_pit_YYYYMMDD.parquet
    experiments/
    _manifest/
  crypto/
    raw/
    normalized/
    pit/
    features/
    experiments/
    _manifest/
```

## Ownership

- `DATA/_registry`: cross-asset source and dataset-version index.
- `DATA/Ashare`: A-share raw files, staging caches, PIT lake, features, and
  experiment artifacts.
- `DATA/crypto`: crypto raw files, staging caches, PIT lake, features, and
  experiment artifacts.

DuckDB files are local cache/staging stores. Research-grade inputs are PIT
datasets under each asset domain's `pit/` directory. The current A-share lake is
hybrid: core price bars remain Parquet, while high-churn sidecars move to Delta
tables managed by `delta-rs`.

## A-Share Source Priority

Research readers must use A-share sources in this order:

```text
PIT Lake > DuckDB cache > xtdata/QMT ingest > TDXCLIrs fallback
```

`DATA/Ashare/pit/kline_daily_pit.parquet` is the default price-bar fact source
for daily A-share research. DuckDB is the local SQL/query layer over both
Parquet and Delta sidecars. xtdata/MiniQMT is an ingest adapter: it may
download and extract local QMT cache data, but it must normalize and promote
rows into PIT storage before any research or backtest code can read them.

Current xtdata scope is daily bars only:

```text
xtdata 1d -> DATA/Ashare/normalized/xtdata/ -> DATA/Ashare/pit/kline_daily_pit.parquet
```

`tick`, `1m`, and `5m` are reserved for future PIT datasets:

```text
DATA/Ashare/pit/tick_trade_pit.parquet
DATA/Ashare/pit/kline_minute_pit.parquet
```

TDX finance and block snapshots land in `DATA/Ashare/raw/tdx_*` first, then
normalize into `DATA/Ashare/normalized/tdx/`. They remain snapshot evidence
with `research_eligible=false` until historical visibility windows exist.

Routine A-share backtests can read a prejoined PIT-safe feature view from
`DATA/Ashare/features/backtest_daily_pit_YYYYMMDD.parquet`. That view is derived
from PIT daily bars, tradability status, adjustment factors, market-cap sidecars,
and industry sidecars; it intentionally excludes non-PIT finance and block
snapshots, and it keeps `share_float_event_pit` as a separate event layer.

Factor-readiness reports live at `DATA/Ashare/_manifest/factor_data_readiness.json`.
They record which granularities are ready, proxy-only, partial, or blocked until
PIT metadata exists.

Source-gap reports live at `DATA/Ashare/_manifest/source_gap_report.json`.
They explain which datasets TDXCLI already covers, which ones must come from
ChinaData/Tushare, and which pulls should stay incremental instead of replaying
the full history.

## Dataset IDs

Use asset-qualified IDs:

```text
ashare.kline_daily_pit
ashare.kline_minute_pit
ashare.tick_trade_pit
ashare.tradability_status_pit
ashare.adjustment_factor_pit
crypto.kline_pit
crypto.funding_rate_pit
crypto.open_interest_pit
crypto.mark_price_pit
crypto.index_price_pit
```

## Initialization

```powershell
cd C:\Users\Administrator\projects\k-atana
uv run python scripts\init-data-lake-layout.py --data-root DATA --assets ashare,crypto --json
```

The initializer only creates missing directories and missing JSON stubs. It does
not overwrite existing manifests or data.

## A-Share Local Build

Use `scripts/ashare-datactl.py` as the repeatable A-share lake entry point for
local, non-network rebuilds:

```powershell
cd C:\Users\Administrator\projects\k-atana
uv run --project backend python scripts\ashare-datactl.py build-local `
  --data-root DATA\Ashare `
  --date 2026-05-18
```

`build-local` creates missing lake directories, exports benchmark index PIT rows
from the DuckDB cache when present, normalizes existing raw TDX snapshots,
builds the backtest-ready daily feature view, refreshes coverage, and refreshes
factor-readiness. It does not download TDX data.

Raw TDX refresh remains explicit:

```powershell
uv run --project backend python scripts\ashare-datactl.py download-tdx-raw `
  --data-root DATA\Ashare `
  --run-id 20260518 `
  --symbol-scope all
```

ChinaData factor sidecars are also explicit:

```powershell
uv run --project backend python scripts\ashare-datactl.py sync-chinadata-factors `
  --data-root DATA\Ashare `
  --start 2016-01-01 `
  --end 2026-05-18
```

This writes:

```text
DATA/Ashare/pit/market_cap_daily_pit/
DATA/Ashare/pit/industry_daily_pit/
DATA/Ashare/pit/share_float_event_pit/
DATA/Ashare/_manifest/chinadata_factor_sidecars.json
```

Dry-run the orchestration before changing files:

```powershell
uv run --project backend python scripts\ashare-datactl.py build-local `
  --data-root DATA\Ashare `
  --date 2026-05-18 `
  --dry-run `
  --json
```
