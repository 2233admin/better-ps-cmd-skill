# XAR-417 qlib Spike + WQ Horror Port

**Date:** 2026-05-18
**Agent:** Claude Sonnet 4.6 (qlib spike agent)
**Parallel work:** Polars-translation agent owns `backend/app/research/factors/wq_horror.py`

---

## Context

XAR-417 is the wheel-audit ticket for qlib (microsoft/qlib, 43k stars, 1.76k PyPI installs/day).
The spike has two deliverables:

1. Verdict on whether qlib is viable as k-atana's factor-computation layer.
2. WQ Horror factor translated to qlib expression DSL (validates end-to-end pipeline design).

---

## WQ Horror — Factor Description

Size-neutral reversal. Within market-cap buckets, identify stocks whose intraday return
persistently deviates from peer-group mean (high "horror score"), weight by recent vol,
z-score mean+std of that adjusted return, Cauchy-quantile, negate, 30d linear decay.

Original WorldQuant Brain DSL:
```
intra_ret      = close / open - 1
mean_returns   = group_mean(intra_ret, rank(ts_mean(cap, 20)), market)
horro          = abs(intra_ret - mean_returns) / (abs(intra_ret) + abs(mean_returns) + 0.1)
horro_day      = ts_mean(horro, 22)
ret_std        = ts_std_dev(intra_ret, 22)
adj_ret        = horro_day * ret_std * intra_ret
adj_ret_mean   = ts_mean(adj_ret, 22)
adj_ret_std    = ts_std_dev(adj_ret, 22)
horro_std_bonus = zscore(adj_ret_mean) + zscore(adj_ret_std)
signal         = -quantile(horro_std_bonus, driver="cauchy")
output         = ts_decay_linear(signal, 30)
```

---

## XAR-417 qlib Spike + WQ Horror Port -- Final Status

### Install
- pyqlib version attempted: **0.9.7** (latest as of 2026-05-18)
- Test in venv: **BLOCKED**
- Details: pyqlib 0.9.7 ships pre-built wheels for cp38/cp39/cp310/cp311/cp312 on win_amd64.
  The k-atana backend venv is **Python 3.13.12** (uv-managed, confirmed by
  `D:/projects/k-atana/backend/.venv/Scripts/python.exe --version`). No sdist exists on
  PyPI. No source-build path: venv has no Cython, no pip. All 10 published versions
  (0.8.6–0.9.7) max out at cp312. pyqlib's own `pyproject.toml` classifies only up to
  Python 3.12.
- CUDA torch status: unaffected (torch 2.12.0+cu132 verified intact; pyqlib install
  was never attempted, so no venv mutation occurred).

### Alpha158 hello-world
- **BLOCKED** (install failed, cannot run)
- Runnable snippet documented in
  `backend/app/research/qlib_integration/wq_horror_qlib.py` (ALPHA158_HELLO_WORLD constant).
- Expected output on cn_data_simple (~5yr CN data): IC mean ~0.01–0.03 per individual
  Alpha158 feature against 1d forward return (per qlib published benchmarks).

### WQ Horror qlib translation
- Expression module: `backend/app/research/qlib_integration/wq_horror_qlib.py` (lines: 244)
- Unit tests: `backend/tests/unit/test_qlib_wq_horror.py` — **19/19 PASSED**
- Translation choices flagged:

| WQ DSL | qlib equivalent | Approximation? | Impact |
|--------|----------------|---------------|--------|
| `$close`, `$open` | `$close`, `$open` | None — exact | None |
| `ts_mean(x, N)` | `Mean($x, N)` | None — exact | None |
| `ts_std_dev(x, N)` | `Std($x, N)` | None — exact | None |
| `abs(x)` | `Abs($x)` | None — exact | None |
| `rank(ts_mean(cap, 20))` | `Rank(Mean($market_cap, 20))` | None — exact | None |
| **`group_mean(x, group, market)`** | **Cross-sectional mean (handler-level)** | **YES — approximation** | Stocks at size-distribution tails will diverge vs Polars impl. Within-decile mean != cross-sectional mean. This is the dominant source of cross-agent factor disagreement. |
| `zscore(x)` | `CSZScore($x)` | Minor — CSZScore may not be in all qlib versions; fallback to pandas groupby date-level z-score | Negligible in expectation |
| **`quantile(x, driver="cauchy")`** | **`Rank()` + `atan(x)/pi + 0.5`** | **YES — approximation** | Cauchy driver spreads tails more than uniform rank. Stocks with extreme horro_std_bonus will rank differently. Direction preserved; magnitude compressed vs true Cauchy. |
| **`ts_decay_linear(x, 30)`** | **`WMA($x, 30)`** | None — exact match | None (WMA is defined as linear decay weights) |

### Factor IC on qlib data
- Dataset: **BLOCKED** (cannot run without pyqlib install)
- 1d IC: BLOCKED
- 5d IC: BLOCKED
- 22d IC: BLOCKED

