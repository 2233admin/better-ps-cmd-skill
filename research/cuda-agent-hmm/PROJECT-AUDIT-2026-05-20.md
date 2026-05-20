# k-atana Ship-Readiness Audit -- 2026-05-20

Read-only audit of the surface OTHER THAN HMM/regime. Empirical -- every claim
cites file:line on main HEAD (commit 515e150).

Severity legend: **P0** blocker, **P1** real gap, **P2** cleanup.

---

## 1. Backtest engine

File: backend/app/research/backtest/engine.py (512 lines)

### What is enforced

- **T+1** -- enforced in AShareLedgerBacktester via entry_date check at L90-94
  (rejects sell when current_date <= entry_date). Reject reason "t_plus_one"
  recorded on the order.
- **ST / suspension / not-tradable** -- L238-243 `_reject_reason` reads
  is_st, is_suspended, is_tradable from the bar row.
- **Limit-up / limit-down** -- L244-247, but these are read as BOOLEAN FLAGS
  from the bar row. The engine does NOT compute limit-hit from prior close vs
  current close. It trusts whatever the input frame says.
- **Volume gate** -- L248-249 rejects when volume <= 0.
- **Lot-size rounding** -- L178 rounds qty to multiples of lot_size (100).
- **Commission + slippage** -- both modelled, L207-211.

### What is NOT enforced (P0/P1)

- **P0 -- limit-up/limit-down flags are synthetic.**
  scripts/ingest-ashare-tradability.py L3-6 own docstring:
  "Suspension and limit-up/down flags require quote-side data ... and are
  emitted as False with a low-fidelity reason note."
  Result: in production-ingested data is_suspended / limit_up / limit_down are
  always False. The engine's gates at engine.py L238-247 NEVER fire on real
  data. Only the synthetic fixture path exercises them.

- **P0 -- single-symbol only.** AShareLedgerBacktester.run() takes one
  symbol. No portfolio simulation, no cross-symbol cash allocation, no
  industry-cap enforcement at the trade level. Pipeline runner calls it per
  symbol in a dict-comprehension (runner.py L387-393) -- each backtest is
  fully isolated.

