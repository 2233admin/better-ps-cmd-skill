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

DuckDB is not part of this acceptance path. The canonical A-share research
input is the PIT parquet lake under `<lake-root>/pit/`; local DuckDB files remain
legacy/tooling cache until a separate migration promotes them with PIT contracts.

If historical A-share bars still live in a local DuckDB file, export them into
the lake first:

```powershell
$env:KATANA_ASHARE_DATA_DIR = "C:\Users\Administrator\projects\k-atana\DATA\ashare"
uv run python ..\scripts\export-ashare-duckdb-to-lake.py `
  --start 2026-04-01 `
  --end 2026-05-16
```

That export path is a migration bridge only. It does not make DuckDB the
control-plane fact source.

If `KATANA_ASHARE_DUCKDB_PATH` is unset, the exporter first looks for:

```text
<repo>/DATA/ashare/Aquant.duckdb
<repo>/DATA/ashare/ashare.duckdb
```

If `KATANA_ASHARE_LAKE_ROOT` is unset, the default lake root is:

```text
<repo>/DATA/ashare/lake
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
