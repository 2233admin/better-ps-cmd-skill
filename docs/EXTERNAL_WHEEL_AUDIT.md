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
| Technical indicators | `backend/app/data/indicators.py` | **TA-Lib/ta-lib-python** | 11,964 | 2026-03-16 | **22,455** | **HIGH PRIORITY** — by far highest install rate in the entire audit; we almost certainly should be importing this instead |
| Portfolio optimization | (not built) | **robertmartin8/PyPortfolioOpt** (efficient frontier, Black-Litterman, HRP) | 5,724 | 2026-04-20 | 5,775 | **NEW gap surfaced** — we have no portfolio optimization layer; this is the standard. Riskfolio-Lib (4.2k★ / 3.2k/day) is the risk-focused alternative |
| Quant terminal (overlaps k-atana scope) | k-atana itself | **OpenBB-finance/OpenBB** | 67,724 | 2026-05-18 | 2,474 | **strategic question** — OpenBB explicitly targets "AI agents" and is the largest finance-OSS project. Worth a hard look at whether k-atana's frontend should integrate vs reinvent |
| Agent-based trading framework (2026 trend) | — | **HKUDS/Vibe-Trading** | 7,531 | 2026-05-17 | (no PyPI release) | **2026 trend signal** — Hong Kong U Data Science Lab. Worth tracking before building anything in `app/ai_lab/trading/` |
| Multi-agent LLM trading firm (top 2026 pick) | — | **TauricResearch/TradingAgents** | 76,614 | 2026-05-17 | 302 | **HIGH PRIORITY** — biggest 2026 agent-trading project (3x Vibe-Trading stars), Apache-2.0, simulates a full trading firm (fundamental/sentiment/technical/researcher/trader/risk/PM agents). PyPI install rate low because most users clone + run via UI. |
| Agent-trading sibling | — | **HKUDS/AI-Trader** | 17,934 | 2026-05-13 | n/a | Same lab as Vibe-Trading; full agent execution layer. Track alongside XAR-422. |
| Crypto unified exchange API | `backend/app/data/okx_client.py` | **ccxt/ccxt** | 42,488 | 2026-05-17 | **116,618** | **HIGHEST install rate in entire audit.** MIT. 100+ exchanges. Our hand-rolled OKX client should almost certainly become `ccxt.okx()`. |
| Crypto official OKX SDK | `backend/app/data/okx_client.py` | **okxapi/python-okx** | (small repo) | active | **3,074** | Official OKX v5 endpoints (funding-rate-history, open-interest, mark-price) — exactly what we need for the crypto funding/OI PIT gap. Use alongside ccxt: ccxt for portability, python-okx for OKX-specific PIT depth. |
| Crypto backtest (permissive) | — | **jesse-ai/jesse** | 7,902 | 2026-05-16 | 446 | MIT — picked over freqtrade (50k★ but GPL-3.0, distribution-incompatible with k-atana). |
| Crypto historical funding/OI bulk dump | (gap) | **okx-dump** (PyPI) | n/a | active | 21 | Niche but exactly fills crypto funding/open-interest/mark-price PIT gap. Worth a spike. |

## DO NOT recommend (graveyard)

