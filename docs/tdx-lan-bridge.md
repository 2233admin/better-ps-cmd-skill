# TDX LAN Bridge

The current bridge is `tdx-cli` from TDXCLI-RS:

```text
D:\projects\tdx\tdxcli-rs\target\debug\tdx-cli.exe
```

`k-atana` does not call TDX from research code. TDX access stays behind ingest
scripts. The research pipeline consumes only PIT parquet plus a coverage
manifest.

## Current Command Surface

The minimum command used by `k-atana` is:

```powershell
tdx-cli.exe kline-range 600000 --start 2026-04-01 --end 2026-05-16 --json
```

The ingest script maps canonical symbols:

```text
600000.SH -> tdx-cli code 600000, market SH
000001.SZ -> tdx-cli code 000001, market SZ
```

## Boundary

TDX data is treated as vendor/source data until normalized into:

```text
ashare.kline_daily_pit
```

Research code must not:

- shell out to `tdx-cli`
- read TDX local files directly
- use TDX data without `event_time`, `available_at`, and `source_updated_at`

## Next Bridge Work

Future bridge hardening should add:

- exchange calendar based `available_at`
- retry and timeout policy
- structured command logs
- multi-symbol batching through `scan-kline`
- coverage by symbol and date range