- **P1 -- no slippage model conditioning.** Slippage is a fixed 0.001
  regardless of size, ADV, or limit-band proximity. Real A-share slippage on
  the upper-bound is unbounded (you don't fill at all).

- **P1 -- T+1 done by entry_date, not settlement clock.** If signals fire on
  partial-trading-day data, an entry/exit pair could intra-day-cross.

### Walk-Forward + Purged CV harness

- Exists ONLY inside the gplearn miner (mine.py L298: TimeSeriesSplit with
  embargo gap). NOT a reusable harness in backend/app/research/methodology/.
- Embargo gap implemented, but NOT López de Prado purged k-fold (the
  "purging" semantics that drop training samples whose forward horizons leak
  into the test window are not implemented).
- Grep result: zero matches for `walk_forward` / `purged` / `PurgedCV` in
  app/research/methodology/.

### DSR / PBO + Benjamini-Hochberg

- methodology/dsr_pbo.py: 389-line implementation, well-documented, exists.
- **CONSUMED BY:** scripts/run-daily-evo.py only (L397-403 import + L497-585
  invocations as ship_gate alpha filter).
- **NOT CONSUMED BY** the runtime ship-gate evaluate_ashare_control() in
  pipeline/control.py -- grep confirms zero references in control.py.
- Verdict: DSR/PBO is a research-loop tool, not a production gate. The
  pipeline output's "control_report" decides promote/observe/reject WITHOUT
  any deflated-Sharpe test.

### vbt-pro path

- Tickets G10/G12 marked completed ("Path A self-impl + parity smoke",
  "Path B from_filled_allocations spike").
- Reality on main HEAD: zero imports of `vectorbtpro` anywhere in
  backend/app/. mine.py L175 imports `vectorbt` (open-source) only, uses
  `vbt.Portfolio.from_signals` (Path A). `from_filled_allocations` (Path B)
  has zero usages.
- Path B spike result was a notebook/spike; nothing was wired into runtime.

### JST iteration outputs (iter-3 / 3b / 5 / 7)

- Zero matches for `jst_adapter` / `eval_jst` / `engine_only_smoke` anywhere
  in backend/app/. Tickets #672-679 closed but the artifacts live outside
  backend/app/ (likely in research/ scratch or a sibling repo). NOT shipped
  as production code.

---

## 2. Factor development framework

### Lane I 13 features (PR-1)

- markets/ashare/features.py is 335 lines real polars code, present and
  imported by markets/ashare/runner.py path. Adapter framework looks alive:
  - adapters.py 10 lines defines Protocol
  - markets/ashare/adapter.py 15 lines actual rename impl (date->event_time,
    symbol->asset_id, amount->notional, adds market="ashare")
  - markets/ashare/constraints.py 19 lines fill_tradability_defaults()
  - markets/ashare/runner.py is **1 line stub**: `"""A-share factor
    entrypoints will live here as the shared core is adopted."""`

  P1 -- markets/ashare/runner.py was supposed to be the canonical entry per
  PR-1 design; it's still a docstring. The real pipeline still lives in
  pipeline/runner.py which uses its own _calculate_factor_frame at L410
  computing only momentum_20d, volatility_20d, turnover_pressure_20d --
  three factors hardcoded, not the Lane I 13.

### Factor families (XAR-411)

- definitions/families.py is 272 lines real code with 3 family specs:
  value_pb_zscore, quality_roe_zscore, momentum_resid_volatility.
- **P0 -- value + quality families cannot run on real A-share data.**
  L11-14 docstring: "Value & quality families need a fundamentals lake that
  is NOT YET BUILT in this repo (no producer exists under
  app.research.pipeline.data_lake)." Confirmed by grep:
  scripts/build-* only produces calendar + coverage + tradability + xdxr.
  No fundamentals producer. Functions accept fundamentals frame from caller
  for unit-testing only.

### G8 catalog docs / G9 5-tier topology

- Glob D:/projects/k-atana/**/catalog*.md returns 0 hits. Glob for
  factor*catalog* returns 0 hits. The Linear tickets G8/G9 are marked
  completed but the docs are not under k-atana/ -- if they exist they're in
  research/ or memory/ outside the repo's docs/ tree.

- **P0 -- 5-tier topology is mostly empty on main.** git ls-files shows
  only 9 .py sources under factors/: __init__, adapters, contracts,
  definitions/__init__, definitions/families, families (shim), transforms,
  mining/_universe_loader, mining/gplearn/mine. Plus a primitives/
  transforms.py and __init__. That is it.
  Stale __pycache__ entries exist for: mining/shinka/run_evo,
  mining/shinka/evaluate, mining/shinka/seed_loader, mining/shinka/initial,
  mining/shinka/metrics_hammergpt, mining/shinka/regularizer,
  mining/pysr/mine, mining/rl_spike/hello, derive/derived_features,
  derive/ashare_features, evaluation/leaderboard/aggregate +
  schema + render + __init__, factors/hg_engine. **Source files for ALL
  of these are absent from git ls-files.**
  Confirmed by scripts/run-daily-evo.py L93-95 own comment:
  "shinka and pysr dirs exist on this branch but contain only .pyc
  artifacts -- no runnable Python source." It guards on
  _SHINKA_AVAILABLE / _PYSR_AVAILABLE module-path existence (L100-101) and
  silently omits both engines when missing.

  Net effect: on main HEAD, run-daily-evo.py runs **gplearn only**. PySR is
  not callable. ShinkaEvolve is not callable. Quality ledger and
  evaluation/leaderboard are not present as Python source. The "G9
  reorg" was rolled back or never landed on main.

  XAR-411 docstring in factors/families.py L1-6 confirms factors/families.py
  is just a compatibility shim re-exporting from definitions/families.py.

### mine.py + run-daily-evo.py (PR-3, PR-3a/b)

- mine.py 718 lines is real. Universe-agnostic. PIT-aware. 4 P0 hotfixes
  visible in commit 3ef3ef3.
- run-daily-evo.py 42550 bytes / ~1080 lines. ENGINES matrix (L159-185)
  shows gplearn always present for crypto + ashare; shinka/pysr conditional
  on source-module existence -- and absent on main.

### ShinkaEvolve + PySR + gplearn end-to-end on real data

- gplearn: source present, callable, ship_gate via DSR/PBO wired.
- ShinkaEvolve: source absent (only .pyc orphans). Cannot run.
- PySR: source absent (only .pyc orphan). Cannot run.

### AR-α regularizer.py

- mining/shinka/regularizer.py source is absent on main (only .pyc orphan).
  G1 ticket #647 marked completed but the artifact didn't land on main.

---

## 3. Data layer (PIT lake)

### XAR-407 calendar wiring

- pipeline/runner.py L295-296: `calendar = _load_trading_calendar(config.data_root)
  if config.data_root is not None else None`, passed into _normalize_legacy_kline.
- L826-838: when calendar is provided, _normalize_legacy_kline uses
  bisect_right to pick the next trading-day open as available_at. Real
  wiring. **Landed.**
- Caveat: only the legacy-schema branch (L294 if-block) hits this. The
  PIT-native branch (L292) doesn't recompute available_at. Acceptable since
  PIT-native frames already carry it.

### XAR-408 tradability_status_pit producer

- scripts/ingest-ashare-tradability.py: 12668 bytes, real code wraps
  tdx-cli security-list. **P1 caveat documented by script itself**: is_st
  is derived from name, is_delisted from list membership, but
  is_suspended / limit_up / limit_down are emitted as False with a
  low-fidelity reason note (script docstring L3-6). Producer ships data,
  but data is incomplete -- the engine downstream cannot reject limit-band
  hits on production data because the source has no quote.

### XAR-409 xdxr / adjustment_factor producer

- scripts/ingest-ashare-xdxr.py: 12864 bytes, real code.
- Resolved by data_lake.py resolve_adjustment_factor_parquet() L85-93.
- Merged into PIT frame by runner.py _with_adjusted_prices L896-910, computes
  adjusted_close = close * adjustment_factor. PIT-safe via
  factor_available_at <= available_at gate. **Landed.**

### Warehouse-first cutover (PR-2)

- pipeline/data_lake.py 193 lines resolves parquets from
  <data_root>/_manifest/coverage.json or globs. Wired into runner.py via
  resolve_kline_daily_parquet (L277), resolve_tradability_status_parquet
  (L871), resolve_adjustment_factor_parquet (L874),
  resolve_trading_calendar_parquet (L785).
- **Landed**, but the cutover is partial: PipelineConfig still accepts
  config.pit_parquet for direct-file mode (L274) and
  config.data_root for lake mode (L276), plus a fixture fallback (L278).
  Three input paths is the opposite of "warehouse-first only".

### PR-3a 4 P0 PIT bugs hotfix

- Commit 3ef3ef3 "fix(factors): 4 P0 PIT bugs in gplearn miner (PR-3a hotfix)".
- mine.py L274-289 explicitly documents per-fold NaN/inf filter (was global
  pre-split = leakage), forward_ret via shift(-1) instead of current/back
  next_ret, signal-attach via join, etc.
- Confirmed in tests: test_pit_audit_fixes.py exists with TestP01-P04
  classes covering each fix.
- **Landed.**

---

## 4. Execution / broker layer

### TDX account_reader.py L139-149 stubs

- L241-251 still returns empty lists. Docstring L168-171: "Until that
  calibration lands, the position/trade/entrust readers report
  _uncalibrated() so MultiSourceAccountReader can fall back to other
  sources." **NOT calibrated.** XAR-414 marker still active.
- Live consequence: if EasyXT/QMT bridge isn't connected, the entire
  account snapshot returns empty -- no positions read.

### XAR-414 EasyXT/QMT data probe matrix

- scripts/probe-tdx-controls.py 5938 bytes exists -- a real probe script.
- adapters/qmt/bridge.py L46-50 buy/sell go through HTTP POST to a bridge
  URL. **P0 -- query_positions / query_orders / cancel are stubs** at
  L141-148 returning [] / False. Read-back over HTTP not implemented.
  Comment at L8-10: "k-atana emits execution intents; the external EasyXT
  bridge owns broker SDK integration" -- so the read-back is conceptually
  pushed out of scope, but reconciliation needs it.

### XAR-412 reconciliation state machine

- trading/recon_state.py 177 lines, full pending->submitted->filled->
  reconciled / quarantined state machine, JSONL-backed, idempotent. Tests
  cover all transitions (test_recon_state.py 12 tests collected).
  **Landed.**
- Caveat: state machine is wired only if intent IDs flow in. The
  acceptance/rejection/fill IDs come from journal observation -- which
  needs query_orders() to work (see above). End-to-end reconcile in
  production currently broken at the read-back layer.

### XAR-413 EasyXT live_test gate

- adapters/qmt/bridge.py L56-105 mirrors OKX live_test_rejection:
  env-flag, allowlist, notional cap, daily-order cap. tests
  test_qmt_live_test_gate.py 8 tests verify all branches.
- **Landed -- but with caveats.**
  - L25-27 explicit: daily order counter is in-process dict only, NOT
    persisted. Process restart resets the gate. Cross-process enforcement
    requires redis/sqlite -- noted but not done.
  - L123 every submitted intent has `dry_run: True` hardcoded. Live
    test passes the gate then sends dry_run=True to bridge. The "live"
    test never sends a live order.

---

## 5. Crypto

### OKX sandbox auth (XAR-430)

- Closed-out per ticket. scripts/xar430-auth-smoke.py 2936 bytes confirms
  read-write artifact landed.

### Kline backfill

- scripts/ingest-crypto-mark-price.py 1960 bytes, ingest-crypto-funding.py
  4377 bytes, ingest-crypto-open-interest.py 5122 bytes. All real ingest
  scripts using ccxt + python-okx (per wheel-search rule). Ingest-kline
  not visible in scripts/ list at top level -- presumably embedded in
  okx_pit_writer per backfill_funding_rate import (funding script L25).

### 4 alphas IC + backtest on BTC/ETH 3-month + 8-symbol 2yr expansion

- crypto_pipeline/factors.py 171 lines defines CryptoFactorValue,
  generate_crypto_signals, calculate_crypto_factors. Looks real.
- Results artifacts not visible under backend/ -- they'd be under
  artifacts/. Cannot verify from code that the IC + backtest produced
  results in repo. Tickets #640-641 marked complete; if artifacts exist
  they live outside backend/.

### funding / OI / mark / index PIT datasets

- **P0 -- silent fallback to synthetic data.** crypto_pipeline/runner.py
  L185-196: if funding_rate / funding_time / mark_price / index_price /
  open_interest columns are missing from the kline frame, the runner
  fills them with zeros or with `close` value as a stand-in. The funding
  check at L201-204 raises only if the PIT contract validation fails on
  the synthesized columns -- which it usually won't, because they were
  just synthesized.
  Net: a swap backtest with no real funding data will run silently using
  funding_rate = 0, mark_price = close, index_price = close, open_interest
  = null. Comment at L186-194 makes this synthesis explicit.

---

## 6. AI lab isolation (XAR-416)

- backend/app/ai_lab/ exists. __init__.py is empty (0 lines).
- predict.py 259 lines, train.py 104 lines, kronos_predictor.py 154 lines,
  models/tft.py 76 lines, kronos_model/kronos.py 417 lines.
- Grep for `from app.ai_lab` / `import.*ai_lab` in backend/app/research:
  **zero matches.** Isolation is real -- research/ does not depend on
  ai_lab/. **Landed.**
- P2: train.py L127 has a single TODO ("# TODO: 实现完整的 RL 训练循环") --
  the RL training loop is a stub. Not a research blocker since research/
  doesn't depend on it.

---

## 7. Test surface

`backend/.venv/Scripts/python.exe -m pytest --collect-only -q` ran clean.

- **345 tests collected in 7.97s.**
- **Zero collection errors.**
- xfail / skip with TODO: none surfaced in the collection output. Tail of
  collection shows recon_state (12 tests), qmt_live_test_gate (8 tests),
  pit_audit_fixes (TestP01-P04), qlib_wq_horror, strategies (Dual Thrust,
  RBreaker, Signal).

---

## 8. Active TODOs / FIXMEs / XXXs in backend/app/

Grep result: **2 TODO occurrences total** across the entire backend/app/:

- backend/app/research/methodology/regime.py:59 -- "See
  expanding_fit_hmm_batched (TODO XAR-466)." (XAR-466 closed; comment
  stale.)
