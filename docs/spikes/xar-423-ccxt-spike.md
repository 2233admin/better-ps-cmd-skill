# XAR-423 Spike: ccxt + python-okx replace okx_client.py

Date: 2026-05-18
Author: claude-sonnet-4-6 (agent)
Status: COMPLETE — spike proves drop-in replacement viable

## Objective

Validate that ccxt 4.5.54 + python-okx 0.4.1 can replace the hand-rolled
`okx_client.py` (229 lines) and additionally fill the crypto funding/OI/mark-price
PIT gap identified in CRYPTO_DATA_PIT_SPEC.md.

## Gate 0: Dependency installation

```
uv add ccxt python-okx
```

- ccxt==4.5.54 installed (6.3 MiB)
- python-okx==0.4.1 installed
- torch==2.12.0+cu132 verified intact after install (`torch.cuda.is_available() == True`)
- No CUDA regression (feedback rule 5 compliant)

## Gate 1: Raw API probe results

### ccxt.okx().fetch_ticker('BTC/USDT:USDT')

Keys: `symbol, timestamp, datetime, high, low, bid, bidVolume, ask, askVolume, vwap,
open, close, last, previousClose, change, percentage, average, baseVolume, quoteVolume,
markPrice, indexPrice, info`

Values sampled: `last=76996.0, bid=76996.0, ask=76996.1, high=78571.4, low=76633.3,
baseVolume=6210666.13`

**Schema diff vs okx_client.get_ticker:**
- okx_client returns raw OKX dict: `instId, instType, last, lastSz, askPx, bidPx, high24h, low24h, vol24h, sodUtc8, ts, ...`
- ccxt returns unified dict; raw OKX fields are in `info` sub-dict
- Adapter `_normalize_ticker()` reconstructs the OKX-shaped dict from both unified + info fields
- All fields used by `crypto_market_data.py` (`last, askPx, bidPx, high24h, low24h, volCcy24h, sodUtc8`) are present in `info`

### ccxt.okx().fetch_ohlcv('BTC/USDT:USDT', '1m', limit=3)

Returns `[timestamp_ms, open, high, low, close, volume]` per row. Same 6-column shape
as okx_client.get_kline (which returns raw OKX data that is also 6+ columns). Adapter
emits str-lists matching OKX convention.

### ccxt.okx().fetch_order_book('BTC/USDT:USDT', 5)

Returns `{bids: [[price, amount, count], ...], asks: [...]}`. okx_client returns
`{bids: [["price","qty","deprecated","orderCount"], ...]}`. Adapter normalises to
string lists matching OKX format.

### ccxt.okx().fetch_funding_rate_history('BTC/USDT:USDT', limit=3)

Keys: `info, symbol, fundingRate, timestamp, datetime`
Missing: `realizedRate, formulaType, method` — these are OKX-specific fields NOT in
ccxt's unified schema. **This is why python-okx is used for the PIT writer instead.**

### python-okx PublicData.funding_rate_history('BTC-USDT-SWAP', limit=3)

Keys: `formulaType, fundingRate, fundingTime, instId, instType, method, realizedRate`
All OKX-native fields present including `realizedRate` needed for accurate PIT data.

### python-okx PublicData.get_open_interest(instType='SWAP', instId='BTC-USDT-SWAP')

Keys: `instId, instType, oi, oiCcy, oiUsd, ts`
All fields needed for `crypto.open_interest_pit`.

### python-okx PublicData.get_mark_price(instType='SWAP', instId='BTC-USDT-SWAP')

Keys: `instId, instType, markPx, ts`
Sufficient for `crypto.mark_price_pit`.

## Gate 2: Adapter implementation

**New file:** `backend/app/markets/crypto/okx_ccxt_adapter.py`

- `OKXCCXTAdapter` class — same public surface as `OKXClient`:
  - `get_tickers(inst_type)`, `get_ticker(inst_id)`, `get_orderbook(inst_id, depth)`
  - `get_kline(inst_id, bar, limit)`, `get_trades(inst_id, limit)`
  - `get_balance()`, `get_positions()`
  - `place_order(..., katana_gate_token)` — gate token contract **preserved identically**
  - `cancel_order(inst_id, order_id)`, `get_orders(inst_type)`, `get_order_history(...)`
- Additional PIT depth methods (not in OKXClient):
  - `get_funding_rate(inst_id)` — current rate via python-okx
  - `get_funding_rate_history(inst_id, before, after, limit)` — via python-okx
  - `get_open_interest(inst_id)` — snapshot via python-okx
  - `get_mark_price(inst_id)` — snapshot via python-okx
- `get_okx_ccxt_adapter()` singleton matches `get_okx_client()` pattern

**Symbol translation:** OKX `BTC-USDT-SWAP` → ccxt `BTC/USDT:USDT`, handled by
`_to_ccxt_symbol()`. Bar string translation: `1H` → `1h`, etc., handled by `_to_okx_bar()`.

## Gate 3: PIT parquet writer

