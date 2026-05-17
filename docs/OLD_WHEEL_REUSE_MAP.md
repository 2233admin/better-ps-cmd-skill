# Old Wheel Reuse Map

Last indexed: 2026-05-17.

This map is the routing table for old code in `k-atana`. Before adding a new
module, check this document first. If an old wheel is reused, add the adapter or
contract here. If it is not reused, record why.

## Rules

- Active code lives under `backend/app/` unless there is an existing tested
  contract under `backend/src/quant_terminal/`.
- `backend/app/research/` is the target home for PIT-safe A-share research,
  manifests, signals, backtests, reports, and morning packages.
- `backend/app/trading/` is the active execution boundary. Research code must
  not import broker SDKs.
- `backend/app/trade/` is compatibility only.
- `backend/src/quant_terminal/` is an old core package. Reuse only through
  tested contracts or small adapters.
- `legacy/` is parked code. Nothing imports from it without first moving the
  needed piece back into active code with tests.
- LLM output is commentary. It can enter `auxiliary_notes`, never evidence.

## Status Legend

| Status | Meaning |
| --- | --- |
| Canonical | Preferred active surface for new work. |
| Reuse via adapter | Old wheel is useful, but only through a thin adapter and contract tests. |
| Compatibility | Kept so old imports do not break; do not add new features here. |
| Parked | Reference material under `legacy/`; not imported by active code. |
| Quarantine | Known-risk code. Read for ideas only until rewritten and tested. |

## Canonical Active Surface

| Area | Canonical path | Purpose | Notes |
| --- | --- | --- | --- |
| Product boundary | `docs/PRODUCT_BOUNDARY.md` | Trading modes and execution limits. | Must be checked before execution-related work. |
| QMT/EasyXT decision | `docs/ARCHITECTURE_DECISION_QMT_PHASE1.md` | A-share execution architecture. | k-atana emits intents; EasyXT owns QMT/xtquant. |
| Project layout | `docs/PROJECT_STRUCTURE.md` | Active vs legacy directory ownership. | Update when ownership changes. |
| A-share PIT data contract | `backend/app/research/ashare_data_contract.py` | PIT dataset names, blocked datasets, required columns. | Canonical for A-share data eligibility. |
| Immutable manifests | `backend/app/research/manifest.py` | Dataset versions, experiment manifests, hashes. | Canonical traceability identity. |
| Research schemas | `backend/app/research/models.py` | Market, frequency, factor, experiment, signal contracts. | Broker-neutral by design. |
| PIT access | `backend/app/research/pit.py` | Point-in-time query facade. | Must clip by `as_of`. |
| Research runner | `backend/app/research/agent.py` | PIT-safe factor -> signal -> backtest loop. | Reuses `app.strategy.backtest` today; see risks below. |
| Auditable research backtest | `backend/app/research/backtest/` | A-share orders/fills/daily ledger/trades/metrics. | Canonical for morning-package evidence. |
| HMC boundary | `backend/app/research/hmc.py` | HMC/materialist state estimate boundary. | Research-only, no trading imports. |
| Morning package | `backend/app/research/morning_package/` | 9 AM A-share decision package artifacts. | Markdown/PDF/CSV/JSON/audit; intent export is manual-confirm only. |
| Research pipeline | `backend/app/research/pipeline/` | PIT -> research runner -> ledger backtest -> morning package. | Minimum credible chain CLI; supports fixture, `--pit-parquet`, and `--data-root`. |
| Trading mode primitives | `backend/app/trading/intent.py` | Runtime mode and local order intent primitive. | Active app-side mode guard. |
| Paper trading | `backend/app/trading/paper.py` | Simulated execution helper. | Gated by trading mode tests. |
| Active QMT boundary | `backend/app/trading/adapters/qmt/bridge.py` | EasyXT bridge adapter without `xtquant` import. | Only bridge boundary, not research code. |
| Active OKX boundary | `backend/app/trading/adapters/okx/bridge.py` | Gated crypto live-test adapter. | Requires explicit environment gates. |

