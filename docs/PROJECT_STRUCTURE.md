# Project Structure

Katana is a multi-market quantitative research terminal with two active market
lines:

- `crypto`: OKX market data, factors, smoke analysis, paper trading, and gated live_test.
- `ashare`: A-share research, QMT/EasyXT boundary contracts, and the external Parquet lake.

Futures, GPU searches, and older macro/Streamlit tools are retained as legacy or
experimental material. They are not part of the default test gate.

## Active Paths

```text
backend/app/
  api/       HTTP API handlers, split by market where ownership is clear.
    crypto/    Crypto-specific routes.
    ashare/    A-share-specific routes.
    research/  Strategy and backtest routes.
    trading/   Paper and gated trading-test routes.
  data/      Shared data adapters and compatibility wrappers.
  markets/   Market-owned implementations.
  macro/     Macro research services still used by the app.
  strategy/  Backtest, factors, and signal logic.
  research/  Target home for factors, signals, backtest, reports, PIT, and HMC.
    ashare_data_contract.py  A-share dataset tiers and minimum PIT schemas.
    manifest.py              Immutable dataset/experiment manifest contracts.
    models.py  Core research schemas.
    pit.py     Point-in-time query facade.
    hmc.py     HMC/materialist dynamics research boundary.
    backtest/  Auditable A-share orders, fills, daily ledger, trades, metrics.
    pipeline/  PIT/data-root -> visible PIT -> research runner -> ledger backtest -> control report -> morning package CLI.
    crypto_pipeline/  Crypto PIT -> factors/signals -> ledger backtest -> control report -> session package.
  trading/   Active execution layer.
    intent/   Trading mode and order intent primitives.
    paper/    Simulated execution helpers.
    risk/     Risk checks.
    adapters/
      okx/    Gated crypto live_test adapter.
      qmt/    QMT/EasyXT boundary adapter.
  trade/     Compatibility wrappers during migration.

backend/tests/
  unit/      Fast local tests.
  contract/  Boundary tests such as trade intent schemas.
  integration/ External-service tests, opt-in only.

frontend/
  src/       Next.js terminal UI.

backend/tools/
  data/      Manual data maintenance probes. Not part of the app runtime.

docs/
  *.md       Architecture decisions and operator notes.

docs/archive/
  Older quickstarts and historical implementation notes.

legacy/
  futures/          Futures and Wenhua/Sanli material, parked for later.
  macro-platform/   Old Streamlit macro dashboard.
  experiments/      GPU and strategy-search experiments.
```

## Market Ownership

Crypto active surface:

- `backend/run_okx_comprehensive.py`
- `backend/app/markets/crypto/crypto_market_data.py`
- `backend/app/markets/crypto/okx_client.py`
- `backend/app/markets/crypto/okx_feed.py`
- `backend/app/api/crypto/okx_api.py`
- `backend/app/markets/crypto/okx_t_runner.py`
- `frontend/src/components/OkxTrading.tsx`

A-share active surface:

- `backend/app/markets/ashare/akshare_feed.py`
- `backend/app/markets/ashare/tdx_parser.py`
- `backend/app/markets/ashare/tdx_realtime.py`
- `backend/app/api/ashare/market.py`
- `backend/app/api/ashare/bond.py`
- `backend/app/trading/adapters/qmt/bridge.py`
- `backend/app/trade/qmt_bridge.py` compatibility wrapper
- `backend/tests/integration/test_easyxt_bridge_contract.py`
- Canonical research/control input: PIT parquet lake under `<lake-root>/pit/`
- Default project-local data root: `<repo>/DATA/`
- Default project-local A-share root: `<repo>/DATA/ashare/`
- External data lake: `/srv/lan-ai/data/ashare`
- External checks: `/srv/lan-ai/artifacts/ashare-data-checks`

DuckDB is not part of the A-share control-plane acceptance path; it remains a
legacy/tooling cache unless a future PIT migration explicitly promotes it.

Compatibility wrappers remain under `backend/app/data/`, `backend/app/api/`,
`backend/app/strategy/`, and `backend/app/trade/` so older imports keep working
during migration.

Cold zone, not default scope:

- `legacy/futures/backend-scripts/run_futures_backtest.py`
- `legacy/futures/backend-scripts/run_materialist_futures.py`
- `backend/app/data/futures_feed.py`
- `backend/app/strategy/futures_strategies.py`
- `legacy/futures/examples/wenhua_*.py`
- GPU search scripts under `legacy/experiments/gpu/`
- Strategy experiment runners under `legacy/experiments/strategy-runs/`
- Old Streamlit/macro scripts under `legacy/macro-platform/`

## Root Policy

Repository root should stay boring:

- `README.md`
- `backend/`
- `frontend/`
- `docs/`
- `scripts/`
- `legacy/`

Do not add new one-off scripts to the root. Put active backend commands under
`backend/`, shared smoke/test commands under `scripts/`, and parked experiments
under `legacy/`.

## Test Gate

Default health check:

```bash
scripts/test-katana.sh all
```

Focused checks:

```bash
scripts/test-katana.sh unit
scripts/test-katana.sh crypto
scripts/test-katana.sh ashare
scripts/test-katana.sh ashare-pipeline-smoke
scripts/test-katana.sh ashare-benchmark
scripts/test-katana.sh frontend
```

The frontend build is explicit because it depends on `frontend/node_modules`.
Integration tests are also explicit because they may need EasyXT, QMT, or bridge
services outside this repository.

## Product Boundary

The runtime boundary is defined in [Product Boundary](PRODUCT_BOUNDARY.md).

Old wheel reuse and migration routing is tracked in
[Old Wheel Reuse Map](OLD_WHEEL_REUSE_MAP.md). Check that map before adding a
new strategy, report, backtest, or execution-facing module.

Default mode:

```text
KATANA_TRADING_MODE=research
```

Crypto live_test requires `KATANA_TRADING_MODE=live_test`,
`KATANA_ENABLE_CRYPTO_LIVE_TEST=1`, a numeric `KATANA_OKX_MAX_ORDER_USDT`, and
explicit `KATANA_OKX_ALLOWED_PAIRS`.

A-share execution defaults to `KATANA_ASHARE_EXECUTION=manual`; only
`easyxt_bridge` may submit broker-neutral intents to an external bridge.