**New file:** `backend/app/markets/crypto/okx_pit_writer.py`

Follows CRYPTO_DATA_PIT_SPEC.md exactly:
- Required columns: `event_time, available_at, source_updated_at` (Datetime[us, UTC])
- Venue: `"okx"`, market_type: `"swap"`

Datasets implemented:
- `crypto.funding_rate_pit`: `backfill_funding_rate(inst_id, out_dir, limit, adapter)`
  - `available_at == event_time` (funding realizedRate known at settlement)
- `crypto.open_interest_pit`: `backfill_open_interest(inst_id, out_dir, adapter)`
  - `available_at = max(event_ts, ingest_ts)` — clock-skew guard for snapshot polling
- `crypto.mark_price_pit`: `backfill_mark_price(inst_id, out_dir, adapter)`
  - Same clock-skew guard

`validate_pit_invariants()` enforces:
1. required columns present
2. `available_at >= event_time` (no future knowledge)
3. no nulls in time columns
4. uniqueness on `(inst_id, event_time)` for funding

## Gate 4: Live smoke test results (BTC-USDT-SWAP, 2026-05-18)

```
backfill_funding_rate  -> (10, 10) rows, Polars Datetime[us,UTC] dtypes confirmed
backfill_open_interest -> (1, 9)   oi_usd=2686875999.34
backfill_mark_price    -> (1, 7)   mark_price=77037.5
```

Three parquet files written to temp dir, all PIT invariants passed.

One issue caught during live smoke: OKX exchange timestamp on OI/mark snapshot can be
slightly ahead of local `ingest_ts` due to clock skew. Fixed with
`available_at = max(event_ts, ingest_ts)`. PIT contract preserved.

## Gate 5: OKXBridge compatibility

`OKXClient` and `OKXBridge` imports unmodified — the adapter is additive only.
`OKXBridge._live_test_rejection()` gate still enforces `KATANA_TRADING_MODE=live_test`.
No regression to existing trading path.

## Test coverage

**New test file:** `backend/tests/unit/test_okx_ccxt_adapter.py`

27 tests, all passing (0.49s, no network calls):
- Symbol/bar translation (7 tests)
- Ticker normalization shape + error handling (3 tests)
- Orderbook shape + error handling (2 tests)
- Kline shape + bar mapping (2 tests)
- PIT invariant validator (3 tests)
- backfill_funding_rate: schema, dtypes, venue, sort, available_at semantics (5 tests)
- backfill_open_interest: parquet write, available_at >= event_time (2 tests)
- backfill_mark_price: parquet write + value (1 test)
- Gate token contract: wrong token rejected, missing creds rejected (2 tests)

## Schema diff summary

| Method | okx_client shape | ccxt adapter shape | Delta |
|--------|------------------|--------------------|-------|
| get_ticker | OKX raw dict (instId, last, askPx...) | same keys reconstructed | identical |
| get_orderbook | {bids: [[str,str,str,str]], asks:...} | same format | identical |
| get_kline | [[str, str, str, str, str, str]] | same | identical |
| get_balance | OKX raw data list | OKX raw data list (via info) | identical |
| place_order | {ordId, clOrdId, sCode} or {error} | same | identical |
| get_funding_rate_history | (not in OKXClient) | python-okx native fields | new |
| get_open_interest | (not in OKXClient) | python-okx native fields | new |
| get_mark_price | (not in OKXClient) | python-okx native fields | new |

## Production rollout path (NOT done in this spike)

1. In `backend/app/data/okx_client.py` (3-line shim), change the star import target
2. In `backend/app/trading/adapters/okx/bridge.py`, no changes needed (OKXBridge uses
   OKXClient via `get_okx_client()` which the shim re-exports)
3. Delete `backend/app/markets/crypto/okx_client.py` (229 lines)
4. Wire `okx_pit_writer.backfill_*` into the ingest pipeline for daily crypto PIT builds

**Estimated rollout effort:** 0.5 day (one session)

## Blockers / honest gaps

- **Auth-required endpoints not smoke-tested** (get_balance, place_order, cancel_order,
  get_order_history): no live OKX API credentials available. The gate token contract is
  verified at the logic level (unit tests), not against a live exchange. A sandbox key
  pair would unblock this.
- **OI/mark-price history not backfilled**: python-okx `get_open_interest()` returns
  only the current snapshot. Historical OI requires a different endpoint
  (`/api/v5/rubik/stat/open-interest-volume`) which python-okx exposes as
  `get_open_interest_volume()` — this was not wired up in the spike.
- **ccxt fetch_open_interest_history**: `openInterestAmount` field returns `None` for
  OKX in ccxt 4.5.54 (quoteVolume works but baseVolume is null). python-okx raw API
  is the correct path for historical OI data; spike uses current snapshot only.
- **1-month backfill not run**: the funding rate OKX endpoint returns max 100 rows
  per call (8-hour interval = ~100 rows/month). Pagination loop needed for full 1-month
  fill. Skeleton is there; production ingest should add `before`/`after` cursor loop.
