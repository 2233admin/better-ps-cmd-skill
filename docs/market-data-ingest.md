# Market Data Ingest

The first production A-share ingest path uses `tdx-cli` from TDXCLI-RS as a
bridge and writes a local PIT parquet lake.

```text
tdx-cli kline-range -> scripts/ingest-ashare-bridge.py
-> pit/kline_daily_pit.parquet
-> _manifest/universe_manifest.json
-> scripts/validate-ashare-lake.py
-> scripts/build-ashare-coverage.py
-> scripts/build-ashare-calendar.py
-> app.research.pipeline.world_snapshot
-> app.research.pipeline.cli / benchmark
-> scripts/run-ashare-control-plane.ps1
```

## Daily Kline PIT

`scripts/ingest-ashare-bridge.py` accepts canonical symbols such as
`600000.SH` and `000001.SZ`. It calls `tdx-cli kline-range`, normalizes bars to
`ashare.kline_daily_pit`, and writes:

```text
<lake-root>/pit/kline_daily_pit.parquet
<lake-root>/_manifest/universe_manifest.json
```

For daily bars, `available_at` is conservatively set to the next calendar day at
09:30 UTC. This is intentionally conservative and must be revisited when an
exchange calendar is available.

For full-market TDX scans, pass the existing `tdx-cli scan-kline --out-dir`
directory with `--scan-out-dir`. If that directory includes
`universe_manifest.json`, `scan_manifest.json`, `scan_summary.json`,
`summary.json`, or `manifest.json`, the ingest bridge preserves the scan
control totals as a first-class lake artifact:

```json
{
  "requested": {"count": 3, "symbols": ["000001.SZ", "300999.SZ", "600008.SH"]},
  "processed": {"count": 3, "symbols": ["000001.SZ", "300999.SZ", "600008.SH"]},
  "success": {"count": 1, "symbols": ["600008.SH"]},
  "empty": {"count": 1, "symbols": ["000001.SZ"]},
  "failure": {
    "count": 1,
    "symbols": ["300999.SZ"],
    "items": [{"symbol": "300999.SZ", "reason": "tdx scan failed"}]
  },
  "pit_symbols": {"count": 1, "symbols": ["600008.SH"]},
  "universe": {"count": 3, "symbols": ["000001.SZ", "300999.SZ", "600008.SH"]}
}
```

When scan metadata is missing, the bridge degrades explicitly: `pit_symbols`
becomes the requested/processed/success universe, and `empty`/`failure` are
empty lists. That is weaker evidence, but it is visible instead of smuggled
into a bare parquet row count.

Example:

```powershell
cd C:\Users\Administrator\projects\k-atana\backend
uv run python ..\scripts\ingest-ashare-bridge.py `
  --tdx-cli D:\projects\tdx\tdxcli-rs\target\debug\tdx-cli.exe `
  --symbols 600000.SH,000001.SZ `
  --start 2026-04-01 `
  --end 2026-05-16 `
  --out-root C:\tmp\katana-ashare-lake-tdxcli `
  --json
```

## Validation And Coverage

For the existing local lake, prefer the data-lake control entry point:

```powershell
cd C:\Users\Administrator\projects\k-atana
uv run --project backend python scripts\ashare-datactl.py build-local `
  --data-root DATA\Ashare `
  --date 2026-05-18
```

This runs the local-only closure path:

```text
ensure dirs -> export index PIT -> normalize existing TDX raw snapshots
-> build backtest feature view -> coverage -> factor readiness
```

It deliberately does not call TDX. Use `download-tdx-raw` explicitly when a new
raw snapshot is needed.

Validate the lake:

```powershell
uv run python ..\scripts\validate-ashare-lake.py `
  --root C:\tmp\katana-ashare-lake-tdxcli `
  --json
```

Build the manifest consumed by `--data-root`:

