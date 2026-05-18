# External Wheel Audit (read before building anything that smells like a library)

Last audited: **2026-05-18** (live GitHub API). Re-audit on every major addition.

**Companion to** `OLD_WHEEL_REUSE_MAP.md` (which catalogues *internal* code reuse).
**This doc catalogues** *external* (PyPI / GitHub) libraries that overlap with
k-atana subsystems, so future contributors do not rebuild what already exists.

> **Rule for the shortlist:** every candidate row in this doc MUST cite live data —
> star count, last-push date, license — pulled from the GitHub API or `gh repo view`.
> Adjectives like "active" or "popular" are not enough. Star count alone is not
> enough either (a dead 15k-star repo is worse than an active 4k-star one — see
> tushare below).

## TL;DR

| Subsystem | Current code | Top candidate | Stars | Last push | Status |
|---|---|---|---|---|---|
| Factor library | `backend/app/research/factors/` | **microsoft/qlib** (A-share PIT, Alpha158/360) | 43,119 | 2026-04-22 | active — XAR-417 |
| Performance / tear sheet | `backend/app/research/pipeline/attribution.py` | **ranaroussi/quantstats** | 7,128 | 2026-01-13 | active — XAR-417 |
| Factor IC analysis | (not built) | **stefan-jansen/alphalens-reloaded** | 587 | 2025-12-15 | semi-active — needed |
| Backtest engine | `backend/app/research/backtest/` | **ricequant/rqalpha** | 6,391 | 2026-05-15 | very active — XAR-418 |
| Trading platform / gateway | `backend/app/trading/adapters/` | **vnpy/vnpy** (CTP + GUI + community) | 40,623 | 2026-05-17 | very active — XAR-418 |
| Data: daily / fundamentals / F10 | `scripts/ingest-ashare-*.py` | **akfamily/akshare** | 19,421 | 2026-05-02 | active |
| Data: alternative A-share | — | **zvtvz/zvt** (factor + selection middleware) | 4,139 | 2026-04-13 | active — spike worth |
| Param sweeps / fast backtest | — | **polakowo/vectorbt** | 7,574 | 2026-04-25 | active (OSS slow; PRO is paid) |
| Time-series ML (ai_lab roadmap) | `app/ai_lab/` | **functime-org/functime** (Polars-native) | 1,172 | 2026-05-03 | active |
| Reconciliation state machine | `backend/app/trading/recon_state.py` | (`transitions`) | — | — | **keep hand-rolled** — 5 states, framework over-engineering |
| Live_test rate-limit gate | `backend/app/trading/adapters/qmt/bridge.py` | (`pyrate-limiter`) | — | — | **keep hand-rolled** — tightly coupled to bridge contract |

## DO NOT recommend (graveyard)

| Library | Why |
|---|---|
| **waditu/tushare** | 14,979★ but last push 2024-03-13 — 14 months dead. The community moved to akshare. |
| **quantrocket-llc/empyrical** | 1,479★ but last push 2024-07-26 — 22 months dead. Bigger star count than `empyrical-reloaded` but actually deader. |
| **quantopian/empyrical** | Original, archived; do not depend on it directly. |
| **quantopian/pyfolio** | Original, archived. Use `pyfolio-reloaded` if you must. |
| **quantopian/zipline** | Original archived; use `stefan-jansen/zipline-reloaded` (US-equity focused, no live trading — wrong fit for A-share). |

## Rule

Before writing any module that looks like "a small framework" (factor lib, backtest engine, attribution math, state machine, rate-limiter, data adapter, FSM), the contributor or agent **must**:

1. Spend 30 minutes searching `awesome-quant`, `pip search`, GitHub topic + year filter, and the Chinese ecosystem (akshare / tushare / vnpy / rqalpha / baostock / zvt — none of these surface on western awesome lists).
2. Write a 3–5 candidate shortlist using the live-data format shown above (name, **star count**, **last-push date**, license, one-liner). `gh repo view <repo> --json stargazerCount,pushedAt,licenseInfo` or `curl https://api.github.com/repos/<owner>/<repo>` is the source of truth — not blog posts or training-data assumptions.
3. Only proceed to implementation after declaring **"wheels evaluated, none fit because [evidence with live data]"**.

If you are an LLM agent dispatched on a subtask: the dispatching prompt must include this requirement explicitly. The LLM default is to skip this step and use stale training-data numbers.

## When self-write is correct