## Reuse Via Adapter

| Old wheel | Current owner | Reuse target | Contract |
| --- | --- | --- | --- |
| `backend/src/quant_terminal/trade/intent.py` | Old core package | EasyXT broker-neutral execution payload. | `backend/tests/contract/test_trade_intent.py` |
| `quant_terminal.trade.intent.make_trade_intent` | Old core package | `morning_package.adapters.to_easyxt_bridge_payloads` | Export only after `decision=trade` and `manual_action=confirm`. |
| `backend/app/strategy/backtest.py` | Active but old strategy layer | `ResearchAgentRunner` backtest evidence source. | `backend/tests/unit/test_backtest_semantics.py` and research-agent contracts. |
| `backend/app/macro/briefing.py` | Active macro service | Candidate source for morning package `auxiliary_notes`. | Not evidence; LLM text cannot feed evidence fields. |
| `backend/src/quant_terminal/core/metrics.py` | Old core package | Candidate metrics utility after contract review. | Not currently wired into morning package. |
| `backend/src/quant_terminal/strategies/dual_thrust.py` | Old core package | Strategy reference and unit tests. | Covered by `backend/tests/unit/test_strategies.py`; not PIT-safe A-share default. |

## Compatibility Wrappers

These paths should not receive new business logic.

| Wrapper | Points to | Policy |
| --- | --- | --- |
| `backend/app/trade/executor.py` | `backend/app/trading/executor.py` | Compatibility only. |
| `backend/app/trade/qmt_bridge.py` | QMT/EasyXT boundary adapter | Do not use from research code. |
| `backend/app/data/akshare_feed.py` | `backend/app/markets/ashare/akshare_feed.py` | Keep import compatibility. |
| `backend/app/data/tdx_parser.py` | `backend/app/markets/ashare/tdx_parser.py` | Keep import compatibility. |
| `backend/app/data/tdx_realtime.py` | `backend/app/markets/ashare/tdx_realtime.py` | Keep import compatibility. |
| `backend/app/api/market.py` | `backend/app/api/ashare/market.py` | Keep import compatibility. |
| `backend/app/api/bond.py` | `backend/app/api/ashare/bond.py` | Keep import compatibility. |

## Parked Legacy Index

| Zone | Paths | What is inside | Reuse policy |
| --- | --- | --- | --- |
| Strategy experiments | `legacy/experiments/strategy-runs/*.py` | Old backtest demos, ML strategy runners, HMM, walk-forward scripts. | Read for ideas; do not import. Promote one strategy at a time with PIT data and contracts. |
| GPU experiments | `legacy/experiments/gpu/*.py` | GPU grid/massive search scripts and benchmark notes. | Parked until optimization track restarts. |
| Futures material | `legacy/futures/backend-scripts/*.py` | Sanli/Wenhua demos, futures backtest scripts. | Futures is outside current default scope. |
| Futures examples | `legacy/futures/examples/*.py` | Wenhua bridge/backtest examples. | Reference only; broker execution patterns require new boundary tests. |
| Old macro platform | `legacy/macro-platform/*.py` and `legacy/macro-platform/pages/*.py` | Streamlit macro dashboard, stock visualizer, correlation views. | Do not revive UI directly. Extract data logic only with tests. |
| Macro utilities | `legacy/macro-platform/utils/*.py` | Regime detection, alignment, correlation helpers. | Candidate for active macro/research utilities after tests. |
| Archived docs | `docs/archive/*.md` | Old quickstarts and factor API notes. | Reference only; not authority over current architecture. |

## File-Level Index

This is the current old-wheel inventory by file. Add new entries here when a
module is discovered, moved, revived, or quarantined.

### Root And Backend Commands