```powershell
uv run python ..\scripts\build-ashare-coverage.py `
  --root C:\tmp\katana-ashare-lake-tdxcli `
  --json
```

Coverage embeds the scan universe into
`<lake-root>/_manifest/coverage.json` as `scan_universe` plus flat
`requested_symbols`, `processed_symbols`, `success_symbols`, `empty_symbols`,
`failure_symbols`, and `pit_symbols` fields. The pipeline may still use
`symbols` as the current PIT scan universe; `requested_symbols` records what
TDX was asked to scan, including symbols that returned no bars or failed.

Run the A-share research pipeline:

```powershell
uv run python -m app.research.pipeline.cli `
  --date 2026-05-17 `
  --symbols 600000.SH,000001.SZ `
  --data-root C:\tmp\katana-ashare-lake-tdxcli `
  --out C:\tmp\katana-ashare-lake-tdxcli-run `
  --code-commit tdxcli-rs-ingest
```

Promotion requires `morning_package/control_report.json` to avoid `halt` and
`reject`. Fixture data must not promote.

## Incremental Lake

Daily operation should merge only the new batch, not rebuild a five-year lake.
Use the incremental merger after producing a PIT batch parquet or a
`tdx-cli scan-kline --out-dir` batch:

```powershell
uv run python ..\scripts\ingest-ashare-incremental.py `
  --out-root C:\tmp\katana-ashare-lake-tdxcli `
  --batch-parquet C:\tmp\latest-batch\kline_daily_pit.parquet `
  --start 2026-05-17 `
  --end 2026-05-17 `
  --json
```

Merge key is `symbol + event_time`. New rows replace existing rows with the
same key, then the lake is sorted by `symbol,event_time`. The script writes:

```text
<lake-root>/_manifest/incremental_batch.json
<lake-root>/_manifest/previous_content_hash
<lake-root>/_manifest/new_content_hash
```

## Ashare Control Plane

The control plane is the standard closure path:

```text
TDX/Lake -> validate -> coverage -> calendar -> world_snapshot
-> research pipeline -> backtest tables -> benchmark -> gate report
```

DuckDB is not the canonical storage layer, but it is part of the local query
path. The current A-share lake is hybrid under `<lake-root>/pit/`: daily kline
bars remain Parquet, while high-churn sidecars can live as Delta tables managed
by `delta-rs`. Local DuckDB files remain cache/tooling state until a separate
migration promotes them with PIT contracts.

If historical A-share bars still live in a local DuckDB file, export them into
the lake first:

```powershell
$env:KATANA_ASHARE_DATA_DIR = "C:\Users\Administrator\projects\k-atana\DATA\Ashare"
uv run python ..\scripts\export-ashare-duckdb-to-lake.py `
  --start 2026-04-01 `
  --end 2026-05-16
```

That export path is a migration bridge only. It does not make DuckDB the
control-plane fact source.

When the PIT lake is already accepted and the legacy DuckDB cache needs to
catch up for fallback tooling, sync the accepted PIT rows back into
`main.tdx_daily`:

```powershell
uv run python ..\scripts\sync-ashare-pit-to-duckdb.py `
  --start 2026-04-30 `
  --end 2026-05-18 `
  --json
```

This cache sync is idempotent: it deletes matching `symbol,date` rows in
DuckDB before inserting the PIT rows. It must run after PIT validation and
coverage, not before.

If `KATANA_ASHARE_DUCKDB_PATH` is unset, the exporter first looks for:

```text
<repo>/DATA/Ashare/Aquant.duckdb
<repo>/DATA/Ashare/ashare.duckdb
```

If `KATANA_ASHARE_LAKE_ROOT` is unset, the default lake root is:

```text
<repo>/DATA/Ashare
```

Run it from the repo root:

```powershell
scripts\run-ashare-control-plane.ps1 `
  -LakeRoot C:\tmp\katana-ashare-lake-tdxcli `
  -Date 2026-05-17 `
  -OutRoot C:\tmp\katana-ashare-control-runs `
  -Mode all
