# External Wheel Audit (read before building anything that smells like a library)

Last audited: **2026-05-18** (live GitHub API + PyPI download stats via opencli). Re-audit on every major addition.

**Companion to** `OLD_WHEEL_REUSE_MAP.md` (which catalogues *internal* code reuse).
**This doc catalogues** *external* (PyPI / GitHub) libraries that overlap with
k-atana subsystems, so future contributors do not rebuild what already exists.

> **Rule for the shortlist:** every candidate row in this doc MUST cite three live
> data points — **stars + last-push date + PyPI installs/day** — pulled from the
> GitHub API (`curl https://api.github.com/repos/<o>/<r>`) and PyPI
> (`opencli pypi downloads <pkg>` or `pypistats`). Adjectives ("active", "popular")
> are not enough. Any single number is not enough either:
>
> - **tushare** has 15k stars and 17k installs/day, but last push 2024-03 (dead 14mo). New `pip install` users are paying customers of tushare.pro getting binaries — the OSS repo is not maintained. Don't add to a new project.
> - **zvtvz/zvt** has 4.1k stars (looks healthy) but only **12 installs/day** — a ghost project with abandoned community.
> - **microsoft/qlib** has 43k stars but only **1.8k installs/day** — research/PoC star bait, much lower actual prod adoption than the star count suggests (still legit, but calibrate expectations).
>
> Triangulate three signals before committing.

## TL;DR

| Subsystem | Current code | Top candidate | Stars | Last push | PyPI/day | Status |
|---|---|---|---|---|---|---|
| Factor library | `backend/app/research/factors/` | **microsoft/qlib** (A-share PIT, Alpha158/360) | 43,119 | 2026-04-22 | 1,760 | active — XAR-417 (star bait; prod adoption modest, still legit) |
| Performance / tear sheet | `backend/app/research/pipeline/attribution.py` | **ranaroussi/quantstats** | 7,128 | 2026-01-13 | 8,442 | active — XAR-417 (clear winner: 17x empyrical-reloaded installs) |
| Factor IC analysis | (not built) | **stefan-jansen/alphalens-reloaded** | 587 | 2025-12-15 | 1,388 | semi-active — needed |
| Backtest engine | `backend/app/research/backtest/` | **ricequant/rqalpha** | 6,391 | 2026-05-15 | 143 | very active commits — XAR-418 (low installs: niche but maintained) |
| Trading platform / gateway | `backend/app/trading/adapters/` | **vnpy/vnpy** (CTP + GUI + community) | 40,623 | 2026-05-17 | 1,016 | very active — XAR-418 (most CTP users install vnpy via Github source, not PyPI) |
| Data: daily / fundamentals / F10 | `scripts/ingest-ashare-*.py` | **akfamily/akshare** | 19,421 | 2026-05-02 | 70,032 | active — **highest CN-data install rate by far** |
| Data: alternative A-share | — | **zvtvz/zvt** (factor + selection middleware) | 4,139 | 2026-04-13 | **12** | **DEMOTE — ghost project (12 installs/day = ~360/month, no community)** |
| Param sweeps / fast backtest | — | **polakowo/vectorbt** | 7,574 | 2026-04-25 | 14,610 | active (OSS slow; PRO is paid; very high installs suggests heavy real use) |
| Time-series ML (ai_lab roadmap) | `app/ai_lab/` | **functime-org/functime** (Polars-native) | 1,172 | 2026-05-03 | 487 | active but niche |
| Reconciliation state machine | `backend/app/trading/recon_state.py` | (`transitions`) | — | — | — | **keep hand-rolled** — 5 states, framework over-engineering |
| Live_test rate-limit gate | `backend/app/trading/adapters/qmt/bridge.py` | (`pyrate-limiter`) | — | — | — | **keep hand-rolled** — tightly coupled to bridge contract |

## DO NOT recommend (graveyard)

| Library | Why |
|---|---|
| **waditu/tushare** | 14,979★ + **16,983 installs/day** but last push 2024-03-13 — 14 months dead. High install count is users paying for tushare.pro binaries (commercial), not OSS health. Community moved to akshare (70k/day). |
| **quantrocket-llc/empyrical** | 1,479★ + 509 installs/day but last push 2024-07-26 — 22 months dead. Bigger star count than `empyrical-reloaded` (107★) but ~10x fewer installs than quantstats (8,442/day). Skip both. |
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

