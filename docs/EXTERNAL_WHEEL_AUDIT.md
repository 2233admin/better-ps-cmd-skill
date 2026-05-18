# External Wheel Audit (read before building anything that smells like a library)

Last audited: 2026-05-18. Re-audit on every major addition.

**Companion to** `OLD_WHEEL_REUSE_MAP.md` (which catalogues *internal* code reuse).
**This doc catalogues** *external* (PyPI / GitHub) libraries that overlap with
k-atana subsystems, so future contributors do not rebuild what already exists.

## TL;DR

| Subsystem | Current code | Best external wheel | Status |
|---|---|---|---|
| Factor library | `backend/app/research/factors/` | **qlib** (MS, A-share PIT native, Alpha158/360) | Under evaluation — XAR-417 |
| Performance attribution | `backend/app/research/pipeline/attribution.py` | **empyrical-reloaded** + **quantstats** | Under evaluation — XAR-417 |
| Factor IC analysis | (we don't have this) | **alphalens-reloaded** | Required if we keep hand-rolled factors |
| Backtest engine | `backend/app/research/backtest/` | **rqalpha** / **vnpy** | Strategic — XAR-418 |
| Trading adapters | `backend/app/trading/adapters/{qmt,okx}/` | **vnpy** CTP gateway / native SDKs | Strategic — XAR-418 |
| Data sources (daily/fundamentals) | `scripts/ingest-ashare-*.py` + tdxcli-rs | **akshare** / **tushare** / **baostock** | Worth evaluating for daily/F10 only — TDX stays for L1/L2 |
| Time-series ML | `app/ai_lab/` (2026 roadmap) | **functime** (Polars-native) | Defer to ai_lab phase |
| Reconciliation state machine | `backend/app/trading/recon_state.py` | `transitions` | **Keep hand-rolled** — 5 states, framework is over-engineering |
| Rate-limit / live_test gate | `backend/app/trading/adapters/qmt/bridge.py` | `pyrate-limiter` | **Keep hand-rolled** — tightly coupled to bridge contract |

## Rule

Before writing any module that looks like "a small framework" (factor lib, backtest engine, attribution math, state machine, rate-limiter, data adapter, FSM), the contributor or agent **must**:

1. Spend 30 minutes searching `awesome-quant`, `pip search`, GitHub topic + year filter, and the Chinese ecosystem (akshare / tushare / vnpy / rqalpha / baostock — none of these surface on western awesome lists).
2. Write a 3–5 candidate shortlist (name, last commit date, license, one-liner).
3. Only proceed to implementation after declaring **"wheels evaluated, none fit because [evidence]"**.

If you are an LLM agent dispatched on a subtask: the dispatching prompt must include this requirement explicitly. The LLM default is to skip this step.

## When self-write is correct

- Wheel pulls in heavy unneeded deps (Qt, web framework, etc.) for what's actually 30 lines
- Schema mismatch is fundamental; integration shim larger than the rewrite
- Code is <50 lines, <5 states (over-engineering risk)
- All candidates unmaintained >18 months
- The piece is *intentionally* tightly coupled to a k-atana contract (live_test gate, PIT manifest)

The presence of *any one* of these is enough to write your own. The absence of *all* of them means use the wheel.

## Detailed candidate inventory

### Factor library

- **qlib** (Microsoft, MIT, 43.1k★, v0.9.7 2025-08) — A-share PIT native since 2022-03; ships Alpha158 + Alpha360 factor zoo, fundamentals adapter from baostock, calendar + adjust-factor layers. pandas-based. https://github.com/microsoft/qlib
- **alphalens-reloaded** (active fork) — factor IC / quantile / turnover analysis. We don't have this at all.
- **Factor Engine** (arXiv:2602.14138, 2026) — Polars-native + PIT-aware factor engine; 11 Stambaugh-Yuan mispricing factors. Best Polars-stack fit but novelty risk; source repo not linked in paper.

### Performance / attribution

- **empyrical-reloaded** (quantrocket-llc fork) — alpha/beta/IR/tracking-error/Sharpe/Sortino/Calmar. Battle-tested.
- **quantstats** (Ran Aroussi) — modern tear-sheet wrapper. Cleaner API than pyfolio.
- **pyfolio-reloaded** — older but works.

### Backtest engine / trading adapters

- **rqalpha** (RiceQuant) — A-share native, plug-in modular, research-first. Likely fit for replacing `app/research/backtest/`.
- **vnpy** — biggest CN community, full live-trading platform, CTP integration. Likely fit for replacing `app/trading/adapters/qmt/*`. Has GUI baggage we don't want.
- **zipline-reloaded** (Stefan Jansen) — US-equity oriented, research-only, no live trading. Probably wrong fit for A-share.
- **vectorbt** — pandas-based, strong for parameter sweeps. Worth a spike for hyperparameter search.

### Data sources

- **akshare** — free, broad, no auth. Daily / fundamentals / news / index. Could replace daily-level ingest scripts.
- **tushare** — best CN data quality, free tier has quotas.
- **baostock** — free, registered, A-share focused. qlib's PIT collector uses this.
- **TDX (tdxcli-rs + frida) stays** — for L1/L2 quote and tick data, no external equivalent.

### Misc

- **functime** — Polars-native time-series ML. Relevant to `app/ai_lab/` future work.

## Tracking

- **XAR-417** — single-component wheel evaluation (qlib / empyrical / alphalens / Factor Engine / quantstats), priority High
- **XAR-418** — strategic vnpy / rqalpha as backtest + trading-adapter layer foundation, priority High

Both have cheap-probe acceptance criteria; do not ship implementation until spikes complete.

## How we got here

On 2026-05-18 the Wave 3-5 closure session shipped hand-rolled `attribution.py`
and `factors/families.py`. Curry flagged the obvious: "GitHub has tons of
wheels". Wheel audit produced this doc. Lesson: 30 minutes of `awesome-quant`
upfront saves a day of writing plus follow-up replacement work.