- backend/app/ai_lab/train.py:127 -- "# TODO: 实现完整的 RL 训练循环"
  (RL training loop stub.)

No FIXME / XXX / HACK markers anywhere in backend/app/. Low surface in the
strict sense -- but absence of markers does NOT mean absence of debt: the
silent fallbacks documented above (synthetic funding data, false-default
tradability flags, dry_run=True hardcoded, .pyc-only orphan modules) are
honest gaps that were never tagged as TODO.

---

## Honest bottleneck verdict

**Neither backtest engine nor factor framework is the lid. Both are
"runnable, but lying about what they cover." The real bottleneck is the
data layer's silent synthesis combined with the missing factor mining
sources.**

Stack-ranked by ship-blocking severity:

1. **P0 -- production data is faking the constraint surface.** The
   tradability producer emits is_suspended / limit_up / limit_down = False
   for every row (no quote-side data ingested). The backtest engine has
   the gate logic but the gate never fires. ANY backtest result on
   real data is overstated. A 5% drawdown on a strategy whose entries
   happen on limit-up days is a fiction -- those orders would never fill.

2. **P0 -- factor mining is gplearn-only on main.** ShinkaEvolve, PySR,
   AR-α regularizer, factor leaderboard, derive/ashare_features, hg_engine
   exist only as stale .pyc files. Source missing from git ls-files.
   run-daily-evo.py self-documents this at L93-95. So the "G1/G2/G4/G6
   daily evo runner" is a single-engine setup despite ticket claims.