| File | Status | Notes |
| --- | --- | --- |
| `backend/generate_backtest_report.py` | Quarantine | One-off futures report writer. |
| `backend/run_backtest.py` | Quarantine | Old root-level backtest command. |
| `backend/run_okx_comprehensive.py` | Canonical for crypto smoke, quarantine for framework reuse | Large script; keep smoke behavior, split before reuse. |
| `backend/run_okx_openclaw.py` | Quarantine | Mixed OKX analysis/agent/report runner. |
| `scripts/test-katana.sh` | Canonical | Test and smoke entry. |

### Active App Modules With Old-Wheel Risk

| File | Status | Notes |
| --- | --- | --- |
| `backend/app/strategy/backtest.py` | Reuse via adapter | Current lightweight vector backtester. Needs promotion work before production trust. |
| `backend/app/strategy/engine.py` | Quarantine | Signal generation and order submission are coupled. |
| `backend/app/strategy/engine_optimizer.py` | Quarantine | Optimizer experiment surface. |
| `backend/app/strategy/factors.py` | Reuse candidate | Technical factor helpers. Needs PIT contract review. |
| `backend/app/strategy/futures_strategies.py` | Quarantine | Futures-specific strategy/backtester. |
| `backend/app/strategy/materialist_engine.py` | Reuse candidate | Signal/risk state ideas; not active A-share contract. |
| `backend/app/strategy/market_monitor.py` | Quarantine | Live monitor, alerts, signal logs. |
| `backend/app/strategy/okx_t_runner.py` | Crypto-specific | Do not reuse for A-share. |
| `backend/app/strategy/signals.py` | Reuse candidate | Strategy signal helpers; needs broker-neutral contracts. |
| `backend/app/strategy/t_strategy.py` | Quarantine | Intraday stateful strategy with execution-shaped output. |
| `backend/app/macro/allocation.py` | Reuse candidate | Macro allocation logic. Evidence use needs contracts. |
| `backend/app/macro/briefing.py` | Reuse via auxiliary notes only | LLM macro briefing; not evidence. |
| `backend/app/macro/config.py` | Reuse candidate | Macro config. |
| `backend/app/macro/indicators.py` | Reuse candidate | Macro indicators. |
| `backend/app/macro/knowledge.py` | Reuse candidate | Knowledge search. LLM/wiki data is commentary unless sourced. |

### Compatibility Wrappers

| File | Status | Notes |
| --- | --- | --- |
| `backend/app/api/bond.py` | Compatibility | Use `backend/app/api/ashare/bond.py`. |
| `backend/app/api/market.py` | Compatibility | Use `backend/app/api/ashare/market.py`. |
| `backend/app/data/akshare_feed.py` | Compatibility | Use `backend/app/markets/ashare/akshare_feed.py`. |
| `backend/app/data/crypto_market_data.py` | Compatibility/legacy bridge | Prefer market-owned crypto module. |
| `backend/app/data/okx_client.py` | Compatibility/legacy bridge | Prefer `backend/app/markets/crypto/okx_client.py`. |
| `backend/app/data/okx_feed.py` | Compatibility/legacy bridge | Prefer `backend/app/markets/crypto/okx_feed.py`. |
| `backend/app/data/tdx_parser.py` | Compatibility | Use `backend/app/markets/ashare/tdx_parser.py`. |
| `backend/app/data/tdx_realtime.py` | Compatibility | Use `backend/app/markets/ashare/tdx_realtime.py`. |
| `backend/app/trade/account_reader.py` | Compatibility/unknown | Do not extend until owner is clarified. |
| `backend/app/trade/ctp_bridge.py` | Compatibility/legacy | Futures broker bridge, outside active scope. |
| `backend/app/trade/executor.py` | Compatibility | Use `backend/app/trading/executor.py`. |
| `backend/app/trade/okx_bridge.py` | Compatibility | Use `backend/app/trading/adapters/okx/bridge.py`. |
| `backend/app/trade/qmt_bridge.py` | Compatibility | Use `backend/app/trading/adapters/qmt/bridge.py`. |
| `backend/app/trade/risk.py` | Compatibility/unknown | Prefer `backend/app/trading/risk.py`. |
| `backend/app/trade/ths_bridge.py` | Compatibility/legacy | Outside active A-share execution boundary. |

