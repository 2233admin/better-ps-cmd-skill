# Market Data Ingest

The first production A-share ingest path uses `tdx-cli` from TDXCLI-RS as a
bridge and writes a local PIT parquet lake.

```text
tdx-cli kline-range -> scripts/ingest-ashare-bridge.py
-> pit/kline_daily_pit.parquet
-> _manifest/universe_manifest.json
-> scripts/validate-ashare-lake.py
-> scripts/build-ashare-coverage.py
-> backend app.research.pipeline --data-root
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