3. **P0 -- crypto swap pipeline synthesizes funding/OI/mark/index when
   missing.** crypto_pipeline/runner.py L185-196 silently fills with
   close-as-mark, funding=0, etc. Any swap backtest result is suspect
   unless you can prove the input parquet had real columns.

4. **P0 -- value + quality factors have no production fundamentals
   producer.** definitions/families.py docstring explicit -- the
   fundamentals lake is "not yet built". The two factor families that
   matter for cross-sectional A-share alpha (PB, ROE) cannot run on real
   data without a manual fixture. Only momentum_resid_volatility, the
   OHLCV-only family, runs end-to-end.

5. **P0 -- live execution read-back is missing.** QMTBridge.query_positions
   / query_orders return []. TDX reader returns [] for positions / trades /
   entrusts. The recon_state machine is fine -- it just has nothing to
   reconcile against. Live trading would emit intents into the void.

6. **P1 -- DSR/PBO is not a runtime gate.** It's only in the evo loop.
   The pipeline ship-gate evaluate_ashare_control() decides
   promote/observe/reject without any deflated-Sharpe test. Backtests
   that pass control_report.decision="promote" have NOT survived
   multi-trial selection bias correction.

7. **P1 -- Walk-Forward is gplearn-internal, not a reusable harness.** No
   methodology/walk_forward.py module. No Purged K-fold (only embargo gap).
   The factor framework cannot offer "WF-validated" claims as a portable
   contract.