### Old Core Package: `backend/src/quant_terminal`

| File | Status | Notes |
| --- | --- | --- |
| `backend/src/quant_terminal/core/backtest.py` | Reuse candidate | Unit-tested old backtest engine; promotion requirements still apply. |
| `backend/src/quant_terminal/core/metrics.py` | Reuse candidate | Metrics utilities; can normalize morning package metrics later. |
| `backend/src/quant_terminal/core/portfolio.py` | Reuse candidate | Portfolio logic; needs A-share constraints before promotion. |
| `backend/src/quant_terminal/data/base.py` | Reuse candidate | Data abstractions. |
| `backend/src/quant_terminal/data/crypto.py` | Crypto-specific | Do not reuse for A-share. |
| `backend/src/quant_terminal/data/futures.py` | Parked | Futures data. |
| `backend/src/quant_terminal/data/sanli_futures.py` | Parked | Sanli futures data. |
| `backend/src/quant_terminal/data/stocks.py` | Reuse candidate | Stock data loader; must be checked against PIT contract. |
| `backend/src/quant_terminal/data/unified.py` | Reuse candidate | Unified data interface; needs owner decision. |
| `backend/src/quant_terminal/data/wenhua_futures.py` | Parked | Wenhua futures data. |
| `backend/src/quant_terminal/gpu/core.py` | Parked | GPU utilities. |
| `backend/src/quant_terminal/gpu/grid_search.py` | Parked | GPU search. |
| `backend/src/quant_terminal/optimization/grid_search.py` | Reuse candidate | Optimizer utility; not default research path. |
| `backend/src/quant_terminal/optimization/random_search.py` | Reuse candidate | Optimizer utility; not default research path. |
| `backend/src/quant_terminal/strategies/base.py` | Reuse candidate | Strategy interface. |
| `backend/src/quant_terminal/strategies/dual_thrust.py` | Reuse via tests | Covered by strategy unit tests. |
| `backend/src/quant_terminal/strategies/rbreaker.py` | Reuse via tests | Covered by strategy unit tests. |
| `backend/src/quant_terminal/trade/executor.py` | Quarantine | Old execution abstraction. |
| `backend/src/quant_terminal/trade/intent.py` | Reuse via adapter | Canonical EasyXT payload contract for now. |
| `backend/src/quant_terminal/trade/live_engine.py` | Quarantine | Do not route A-share QMT through it. |
| `backend/src/quant_terminal/trade/sanli_executor.py` | Parked | Futures broker-specific executor. |
| `backend/src/quant_terminal/trade/wenhua_executor.py` | Parked | Futures broker-specific executor. |
| `backend/src/quant_terminal/utils/config.py` | Reuse candidate | Utility config. |
| `backend/src/quant_terminal/utils/logging.py` | Reuse candidate | Logging helper. |
| `backend/src/quant_terminal/utils/validation.py` | Reuse candidate | Validation helper. |

### Legacy Strategy Experiments

