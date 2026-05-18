# WQ Horror Factor -- IC Probe Blocked

**Status**: BLOCKED  
**Date**: 2026-05-18  
**Agent**: IC validation cheap probe (claude-sonnet-4-6)  
**Time spent on data check**: <30 min (mandatory gate per task spec)

---

## What was checked

### 1. Data lake location

`D:/projects/k-atana/DATA/quant.duckdb` is the only data file on disk (2.1 MB).

Parquet lake search result across all of `D:/projects/`:
- Zero `.parquet` files outside the backend `.venv` directory
- No `pit/kline_daily_pit.parquet` exists
- No `D:/data/ashare/` directory exists

### 2. DuckDB schema probe

```
Tables: bond_info, kline_daily, kline_minute, macro_allocation, macro_briefings,
        macro_indicators, macro_knowledge_log, tick_data, trades

kline_daily columns: code, market, date, open, high, low, close, volume, amount
kline_daily row count: 0
```

All tables are empty. The database schema exists but has never been populated.

### 3. Cap field availability

`cap` is **not in `PRICE_BAR_COLUMNS`** per `backend/app/research/ashare_data_contract.py`.

The canonical kline schema is: `symbol, market, event_time, available_at, source_updated_at,
open, high, low, close, volume, amount`.

No `total_share`, `circulating_cap`, `mktcap`, or `cap` column exists anywhere in the
data contract or the DuckDB schema. There is no proxy either (`total_share * close` cannot
be computed without `total_share`).

### 4. Data ingest tools available

- `akshare`: NOT installed in `backend/.venv`
- `tushare`: NOT installed in `backend/.venv`
- `tdx-cli` binary: not located (ingest scripts reference `--tdx-cli` flag pointing to external binary)
- `polars`, `numpy`, `duckdb`, `scipy`: installed and functional

### 5. Coverage check

Minimum requirement: >=200 symbols x >=2 years of daily data.  
Actual: 0 rows, 0 symbols, no date coverage.

---

## Blocker summary

| Requirement | Status | Detail |
|---|---|---|
| `cap` field (or proxy) | **MISSING** | Not in data contract, not in any table |
| daily OHLCV data | **MISSING** | DuckDB empty, no parquet lake |
| >=200 symbols | **MISSING** | 0 symbols ingested |
| >=2 years coverage | **MISSING** | 0 rows |

**All four requirements are unmet. The factor cannot be computed or validated.**

---

## What is needed to unblock

To proceed with this IC probe, the following must exist **before re-running**:

### Required dataset

A PIT parquet lake at a path passable as `--data-root` to `scripts/demo-run-pipeline.py`,
containing `pit/kline_daily_pit.parquet` with:

```
symbol          -- canonical (e.g. 600000.SH)
market          -- SH | SZ
event_time      -- bar date (UTC datetime)
available_at    -- next session open (UTC datetime, PIT-correct)
source_updated_at
open, high, low, close, volume, amount
```

**Plus** a market-cap field. Options in order of preference:

1. **`total_shares` column** in the kline table so `cap = total_shares * close` can be derived
   per day. Source: Wind, Tushare Pro, or QMT `GetSecurityInfo`.
2. **Separate `cap_pit.parquet`** with `(symbol, event_time, available_at, total_shares, float_shares)`.
   Source: AkShare `stock_zh_a_hist_min_em` or Tushare `daily_basic` (note: Tushare `daily_basic`
   has same-day availability, not next-day -- needs explicit PIT treatment).
3. **Static market cap approximation** (last-known `total_shares` from instrument master, joined on symbol).
   Acceptable only for cross-sectional ranking (cap decile grouping is stable intra-week); must be
   labeled as "static cap proxy" in the report.

### Minimum coverage

- At least 300 tradable A-share symbols (preferably CSI 500 or CSI 800 universe)
- At least 2 years of daily bars (2022-01-01 to 2024-12-31 is sufficient)
- `available_at` must be next trading-day open (the pipeline's `_conservative_available_at` logic
  is acceptable for price bars)

### Ingest path (already exists in repo)

```powershell
# Step 1: populate kline_daily via TDX or AkShare
python scripts/ingest-ashare-bridge.py --symbols <universe> --start 2022-01-01 --end 2024-12-31 --out-root D:/data/ashare/pit-lake/

# Step 2: add cap field (NEW -- needs to be built)
# Either extend ingest-ashare-bridge.py to join Tushare daily_basic,
# or build scripts/ingest-ashare-cap.py pulling AkShare stock_zh_a_spot_em

# Step 3: validate
python scripts/validate-ashare-lake.py --root D:/data/ashare/pit-lake/

# Step 4: re-run this probe
python scripts/validate-wq-horror-factor.py --data-root D:/data/ashare/pit-lake/
```

---

## What the factor requires that the current pipeline does NOT have

The WQ Horror factor uses `group_mean(x, rank(ts_mean(cap, 20)), market)` — cap-bucket-based
peer grouping. This is a **hard dependency on market cap**. Without cap:

- The size-neutral construction is impossible
- A fallback (e.g. group by amount quintile as a liquidity proxy) would test a **different factor**
  and should be labeled separately (not as WQ Horror validation)

The reversal signal itself (high-horror stocks underperforming) may exist without cap grouping,
but that would be a simplified version. If Curry wants to test the cap-agnostic reversal
signal as a separate probe, that can be done with current OHLCV data once the kline table
is populated. **Recommend creating a separate Linear ticket for that.**

---

## Recommendation

1. **This probe**: BLOCKED. Do not fabricate or proxy. Re-run after data ingest.
2. **Unblock path**: Ingest TDX/AkShare daily bars + add cap proxy (total_shares from instrument master).
   Estimated effort: 2-4 hours of data engineering.
3. **Optional parallel probe**: Test cap-agnostic intraday-reversal factor (horror without cap grouping)
   once OHLCV data is populated. File as separate Linear ticket (XAR-new).
4. **Data gap Linear ticket**: Create `XAR-new: Add market-cap field to kline PIT lake`
   referencing this document.
