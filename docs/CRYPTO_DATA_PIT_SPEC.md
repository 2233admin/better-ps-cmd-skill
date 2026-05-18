# Crypto PIT Data Spec

Crypto research data must follow:

```text
source/raw -> normalized -> point-in-time
```

The crypto chain is separate from A-share. It has no T+1, no exchange price
limits, and no morning-only decision window. Spot and swap data must not be
mixed without an explicit `market_type`.

## Required Time Columns

Every research-eligible dataset needs:

- `event_time`: exchange event timestamp.
- `available_at`: earliest timestamp the row could be used by research.
- `source_updated_at`: source ingest/update timestamp.

## Kline Dataset

`crypto.kline_pit` requires:

- `inst_id`
- `venue`
- `market_type`
- `event_time`
- `available_at`
- `source_updated_at`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `quote_volume`

## Swap Datasets

Swap research additionally needs point-in-time funding and reference prices:

- `crypto.funding_rate_pit`
- `crypto.open_interest_pit`
- `crypto.mark_price_pit`
- `crypto.index_price_pit`

Funding, open interest, mark price, and index price are not optional evidence
for leveraged perpetual strategies. A swap backtest without funding is only a
smoke test, not strategy evidence.

## Funding/Basis Research Path

The first crypto alpha path is funding and basis capture, not grid bots or
generic momentum. Research consumes normalized PIT rows and emits trade
intents. The initial factor family is:

- funding annualized level
- mark-index basis
- perp-spot basis
- open-interest change
- volume/liquidity filter
- volatility regime

Research artifacts must include `factor_snapshot.json`, `trade_intents.json`,
portfolio `orders/fills/positions/equity_curve` tables, and
`paper_reconciliation.json`.

## Control Report

Every crypto session package must include `control_report.json` and
`control_report.md`. The report records:

- objective and configured control limits
- observed state: PIT row counts, rejected future rows, factor values, signals,
  backtests, and completed trades
- error terms: drawdown, sample shortfall, reject reasons, observe reasons
- invariants: PIT visibility, event uniqueness, spot no-short, swap short gate,
  and manifest binding
- decision: `promote`, `observe`, `reject`, or `halt`
- evidence level: `fixture_smoke`, `pit_backtest`, or `strategy_evidence`

## Producer Chain (XAR-426, 2026-05-18)

The crypto PIT producer chain is:

```
OKXCCXTAdapter (ccxt + python-okx)
  -> okx_pit_writer.py (PIT normalization + invariant validation)
    -> scripts/ingest-crypto-funding.py      (funding_rate_pit)
    -> scripts/ingest-crypto-open-interest.py (open_interest_pit)
    -> scripts/ingest-crypto-mark-price.py   (mark_price_pit)
```

### Adapter feature flag

`KATANA_OKX_ADAPTER` env var controls which client the shim
(`backend/app/data/okx_client.py`) routes to:

- `ccxt` (default): `OKXCCXTAdapter` backed by ccxt 4.5.54 + python-okx 0.4.1
- `legacy`: original hand-rolled `OKXClient` (229 lines, kept for one-week parallel run)

### Ingest scripts

All three scripts accept `--out-root <lake-root>` and write hive-partitioned parquet:

```
<lake-root>/crypto/<dataset>/venue=okx/market_type=swap/inst_id=<SYM>/<DATE>.parquet
```

Datasets:
- `funding_rate_pit`: funding rate history, paginated via `before`/`after` cursor
  (OKX max 100 rows/call at 8h interval, ~100 rows/month)
- `open_interest_pit`: historical OI via `TradingData.get_open_interest_history()`
  (raw format `[ts_ms, oi_contracts, oi_base_ccy, oi_usd]`, confirmed live 2026-05-18)
- `mark_price_pit`: snapshot poll; run on schedule (e.g. every 8h) for continuous coverage

### Open gaps (post-XAR-426)

- Auth-endpoint smoke (get_balance, place_order) — sandbox keys now in vault
  (`KATANA_OKX_SANDBOX_{KEY,SECRET,PASSPHRASE}`, comrade-cortex-secrets 86232dd).
  Live-smoke tracked in XAR-430.
- `mark_price_pit` has no historical backfill endpoint on OKX public API; snapshot-only.
- `okx_client.py` legacy to be deleted after one week of clean parallel ccxt-mode runs.

## Execution Boundary

Research and backtest may produce crypto candidates. Real OKX orders remain
behind `KATANA_TRADING_MODE=live_test`,
`KATANA_ENABLE_CRYPTO_LIVE_TEST=1`, `KATANA_OKX_MAX_ORDER_USDT`, and
`KATANA_OKX_ALLOWED_PAIRS`.

Paper reconciliation must pass before a candidate is eligible for live_test:
intent -> accepted/rejected -> fill -> cash/position. Reconciliation failures
stay in `paper` and must not call an exchange client.
