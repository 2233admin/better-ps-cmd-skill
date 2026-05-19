## Factor Framework Merge Handoff

Branch: `factor-framework-merge`  
Base: `origin/main @ f640dab`  
Date: `2026-05-19`

### Status Update — 2026-05-20

This handoff doc was written 2026-05-19 14:39, before `origin/crypto` shipped the
W1 ship-gate work later the same day (Lane D DSR/PBO methodology,
Lane I A股 universe mining wire, Lane J dual-universe daily-evo runner).

Supersedes:

- **Next Steps #5** ("Do not port crypto mining runners or funding/OI scripts
  into shared runtime") — stale. `scripts/run-daily-evo.py` + `methodology/dsr_pbo.py`
  are the W1 ship-gate machinery and must be ported onto this branch via PR-3
  (B-axis) after the factor adapter interface (PR-1) is stable.

Three-axis merge plan now in effect (target = this branch, integration order
A → C → B, then promote to `main` after W1 ship-gate rerun passes):

- A-axis (factor abstraction) — port Lane I 13 features into
  `markets/ashare/adapter.py`, supersedes the temporary
  `mine.py::_get_inst_col(universe)` gate.
- C-axis (data ingest) — warehouse-first wins; drop `delta_lake.py` +
  `chinadata` ingest chain; keep `init-data-lake-layout.py` +
  `ingest-ashare-tradability.py` + `ASHARE_DATA_PIT_SPEC.md`.
- B-axis (mining execution) — port `dsr_pbo.py` + `run-daily-evo.py` and
  rewrite `mine.py` against the new adapter interface.

`archive/crypto-w1-complete` tag pinned at `crypto @ bfab2d2` before any
porting started, so W1 lineage is reachable indefinitely.

### What Landed

First-pass shared factor-core skeleton was added without changing live A-share
pipeline behavior.

New shared-core files:

- `backend/app/research/factors/contracts.py`
- `backend/app/research/factors/adapters.py`
- `backend/app/research/factors/definitions/__init__.py`
- `backend/app/research/factors/definitions/families.py`
- `backend/app/research/factors/primitives/__init__.py`
- `backend/app/research/factors/primitives/transforms.py`

New market adapter skeleton:

- `backend/app/research/markets/__init__.py`
- `backend/app/research/markets/ashare/{__init__,contracts,adapter,constraints,runner}.py`
- `backend/app/research/markets/crypto/{__init__,contracts,adapter,constraints,runner}.py`

Legacy import compatibility preserved:

- `backend/app/research/factors/families.py` now re-exports from `definitions.families`
- `backend/app/research/factors/transforms.py` now re-exports from `primitives.transforms`

New tests:

- `backend/tests/unit/test_factor_market_adapters.py`

### What Was Intentionally Not Changed

- `backend/app/research/pipeline/runner.py`
- `backend/app/research/pipeline/world_snapshot.py`
- `backend/app/research/backtest/*`
- Any live A-share factor names or factor-frame contract

Reason: `main` runtime still uses inlined factor logic inside `pipeline/runner.py`.
The old `factors/*` modules were not the production call path yet.

### Validated

Command run:

```powershell
uv run --project backend pytest backend\tests\unit\test_factor_transforms.py backend\tests\unit\test_factor_families.py backend\tests\unit\test_factor_market_adapters.py -q
```

Result:

- `27 passed`

### Current Architecture Decision

One project, one shared factor core, separate market adapters.

Shared layer:

- `research/factors/*`

Market-specific layer:

- `research/markets/ashare/*`
- `research/markets/crypto/*`

Canonical shared-core panel should converge on:

- `asset_id`
- `market`
- `event_time`
- `available_at`
- `close`

Adapters own:

- `symbol -> asset_id` for A-share
- `inst_id -> asset_id` for crypto

### Known Constraints

- Do not merge `origin/crypto` wholesale into `main`.
- `origin/crypto` mixes two schema dialects:
  - A-share-like: `symbol/date`
  - crypto-like: `inst_id/event_time/funding_rate`
- `pipeline/runner.py` must keep producing the current factor frame:
  - `factor`
  - `symbol`
  - `timestamp`
  - `value`
  - `as_of`
  - `metadata`

### Next Steps

1. Move the current inlined A-share runtime factors from `pipeline/runner.py`
   into shared-core modules while preserving factor names:
   - `momentum_20d`
   - `volatility_20d`
   - `turnover_pressure_20d`
2. Keep `pipeline/runner.py` as a compatibility shim that calls the new shared core.
3. Introduce a canonical factor-frame helper that can emit both:
   - shared internal frame using `asset_id`
   - current public A-share frame using `symbol`
4. Port only market-agnostic pieces from `origin/crypto` next:
   - `primitives/base.py`
   - `primitives/ts.py`
   - `primitives/cs.py`
   - `evaluation/leaderboard/schema.py`
   - `mining/shinka/regularizer.py`
5. Do not port crypto mining runners or funding/OI scripts into shared runtime.

### Rule For Follow-up Agents

- Work on `factor-framework-merge`, not `main`.
- Do not modify `DATA/Ashare` or the A-share control-plane scripts from this branch.
- Treat this branch as a refactor/integration lane only.