```

Outputs are placed under one run directory:

```text
control_plane_manifest.json
lake_validation.json
coverage.json
trading_calendar.json
world_snapshot/
pipeline/
benchmark.json
gate_report.json
```

`ashare.world_snapshot_v1` is built only from PIT-safe kline rows visible at
the requested date. It intentionally does not infer ST status, suspension,
adjustments, sectors, or index membership unless those PIT datasets exist.

Full-lake acceptance should check:

```text
lake_validation.json: passed=true and errors=[]
world_snapshot/manifest.json: dataset=ashare.world_snapshot_v1 and rows>0
benchmark.json: duration_ms, rows_per_sec, and symbols_per_sec are present
gate_report.json: decision is not halt
```

The research pipeline now writes run-level parquet tables by default:

```text
backtest_summary.parquet
backtest_orders.parquet
backtest_trades.parquet
backtest_equity_curve.parquet
scan_results.parquet
```

Per-symbol backtest directories are only written with
`--artifact-level full` or `-FullArtifacts`.

## xtdata / MiniQMT Ingest

xtdata is a supplemental ingress path, not a research data source. The order is:

```text
xtdata download -> xtdata extract -> xtdata normalize -> xtdata promote -> PIT Lake
```

Daily bars are the only promoted period in this phase. The supported xtdata
adjustment labels are `none`, `front`, `back`, `front_ratio`, and `back_ratio`.
The promoted daily dataset remains:

```text
DATA/Ashare/pit/kline_daily_pit.parquet
```

Example:

```powershell
cd C:\Users\Administrator\projects\k-atana\backend
uv run python ..\scripts\ingest-ashare-xtdata.py download `
  --symbols 600000.SH,000001.SZ `
  --start 20260401 `
  --end 20260430 `
  --period 1d `
  --adjust none

uv run python ..\scripts\ingest-ashare-xtdata.py extract `
  --symbols 600000.SH,000001.SZ `
  --start 20260401 `
  --end 20260430 `
  --out-parquet ..\DATA\Ashare\normalized\xtdata\kline_daily_batch.parquet

uv run python ..\scripts\ingest-ashare-xtdata.py promote `
  --batch-parquet ..\DATA\Ashare\normalized\xtdata\kline_daily_batch.parquet `
  --out-root ..\DATA\Ashare
```

Tick and minute periods are intentionally blocked from this daily path. When
they are added, they must land in `tick_trade_pit` and `kline_minute_pit`
contracts first.

## TDX Snapshot Evidence

TDX finance and block files are useful evidence, but they are current snapshots,
not historical PIT streams. Keep them out of historical backtests until an
announcement/effective-date normalizer promotes them with explicit visibility
windows.

Download raw snapshot evidence:

```powershell
cd C:\Users\Administrator\projects\k-atana\backend
uv run python ..\scripts\download-ashare-tdx-raw.py `
  --data-root ..\DATA\Ashare `
  --run-id 20260518 `
  --symbol-scope all `
  --json
```

Normalize the raw JSON files into queryable parquet:

```powershell
uv run python ..\scripts\normalize-ashare-tdx-snapshots.py `
  --data-root ..\DATA\Ashare `
  --run-id 20260518 `
  --json
```

Outputs:

```text
DATA/Ashare/normalized/tdx/finance_snapshot_20260518.parquet
DATA/Ashare/normalized/tdx/block_membership_20260518.parquet
DATA/Ashare/_manifest/tdx_finance_snapshot_20260518.json
DATA/Ashare/_manifest/tdx_block_membership_20260518.json
DATA/Ashare/_manifest/tdx_normalized_20260518.json
```

Both normalized datasets set `research_eligible=false` in their manifests.

## Backtest Feature View

For routine backtests, materialize one PIT-safe feature view instead of making
each run re-join daily bars, tradability status, and adjustment factors:

```powershell
cd C:\Users\Administrator\projects\k-atana\backend
uv run python ..\scripts\build-ashare-backtest-view.py `
  --data-root ..\DATA\Ashare `
  --json
