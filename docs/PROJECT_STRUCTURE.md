# Project Structure

Katana has two active market lines:

- `crypto`: OKX market data, factors, smoke analysis, and frontend views.
- `ashare`: A-share research, QMT boundary contracts, and the external Parquet lake.

Futures, GPU searches, and older macro/Streamlit tools are retained as legacy or
experimental material. They are not part of the default test gate.

## Active Paths

```text
backend/app/
  api/       HTTP API handlers.
  data/      Market data adapters.
  macro/     Macro research services still used by the app.
  strategy/  Backtest, factors, and signal logic.
  trade/     Broker-neutral intent and bridge boundaries.

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
- `backend/app/data/crypto_market_data.py`
- `backend/app/data/okx_client.py`
- `backend/app/data/okx_feed.py`
- `backend/app/api/okx_api.py`
- `backend/app/strategy/okx_t_runner.py`
- `frontend/src/components/OkxTrading.tsx`

A-share active surface:

- `backend/app/data/akshare_feed.py`
- `backend/app/data/tdx_parser.py`
- `backend/app/data/tdx_realtime.py`
- `backend/app/trade/qmt_bridge.py`
- `backend/tests/integration/test_easyxt_bridge_contract.py`
- External data lake: `/srv/lan-ai/data/ashare`
- External checks: `/srv/lan-ai/artifacts/ashare-data-checks`

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
scripts/test-katana.sh frontend
```

The frontend build is explicit because it depends on `frontend/node_modules`.
Integration tests are also explicit because they may need EasyXT, QMT, or bridge
services outside this repository.