- **microsoft/qlib** — 43,119★, last push 2026-04-22, **1,760 installs/day** (pyqlib), MIT. A-share PIT native since 2022-03; ships Alpha158 + Alpha360 factor zoo, fundamentals adapter from baostock, calendar + adjust-factor layers. pandas-based. Star/install ratio (~24:1) is high — much of the star count is research interest, not prod adoption. Still legit for what we need (PIT + factor zoo), but calibrate community-support expectations. https://github.com/microsoft/qlib
- **stefan-jansen/alphalens-reloaded** — 587★, last push 2025-12-15, **1,388 installs/day**, Apache. Factor IC / quantile / turnover analysis. We don't have this at all. Healthier than the star count suggests — install rate beats qlib relative to stars (2.4:1 vs 24:1).
- **Factor Engine** (arXiv:2602.14138, Feb 2026) — Polars-native + PIT-aware paper. **No public source code link yet** — defer until repo is up + measurable.

### Performance / attribution / tear sheets

- **ranaroussi/quantstats** — 7,128★, last push 2026-01-13, **8,442 installs/day**, Apache. Modern tear-sheet API. **Clear winner: ~17x more installs than empyrical-reloaded (4,864), ~6x pyfolio-reloaded (1,354).** **Recommended.**
- **stefan-jansen/empyrical-reloaded** — 107★, last push 2025-12-12, **4,864 installs/day**, Apache. Math primitives, semi-active fork. Install rate respectable but quantstats wraps it and adds tear-sheets.
- **stefan-jansen/pyfolio-reloaded** — 590★, last push 2025-12-15, **1,354 installs/day**, Apache. Wrapper around empyrical-reloaded. Lower install rate than quantstats — skip unless you specifically need pyfolio's API.
- **quantrocket-llc/empyrical** — DEAD (last push 2024-07-26), 509 installs/day still leaking. Bigger star count than the Jansen fork but do not use.

### Backtest engine + trading platform

- **vnpy/vnpy** — 40,623★, last push 2026-05-17 (**yesterday**), **1,016 PyPI installs/day**, MIT. Biggest CN community. Full live-trading platform: CTP gateway, event-driven engine, GUI. **Likely replacement for `app/trading/adapters/qmt/*`.** GUI baggage exists but is optional. PyPI install count understates real adoption — vnpy users typically clone from Github + run the GUI installer rather than `pip install`.
- **ricequant/rqalpha** — 6,391★, last push 2026-05-15 (**3 days ago**), **143 installs/day**, Other. A-share native, plug-in modular, research-first. Low install rate is the red flag here — niche tool, mostly used by quantitative research teams that adopted it early. Still maintained, but spike before committing.
- **polakowo/vectorbt** — 7,574★, last push 2026-04-25, **14,610 installs/day**, Other. Pandas-based, strong for parameter sweeps. Very high install rate (2x vnpy) suggests heavy real use for hyperparameter search workflows. Worth a spike; note vectorbtpro is the actively-paid version.
- **stefan-jansen/zipline-reloaded** — US-equity oriented, research-only, no live trading. Probably wrong fit for A-share.

### Data sources

- **akfamily/akshare** — 19,421★, last push 2026-05-02, **70,032 installs/day**, MIT. Free, broad, no auth. Daily / fundamentals / news / index. **Use for new ingest paths.** Highest install rate of any CN data library by a 4x margin — clearly the standard.
- **zvtvz/zvt** — 4,139★, last push 2026-04-13, **12 installs/day**, MIT. "Uniform extendable way to record data, compute factors, select securities." **DEMOTE — install rate (~360/month) signals abandoned community despite recent commits + healthy star count. Triangulation flagged this one — first audit missed it entirely.** Do not adopt without contacting maintainers about user base.
- **baostock** — **9,017 installs/day** (no GitHub repo audit done; community-maintained). qlib's PIT collector uses this for fundamentals. Strong install rate justifies its role as a complement to akshare.
- **waditu/tushare** — DEAD (last push 2024-03-13), but **16,983 installs/day still**. Install rate is users paying for tushare.pro binaries — OSS repo is unmaintained, the company is selling commercial access. Do not add to a new OSS project.
- **TDX (tdxcli-rs + frida) stays** — for L1/L2 quote and tick data, no external equivalent.

### Misc

- **functime-org/functime** — 1,172★, last push 2026-05-03, **487 installs/day**, Apache. Polars-native time-series ML. Relevant to `app/ai_lab/` future work. Modest install rate — early/niche, but Polars stack alignment is high.

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

**Third-pass lesson (added 2026-05-18 evening):** GitHub stars + last-push alone still misread two repos:
- **zvt** looked healthy on Github (4.1k★, recent commits) but PyPI shows only **12 installs/day** — abandoned community, demoted.
- **tushare** looked dead on Github (no commits 14mo) but PyPI shows **17k installs/day** — looked usable from one signal, but those are paying-customer binary installs, not OSS health.

Triangulate three signals (stars + last-push + PyPI installs/day via `opencli pypi downloads <pkg>`) before recommending. Single number lies. Numbers in the shortlist, not adjectives.