- Wheel pulls in heavy unneeded deps (Qt, web framework, etc.) for what's actually 30 lines
- Schema mismatch is fundamental; integration shim larger than the rewrite
- Code is <50 lines, <5 states (over-engineering risk)
- All candidates unmaintained >18 months (check live `pushed_at`, not assumptions)
- The piece is *intentionally* tightly coupled to a k-atana contract (live_test gate, PIT manifest)

Presence of *any one* is enough. Absence of *all* means use the wheel.

## Detailed candidate inventory

### Factor library

- **microsoft/qlib** — 43,119★, last push 2026-04-22, MIT. A-share PIT native since 2022-03; ships Alpha158 + Alpha360 factor zoo, fundamentals adapter from baostock, calendar + adjust-factor layers. pandas-based. https://github.com/microsoft/qlib
- **stefan-jansen/alphalens-reloaded** — 587★, last push 2025-12-15, Apache. Factor IC / quantile / turnover analysis. We don't have this at all.
- **Factor Engine** (arXiv:2602.14138, Feb 2026) — Polars-native + PIT-aware paper. **No public source code link yet** — defer until repo is up + measurable.

### Performance / attribution / tear sheets

- **ranaroussi/quantstats** — 7,128★, last push 2026-01-13, Apache. Modern tear-sheet API, more active and more popular than Stefan Jansen's bundle. **Recommended.**
- **stefan-jansen/empyrical-reloaded** — 107★, last push 2025-12-12, Apache. Math primitives, semi-active fork; small community.
- **stefan-jansen/pyfolio-reloaded** — 590★, last push 2025-12-15, Apache. Wrapper around empyrical-reloaded. Same maintainer state.
- **quantrocket-llc/empyrical** — DEAD (last push 2024-07-26). Bigger star count than the Jansen fork but do not use.

### Backtest engine + trading platform

- **vnpy/vnpy** — 40,623★, last push 2026-05-17 (**yesterday**), MIT. Biggest CN community. Full live-trading platform: CTP gateway, event-driven engine, GUI. **Likely replacement for `app/trading/adapters/qmt/*`.** GUI baggage exists but is optional.
- **ricequant/rqalpha** — 6,391★, last push 2026-05-15 (**3 days ago**), Other. A-share native, plug-in modular, research-first. **Likely replacement for `app/research/backtest/` orchestration.**
- **polakowo/vectorbt** — 7,574★, last push 2026-04-25, Other. Pandas-based, strong for parameter sweeps. Worth a spike for hyperparameter search workflows; note vectorbtpro is the actively-paid version.
- **stefan-jansen/zipline-reloaded** — US-equity oriented, research-only, no live trading. Probably wrong fit for A-share.

### Data sources

- **akfamily/akshare** — 19,421★, last push 2026-05-02, MIT. Free, broad, no auth. Daily / fundamentals / news / index. **Use for new ingest paths.**
- **zvtvz/zvt** — 4,139★, last push 2026-04-13, MIT. "Uniform extendable way to record data, compute factors, select securities." Middleware that sits above data + below factor lib. **Worth a spike** for whether it replaces parts of `app/research/pipeline/`.
- **baostock** (no GitHub repo audit done; community-maintained) — qlib's PIT collector uses this for fundamentals.
- **waditu/tushare** — DEAD (last push 2024-03-13). Do not use.
- **TDX (tdxcli-rs + frida) stays** — for L1/L2 quote and tick data, no external equivalent.

### Misc

- **functime-org/functime** — 1,172★, last push 2026-05-03, Apache. Polars-native time-series ML. Relevant to `app/ai_lab/` future work.

## Tracking

- **XAR-417** — single-component wheel evaluation (qlib / quantstats / alphalens / functime), priority High
- **XAR-418** — strategic vnpy / rqalpha as backtest + trading-adapter layer foundation, priority High

Both have cheap-probe acceptance criteria; do not ship implementation until spikes complete.

## How we got here

On 2026-05-18 the Wave 3-5 closure session shipped hand-rolled `attribution.py`
and `factors/families.py`. Curry flagged the obvious ("GitHub has tons of
wheels") and pushed for a real audit using live API data, not training-data
recall. First-pass `WebSearch` returned a plausible-sounding shortlist that
contained two dead repos as top picks (tushare, quantrocket-llc/empyrical).
Live `api.github.com` fix produced this doc.

**Lesson:** 30 minutes of `curl api.github.com` upfront would have:
- Stopped us writing hand-rolled attribution (quantstats covers it)
- Surfaced zvt (which I missed entirely in the first pass)
- Avoided recommending dead tushare to a future contributor

Numbers in the shortlist, not adjectives.