| File | Status | Notes |
| --- | --- | --- |
| `legacy/experiments/strategy-runs/run_adaptive_system.py` | Parked | Strategy experiment. |
| `legacy/experiments/strategy-runs/run_all_strategies.py` | Parked | Batch strategy experiment. |
| `legacy/experiments/strategy-runs/run_backtest_demo.py` | Parked | Demo with mocked data. |
| `legacy/experiments/strategy-runs/run_backtest_multi.py` | Parked | Multi-backtest experiment. |
| `legacy/experiments/strategy-runs/run_classic_cta.py` | Parked | CTA experiment. |
| `legacy/experiments/strategy-runs/run_grid_search.py` | Parked | Grid-search experiment. |
| `legacy/experiments/strategy-runs/run_hmm_quarterly.py` | Parked | HMM experiment. |
| `legacy/experiments/strategy-runs/run_indicator_strategies.py` | Parked | Indicator strategy experiment. |
| `legacy/experiments/strategy-runs/run_iteration_v2.py` | Parked | Iteration experiment. |
| `legacy/experiments/strategy-runs/run_live_trading_demo.py` | Quarantine | Demo mentions live routes; do not import. |
| `legacy/experiments/strategy-runs/run_ml_optimized.py` | Parked | ML strategy experiment. |
| `legacy/experiments/strategy-runs/run_ml_strategies.py` | Parked | ML strategy experiment. |
| `legacy/experiments/strategy-runs/run_performance_feedback.py` | Parked | Feedback experiment. |
| `legacy/experiments/strategy-runs/run_smart_ml.py` | Parked | ML experiment. |
| `legacy/experiments/strategy-runs/run_walkforward_dl.py` | Parked | Walk-forward DL experiment. |

### Legacy GPU Experiments

| File | Status | Notes |
| --- | --- | --- |
| `legacy/experiments/gpu/GPU_ACCELERATION_SUMMARY.md` | Parked | Historical notes. |
| `legacy/experiments/gpu/benchmark_comparison.py` | Parked | GPU benchmark. |
| `legacy/experiments/gpu/run_cuda_kernel_search.py` | Parked | CUDA kernel experiment. |
| `legacy/experiments/gpu/run_gpu_grid_search.py` | Parked | GPU grid search. |
| `legacy/experiments/gpu/run_gpu_massive_search.py` | Parked | GPU massive search. |

### Legacy Futures

| File | Status | Notes |
| --- | --- | --- |
| `legacy/futures/INTEGRATION_SUMMARY.md` | Parked | Historical integration notes. |
| `legacy/futures/backend-scripts/run_futures_backtest.py` | Parked | Futures backtest. |
| `legacy/futures/backend-scripts/run_materialist_futures.py` | Parked | Futures materialist runner. |
| `legacy/futures/backend-scripts/run_sanli_demo.py` | Parked | Sanli demo. |
| `legacy/futures/examples/futures_examples.py` | Parked | Examples. |
| `legacy/futures/examples/wenhua_histmat_backtest.py` | Parked | Wenhua + hist-mat backtest. |
| `legacy/futures/examples/wenhua_quant_bridge.py` | Parked | Wenhua bridge example. |
| `legacy/futures/examples/wenhua_quant_integration.py` | Parked | Wenhua integration example. |
| `legacy/futures/examples/wenhua_real_backtest.py` | Parked | Wenhua real-data backtest. |

### Legacy Macro Platform

| File | Status | Notes |
| --- | --- | --- |
| `legacy/macro-platform/data_overview.py` | Parked | Macro data utility. |
| `legacy/macro-platform/example_usage.py` | Parked | Usage example. |
| `legacy/macro-platform/macro_market_analyzer.py` | Reuse candidate | Analysis ideas; needs tests. |
| `legacy/macro-platform/quant_api.py` | Parked | Old API script. |
| `legacy/macro-platform/show_data.py` | Parked | Data display. |
| `legacy/macro-platform/stock_visualizer.py` | Parked | Visualization script. |
| `legacy/macro-platform/test_macro_platform.py` | Parked | Old tests, not default gate. |
| `legacy/macro-platform/宏观分析平台.py` | Parked | Old Streamlit app. |
| `legacy/macro-platform/pages/1_📈_宏观仪表板.py` | Parked | Streamlit page. |
| `legacy/macro-platform/pages/2_🔗_关联分析.py` | Parked | Streamlit page. |
| `legacy/macro-platform/utils/correlation_analysis.py` | Reuse candidate | Correlation helper. |
| `legacy/macro-platform/utils/data_alignment.py` | Reuse candidate | Data alignment helper; must prove no look-ahead. |
| `legacy/macro-platform/utils/regime_detector.py` | Reuse candidate | Regime helper; needs contracts. |