```

Current output:

```text
DATA/Ashare/features/backtest_daily_pit_20260518.parquet
DATA/Ashare/_manifest/backtest_daily_pit_20260518.json
```

Use it directly as pipeline input:

```powershell
uv run python -m app.research.pipeline.cli `
  --date 2026-05-18 `
  --pit-parquet ..\DATA\Ashare\features\backtest_daily_pit_20260518.parquet `
  --symbols 600000.SH,000001.SZ `
  --out ..\DATA\Ashare\experiments\pipeline_20260518_backtest_view_smoke `
  --code-commit backtest-view
```

The view keeps `available_at` and `source_updated_at`, so the pipeline still
filters rows by the package date. It includes raw execution prices and
`adjusted_close` for factor calculation. It also includes price-derived
`limit_up_proxy` and `limit_down_proxy` columns, used only when no visible PIT
status row is available. TDX finance/block snapshot parquets are not joined
because they are not historical PIT datasets.

## Factor Data Readiness

Check which factor granularities are safe for historical testing:

```powershell
uv run python ..\scripts\check-ashare-factor-data-readiness.py `
  --data-root ..\DATA\Ashare `
  --json
```

Current readiness after syncing ChinaData status plus factor sidecars is:

```text
price factors: ready
benchmark indices: ready
limit up/down: ready
historical tradability: ready
market cap/free float: ready
industry/block membership: ready
financial statements: missing PIT
```

The report is written to:

```text
DATA/Ashare/_manifest/factor_data_readiness.json
```

## ChinaData Factor Sidecars

The paid ChinaData/Tushare path is used to fill the daily fields that TDX does
not carry well enough for factor research. The current sidecar mapping is:

```text
daily_basic -> pit/market_cap_daily_pit/
bak_daily -> pit/industry_daily_pit/
share_float -> pit/share_float_event_pit/
```

Sync them explicitly:

```powershell
cd C:\Users\Administrator\projects\k-atana
uv run --project backend python scripts\ashare-datactl.py sync-chinadata-factors `
  --data-root DATA\Ashare `
  --start 2016-01-01 `
  --end 2026-05-18 `
  --json
```

Current real output on the local lake:

```text
market_cap_daily_pit: 750,000 rows, 5,755 symbols, 2016-01-27 -> 2026-05-18
industry_daily_pit: 756,000 rows, 5,766 symbols, 2017-06-28 -> 2026-05-18
share_float_event_pit: 58,765 rows, 171 symbols, 2016-12-28 -> 2026-05-18
```

`build-ashare-backtest-view.py` now joins visible `market_cap_daily_pit` and
`industry_daily_pit` rows from either Delta or Parquet into
`backtest_daily_pit_YYYYMMDD.parquet`, so the default factor-backtest entry
surface already carries:

```text
turnover_rate, turnover_rate_f, volume_ratio
pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm
total_share, float_share, free_share, total_mv, circ_mv
name, industry, area
```

`share_float_event_pit` stays separate because it is an event stream, not a
daily snapshot. Flattening it into the daily view would blur announcement-time
semantics.

## Index Benchmarks

Benchmark indices are exported from the legacy DuckDB cache into PIT parquet:

```powershell
uv run python ..\scripts\export-ashare-index-duckdb-to-lake.py `
  --db-path ..\DATA\Ashare\Aquant.duckdb `
  --out-root ..\DATA\Ashare `
  --json
```

Current output:

```text
DATA/Ashare/pit/index_daily_pit.parquet
DATA/Ashare/_manifest/index_daily_pit.json
```

Included symbols: `000001.SH`, `399001.SZ`, `399006.SZ`, `000300.SH`,
`000905.SH`, and `000852.SH`.