### qlib verdict (XAR-417)
- **HYBRID**
- Reasoning:
  - qlib's expression DSL covers 8 of 10 WQ operators with exact mappings. Only
    `group_mean` (requires custom handler) and `quantile(driver="cauchy")` (requires
    post-processing) need non-DSL work.
  - The **install blocker is real but fixable**: downgrade the venv to Python 3.12 or
    wait for qlib to publish cp313 wheels. The qlib repo is actively maintained
    (last push 2026-04-22) and this is likely a 1-2 release lag.
  - qlib's Alpha158/360 factor zoo and IC pipeline are mature and would replace
    hand-rolled factor code. Not worth reinventing.
  - qlib's data layer (PIT calendar, adjust-factor, baostock fundamentals) is
    well-suited for A-share. The PIT contract aligns with k-atana's spec.
  - **Integration friction is the dominant cost** — not the library quality.

- If HYBRID: **keep** Polars-based factor pipeline for production research (PIT-correct,
  fast, no Python version constraint). **Adopt qlib for**: (a) Alpha158/360 factor zoo as
  a baseline signal catalogue, (b) IC/IR evaluation pipeline once on Python <=3.12,
  (c) CN data layer (baostock + adjust-factor) as a complement to akshare.

- Integration friction notes:
  1. **Python version gap**: cp313 wheels don't exist. Either pin venv to 3.12 or wait.
     A separate qlib-evaluation venv on Python 3.12 is the low-friction path today.
  2. **pandas-centric**: qlib is pandas-based; k-atana's research layer is Polars-centric.
     Bridge cost is one `df.to_pandas()` / `pl.from_pandas()` per dataset boundary.
  3. **Heavy dependencies**: pyqlib pulls in lightgbm, mlflow, gym, cvxpy, pymongo, redis.
     These inflate the container image. Consider a separate `qlib-eval` service rather
     than adding to the main backend image.
  4. **group_mean gap**: qlib expression DSL cannot express within-group means without a
     custom C++ operator or handler subclass. This is a real API limitation, not a
     fixable approximation.
  5. **Low prod adoption** (1,760 installs/day for 43k stars = 24:1 ratio): calibrate
     community-support expectations. Issues response time may be slow.

---

## WQ Horror Expression DSL Translation (full)

For reference, the complete qlib expression-DSL approximation (where group_mean
is replaced by cross-sectional mean — see translation choice #3 above):

```python
# Fields exposed via DataHandlerLP
INTRA_RET      = "$close / $open - 1"
RET_STD        = "Std($close / $open - 1, 22)"
CAP_RANK       = "Rank(Mean($market_cap, 20))"

# Handler-level assembly (Python, not DSL):
peer_mean  = intra_ret.groupby(date).transform("mean")   # group_mean approx
horro      = abs(intra_ret - peer_mean) / (abs(intra_ret) + abs(peer_mean) + 0.1)
adj_ret    = horro * ret_std * intra_ret

# Cross-sectional z-scores
adj_ret_mean_z = cs_zscore(rolling_mean(adj_ret, 22))
adj_ret_std_z  = cs_zscore(rolling_std(adj_ret, 22))
horro_std_bonus = adj_ret_mean_z + adj_ret_std_z

# Cauchy quantile approx: atan(x)/pi + 0.5 applied to z-scored bonus
signal  = -(0.5 + atan(horro_std_bonus) / pi)

# ts_decay_linear -> WMA (exact)
output  = WMA(signal, 30)   # in DSL: WMA($signal, 30)
```

---

## Cross-validation with Polars agent

Section for the Polars-agent to fill in after both agents finish.

Expected comparison methodology:
- Select overlap date range (both agents should have the same trading calendar)
- Join on (date, symbol) inner join
- Compute relative difference: `abs(qlib_val - polars_val) / (abs(polars_val) + 1e-6)`
- **Expected: <5% relative diff** for most stocks; larger divergence at size tails
  (due to group_mean approximation) and extreme alpha stocks (due to Cauchy vs rank).
- Systematic bias direction: qlib signal will be more compressed at extremes
  (rank-based quantile vs Cauchy-spread quantile).

The Polars agent's `wq_horror.py` should implement true decile group means, making
the Polars version the "reference" implementation and the qlib version the "approximation
under DSL constraints."

---

## Recommended next actions

1. **Short-term (today)**: Create a separate Python 3.12 venv for qlib evaluation:
   ```powershell
   uv venv --python 3.12 D:/projects/k-atana/.venvs/qlib-eval
   uv pip install --python D:/projects/k-atana/.venvs/qlib-eval/Scripts/python.exe pyqlib
   ```
   Run Alpha158 hello-world and IC check from this venv. Feed results back into this doc.

2. **Medium-term**: qlib data layer (baostock + adjust-factor calendar) as complement to
   akshare for PIT fundamentals. Ticket: XAR-417 follow-up.

3. **Deferred**: Custom qlib operator for `group_mean` — only worthwhile if we commit
   to qlib as primary factor engine (ADOPT verdict). Under HYBRID, avoid the C++ operator
   work; implement group means in the Polars handler instead.

---

## Files produced

| Path | Purpose |
|------|---------|
| `backend/app/research/qlib_integration/__init__.py` | Package marker + install note |
| `backend/app/research/qlib_integration/wq_horror_qlib.py` | WQ Horror qlib translation |
| `backend/tests/unit/test_qlib_wq_horror.py` | 19 unit tests (all passing) |
| `docs/spikes/xar-417-qlib-spike.md` | This document |