## Quarantine / Known Risk

| Path | Risk | Required promotion work |
| --- | --- | --- |
| `backend/app/strategy/engine.py` | Generates signals and submits orders through executor in the same flow. | Keep out of research. Split signal generation from execution before reuse. |
| `backend/app/strategy/t_strategy.py` | Intraday strategy with execution-shaped signals and stateful behavior. | Needs boundary tests and paper-only harness. |
| `backend/app/strategy/market_monitor.py` | Writes signal logs and triggers alerts from live monitor flow. | Separate telemetry from signal contracts. |
| `backend/generate_backtest_report.py` | One-off futures text report writer. | Do not extend. Replace with report renderer contracts if needed. |
| `backend/run_backtest.py` | Root-level old backtest command. | Route future commands through `scripts/` or active package CLIs. |
| `backend/run_okx_openclaw.py` | Mixed analysis/agent/report script. | Keep as smoke reference; do not make it a general framework. |
| `backend/run_okx_comprehensive.py` | Active crypto smoke but large script surface. | Keep as smoke until split into modules. |
| `backend/src/quant_terminal/trade/live_engine.py` | Old live engine path. | Do not route A-share QMT through it during phase 1. |
| `backend/src/quant_terminal/trade/wenhua_executor.py` | Broker-specific futures executor. | Futures-only; no active import. |
| `backend/src/quant_terminal/trade/sanli_executor.py` | Broker-specific futures executor. | Futures-only; no active import. |

## Morning Package Reuse Routing

| Morning package need | Reused wheel | Current implementation |
| --- | --- | --- |
| Dataset identity | `app.research.manifest.DatasetVersion` | `build_morning_package(..., dataset_version=...)` accepts object or string. |
| Manifest hash | `ExperimentManifest.manifest_hash()` | Builder resolves from manifest when passed. |
| Config hash | `app.research.manifest.sha256_json` | CLI fixtures use stable config hashes. |
| Backtest evidence | `app.strategy.backtest.BacktestResult` shape | Morning package can still normalize old research-runner results for compatibility. |
| Auditable backtest evidence | `app.research.backtest.LedgerBacktestResult` | Pipeline feeds orders/fills/ledger metrics into morning package evidence. |
| Trade intent export | `quant_terminal.trade.intent.make_trade_intent` | `to_easyxt_bridge_payloads` exports confirmed trade intents only. |
| Macro narrative | `app.macro.briefing` | Not wired yet; may enter `auxiliary_notes` only. |

## Promotion Checklist

Use this before moving anything out of `legacy/` or `backend/src/quant_terminal/`.

1. Identify the canonical active owner path.
2. Add or update a contract test first.
3. Prove the code is broker-neutral if it touches research.
4. Prove PIT safety if it touches A-share research.
5. Add an adapter instead of importing a large old module directly.
6. Add the reused path to this map.
7. Run:

```bash
uv run --no-project --with-requirements backend/requirements.txt python -m pytest backend/tests/unit backend/tests/contract -p no:cacheprovider
sentrux check .
```

Or through the repo gate:

```bash
scripts/test-katana.sh unit
scripts/test-katana.sh arch
```

## Sentrux Status

Sentrux rules live in `.sentrux/rules.toml`. The initial rule set is conservative
because the repo still carries a large old-wheel surface. As of this index,
`sentrux check .` passes.

- Block imports from `legacy/` into `backend/app/`.
- Block `xtquant` imports outside the EasyXT/QMT adapter boundary.
- Block `backend/app/research/` imports from `backend/app/trading/` and
  `backend/app/trade/`.
- Warn on new root-level `run_*.py` scripts.
- Warn when new logic is added under compatibility wrappers.

Current caveat: sentrux may need its one-time language grammar package before it
can analyze dependencies locally.
