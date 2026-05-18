# XAR-426 — Crypto OKX Adapter Rollout

Date: 2026-05-18
Status: COMPLETE (live-test still gated, see XAR-430)
Predecessor: XAR-423 spike (commit a8c18b8, see `docs/spikes/xar-423-ccxt-spike.md`)

## Goal

Move crypto path off hand-rolled `okx_client.py` (229 lines) onto
`ccxt 4.5.54 + python-okx 0.4.1`. Add three PIT producers (funding /
open-interest / mark-price). Keep the gate-token contract for live trading
identical. Allow one week of parallel ccxt-vs-legacy operation before deletion.

## Wave-by-wave

| Step | Commit | What |
|---|---|---|
| 1. Feature flag | 97312a9 | `KATANA_OKX_ADAPTER=ccxt|legacy` env var routes the shim. Default `ccxt`. |
| 2. PIT ingest CLIs | dbf21ad | `scripts/ingest-crypto-{funding,open-interest,mark-price}.py` with hive-partitioned parquet, before/after pagination for funding, OI history via `TradingData.get_open_interest_history`, mark-price snapshot. |
| 3. Live-test gate | 130f87e | `OKXBridge._live_test_rejection` confirmed wired through ccxt adapter; gate-token contract unchanged. |
| 4. Spec update | (this commit) | `docs/CRYPTO_DATA_PIT_SPEC.md` §Producer Chain documents adapter + scripts + open gaps. |
| 5. Summary | (this commit) | This doc. |

## Producer chain

```
OKXCCXTAdapter (ccxt + python-okx)
  -> okx_pit_writer.py (PIT normalization + invariant validation)
    -> scripts/ingest-crypto-funding.py        (funding_rate_pit)
    -> scripts/ingest-crypto-open-interest.py  (open_interest_pit)
    -> scripts/ingest-crypto-mark-price.py     (mark_price_pit)
```

All three scripts emit to `<lake-root>/crypto/<dataset>/venue=okx/market_type=swap/inst_id=<SYM>/<DATE>.parquet`.

PIT invariants enforced at write:
1. required columns present (`event_time`, `available_at`, `source_updated_at`)
2. `available_at >= event_time` (no future knowledge — `available_at = max(event_ts, ingest_ts)` for snapshot polls to absorb clock skew)
3. no nulls in time columns
4. funding uniqueness on `(inst_id, event_time)`

## Verified

- `pytest backend/tests/unit/test_okx_ccxt_adapter.py` — 27 green (0.49s, no network)
- Live smoke (BTC-USDT-SWAP, 2026-05-18, from XAR-423):
  - funding 10 rows, PIT shapes confirmed
  - OI snapshot `oi_usd=2,686,875,999.34`
  - mark snapshot `mark_price=77037.5`
- Feature flag round-trip: setting `KATANA_OKX_ADAPTER=legacy` routes back to original `OKXClient` (regression escape hatch)

## What did NOT change

- `OKXBridge` contract — same gate-token, same `KATANA_TRADING_MODE=live_test` env gate
- Trading code paths — adapter is drop-in via shim, no callers modified
- Existing `OKXClient` — still in tree, will be deleted after parallel-run window

## Open gaps (handed off as Linear)

- **XAR-430**: OKX auth-endpoint live smoke (`get_balance`, `place_order`) using sandbox creds (`KATANA_OKX_SANDBOX_{KEY,SECRET,PASSPHRASE}` in vault 86232dd). Spike covered public endpoints only — auth path needs a real exchange handshake before the gate is meaningful.
- `mark_price_pit` has no historical backfill endpoint on OKX public API — snapshot poll only. Schedule a 5-30 min ingest cron for continuous coverage.
- Delete `backend/app/markets/crypto/okx_client.py` after 7-day clean run window (target 2026-05-25).

## Rollback path

```
$env:KATANA_OKX_ADAPTER = "legacy"   # PowerShell
export KATANA_OKX_ADAPTER=legacy     # bash
```

Restart processes. No code change required.