| Library | Why |
|---|---|
| **waditu/tushare** | 14,979★ + **16,983 installs/day** but last push 2024-03-13 — 14 months dead. High install count is users paying for tushare.pro binaries (commercial), not OSS health. Community moved to akshare (70k/day). |
| **quantrocket-llc/empyrical** | 1,479★ + 509 installs/day but last push 2024-07-26 — 22 months dead. Bigger star count than `empyrical-reloaded` (107★) but ~10x fewer installs than quantstats (8,442/day). Skip both. |
| **quantopian/empyrical** | Original, archived; do not depend on it directly. |
| **quantopian/pyfolio** | Original, archived. Use `pyfolio-reloaded` if you must. |
| **quantopian/zipline** | Original archived; use `stefan-jansen/zipline-reloaded` (US-equity focused, no live trading — wrong fit for A-share). |
| **hudson-and-thames/mlfinlab** | 4,743★ but **0 installs/day** (literally zero in last day, 7 last month) + last push 2023-10. Implementations from López de Prado's *Advances in Financial ML*. Pure star bait — the book sells, the library is abandoned. Re-implement the chapters you need from the book, don't depend on this. |
| **mementum/backtrader** | 21,580★ + 8,714 installs/day but **last push 2024-08-19 (21 months dead)**. Same pattern as tushare: huge install count from inertia (every retail tutorial uses it), but zero maintenance. New projects: vectorbt (14k/day, active) or rqalpha (CN-native). Existing users: fork or migrate. |
| **freqtrade/freqtrade** | 50,460★ + 2,439 installs/day + actively maintained but **GPL-3.0** — copyleft license incompatible with distributing k-atana (would force open-sourcing trading code). Use **jesse-ai/jesse** (MIT, 446/day) or **hummingbot** (Apache-2.0, 65/day) instead. Library is technically not dead, but functionally graveyarded for our license profile. |

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
- **XAR-419** — TA-Lib swap for hand-rolled `data/indicators.py`, priority Medium
- **XAR-420** — PyPortfolioOpt to fill portfolio-optimization gap, priority Medium
- **XAR-421** — Strategic OpenBB overlap evaluation (terminal scope question), priority High
- **XAR-422** — Track HKUDS/Vibe-Trading before building `app/ai_lab/trading/`, priority Low
- **XAR-423** — ccxt + python-okx replace `data/okx_client.py` (also closes crypto funding/OI PIT gap), priority Urgent — **SPIKE DONE (`a8c18b8`), VERDICT ADOPT**
- **XAR-424** — Evaluate TauricResearch/TradingAgents (76k★) before designing `ai_lab/trading/`, priority High
- **XAR-425** — XAR-421 follow-up: OpenBB AGPLv3 license posture decision, priority High
- **XAR-426** — Crypto rollout: ship the ccxt adapter, wire PIT backfill into ingest, retire `okx_client.py` (XAR-423 follow-up), priority Urgent
- **XAR-427** — qlib spike unblock via py3.12 sub-venv (XAR-417 partial via `b190460` shows HYBRID; install was blocked on cp313), priority High
- **XAR-428** — Data engineering: ingest A-share OHLCV + add `cap` field (unblocks Polars factor research, surfaced by `b94b38b` BLOCKED probe), priority High
- **XAR-429** — Evaluate WonderTrader/wtpy + document CN quant ecosystem taxonomy (heavily-wrapped vs details-exposed), priority Medium

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

**Fourth-pass lesson (added 2026-05-18 late evening):** even after triangulation, the **original audit was scoped too narrowly** — I evaluated wheels for the subsystems we already had, not wheels for things we *should* have but don't. Re-ran with `gh search repos --topic quantitative-finance --sort stars` and surfaced 4 major omissions:

- **TA-Lib** — 22,455 installs/day, by far the highest in the entire audit. We have `data/indicators.py` hand-rolled. Should almost certainly be ta-lib.
- **PyPortfolioOpt** — 5,775 installs/day, MIT, active. We have **no portfolio optimization layer at all**. This is a gap we didn't know to look for.
- **OpenBB** — 67,724★, pushed today. Explicitly targets AI agents. This is the largest finance-OSS project on GitHub and it overlaps with k-atana's terminal scope — strategic question, not just a wheel pick.
- **Vibe-Trading (HKUDS)** — 2026 agent-trading trend, very recent. Track before building `app/ai_lab/trading/`.

Also caught two more graveyard inhabitants via install rate:
- **mlfinlab** — 4.7k★ but **0 installs/day** + 2.5yr dead. Star bait based on López de Prado's book.
- **backtrader** — 21k★ + 8.7k installs/day but **21 months dead**. Inertia + tutorial recommendations keep installs high.

**Process update:** start with broad GitHub topic search (`gh search repos --topic <domain> --sort stars --limit 25`), not "wheels for the modules I already wrote." The latter misses the strategic-overlap candidates (OpenBB) and the gaps you didn't know existed (PyPortfolioOpt).

**Fifth-pass lesson (added 2026-05-18, after Grok second opinion):** the 4th pass was still A-share / generic quant heavy and missed two whole categories:

1. **Crypto side** — only had OKX REST client hand-rolled. Broad `gh search repos --topic cryptocurrency-exchanges --sort stars` + Grok consultation surfaced:
   - **ccxt** 42k★ + **116,618 installs/day** (highest of any wheel in any pass), MIT, 100+ exchanges. Our `data/okx_client.py` should be `ccxt.okx()`.
   - **python-okx** 3,074/day, official OKX v5 SDK with `/funding-rate-history`, `/open-interest`, `/mark-price` endpoints — fills the crypto funding/OI PIT gap from the original Wave-5 plan.
   - **jesse-ai/jesse** MIT (vs freqtrade GPL-3.0 which would force open-sourcing trading code).
   - **okx-dump** bulk historical downloader for OKX (low installs but exactly the PIT need).

2. **Agent-trading frontier** — missed the biggest one:
   - **TauricResearch/TradingAgents** — 76,614★ + 302/day + Apache-2.0 + pushed yesterday. 3x Vibe-Trading's stars. Simulates a full trading firm with specialist agents. This is the 2026 cohort flagship and I had zero awareness.

**Process lesson #2:** I evaluated ecosystem with internal-knowledge searches even after switching to live API data. Asking Grok (xAI, different training data + browse access) for "what am I missing on the 2026 frontier" surfaced TradingAgents (76k★ — should have been impossible to miss) and confirmed AGPL risk on OpenBB. Independent-model second opinion is cheap and catches systematic blind spots.

**Process lesson #3 — license is a signal that beats install rate:** freqtrade (GPL-3.0, 50k★, 2.4k/day) is technically active and popular, but practically graveyarded for any project that might be distributed. Same for OpenBB (AGPLv3 — *network* copyleft, even harder than GPL). Pin license in the triangulation, not just stars/push/installs.

### CN A-share quant ecosystem taxonomy (added 2026-05-18 evening, Curry-surfaced)

The CN quant trading platform landscape splits along an **abstraction axis**. Know which side a tool sits on before evaluating:

**高度封装 (heavily wrapped)** — focus on `handle_bar` strategy logic, low-frequency market data:
- **QMT / xtquant** (迅投) — docs: http://docs.thinktrader.net/. Used by k-atana's EasyXT bridge (see XAR-414, ACCOUNT_READER_MATRIX.md)
- **PTrade** (中泰)
- **掘金** (myquant)
- **聚宽** (joinquant)

Pros: fast to start, less boilerplate. Cons: less control over order callback states.

**细节暴露 (details exposed)** — manage order callback states explicitly, high-frequency market data:
- **CTP / CTP-mini** (上期所标准 futures API)
- **华鑫奇点** (htsec single-point — equity HFT)
- **中泰 XTP** (zhongtai XTP — equity HFT)

Pros: full control + microstructure access. Cons: every callback needs state-machine handling.

**Where k-atana sits:** EasyXT/QMT (heavily-wrapped side) for execution; research layer is PIT-correct + broker-neutral; potential future migration to details-exposed when HFT signals justify the complexity (per XAR-413 / XAR-418 strategic eval).

**Cross-style wheels** worth tracking:
- **WonderTrader/wondertrader** (C++ core, 6,080★, MIT, 2025-09-30 last push — semi-active/cooling) + **wtpy** (Python wrapper, 1,472★, 6 PyPI/day) — straddles both styles. Tracked in XAR-429.

### Ecosystem signal: OKX Agent Trade Kit (added 2026-05-18 evening)

OKX shipped an official `okx-trade-mcp` + `okx-trade-cli` toolkit (https://www.okx.com/docs-v5/agent_zh/) explicitly for LLM agents (Claude / Cursor / VS Code). JavaScript only — no Python equivalent. Three implications:

1. **XAR-423 unchanged:** still ccxt + python-okx for our Python stack.
2. **XAR-413 validated:** OKX's four-layer safety (demo / read-only / permission / fund-operation caution tags) is the same pattern as our EasyXT live_test promotion gate. Independent confirmation our architecture is right.
3. **XAR-424 gains a primary reference:** exchange-side endorsement of agent-trading direction. When designing `app/ai_lab/trading/`, the OKX kit's safety architecture is what "OKX officially considers safe for an LLM to call." Borrow patterns, don't import.