8. **P1 -- markets/ashare/runner.py is a 1-line docstring.** PR-1
   intended this as the canonical A-share entry; the actual pipeline
   still drives off pipeline/runner.py with 3 hardcoded factors.

9. **P2 -- topology .pyc orphans pollute the tree.** Cleanup needed:
   either restore the source files (PySR, Shinka, leaderboard, derive,
   hg_engine, rl_spike, indicators/, regularizer) from
   factor-framework-merge / archive, or rm the orphan __pycache__
   directories so future audits don't get false signals.

10. **P2 -- the `factor-framework-merge` branch is identical to main for
    factors/.** Ticket #667 "promote factor-framework-merge to main" was
    closed but the promote left both branches at the same incomplete
    state. If the missing sources existed elsewhere (worktree, archive)
    they were never committed to either branch.

**Answer to the user's question: the lid is the data layer, not the
backtest engine and not the factor development framework.** The engine
implements the constraints honestly but operates on data where the
constraint flags are synthesized to False. The factor framework's
"adapter + 5-tier topology + 18 mother factors" narrative is half-shipped
-- the structural skeleton (adapters, contracts, primitives, families
spec) exists, but the meat (mining engines beyond gplearn, fundamentals
producer, derive features, leaderboard) is either missing source on main
or guarded behind silent fallbacks.

A useful next move (if not on the HMM track) is one focused sprint on
the data layer: real quote-side ingest for limit-band flags, a
fundamentals producer for value/quality, and an end-to-end test that
asserts no silent synthesis happened (no fallback columns, no false-
default tradability, no .pyc-only modules referenced). Without that, every
ship-gate verdict on this codebase is rubber-stamped fiction.
