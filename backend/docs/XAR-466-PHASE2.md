# XAR-466 Phase 2 — full-universe HMM scan benchmark

## TL;DR

GPU batched HMM scan over **B=7504 全 A 股, T=1889 (2018-2026), D=20, K=8, refit_every=21**:

| Variant | Wall | GPU Peak | vs v1 |
|---|---:|---:|---:|
| **v1** (expanding window, cold init) | **245.8s = 4m06s** | 9.66 GB | 1.00x |
| **v2** (T_max=1000 sliding + warm-start) | **185.3s = 3m05s** | 5.19 GB | 1.33x |
| CPU projection (hmmlearn x B sequential) | ~3473s = 58 min | -- | -- |
| GPU vs CPU speedup | -- | -- | **14-19x** |

Engineering decision: **pomegranate stays**. Rust / Triton kernel rewrite is NOT triggered.

Trigger condition for rewrite (per Curry's "真的影响效率的换 rust" rule):
- B > 5000 plateaus past current numbers, OR
- daily refit latency drops below 100ms requirement.

Neither condition met. 3-4 minute full-universe scan is firmly in the "加速" range.

## Configuration

- panel: `(7504, 1889, 20)` float32, 1.08 GB raw
- HMM: pomegranate v1.1.2 DenseHMM, **diagonal covariance**
  (full-cov went non-PSD on forward-filled rows -- diag is more robust and faster)
- min_train=504, refit_every=21, max_iter=10, tol=1e-4
- GPU: RTX 5090 (cu132, sm_120) Blackwell
- features: ret, vol_5d/20d/60d, mom_5d/20d/60d/120d, dd_20d/60d, range_20d, c2h_20d,
  amihud_20d, turn_z_20d, vol_log_change, open_to_close_ret, skew_20d, kurt_20d,
  vol_accel, hl_log_range
- training calendar: 2018-01-02 -> 2026-04-14, 2009 trading days
- mask density: 85.8% real data, 14.2% forward-filled (early-life stock prefixes)

## v1 per-refit cost (linear in T_train)

| refit | T_train | fit_ms | fwd_ms | peak_MB |
|---:|---:|---:|---:|---:|
| 0  | 504  | 1654 |  68 | 2630 |
| 10 | 714  | 2034 |  86 | 3712 |
| 30 | 1134 | 3407 | 164 | 5877 |
| 50 | 1554 | 4377 | 176 | 8041 |
| 60 | 1764 | 4752 | 212 | 9122 |

90.3% of wall is `model.fit()` (10 EM iter forward-backward).
4.0% forward (era posterior). 5.7% python overhead.

Per-refit fit time scales linearly with T_train -- expected for FB pass O(B*T*K^2).

## v2 deltas

1. **T_max=1000 sliding cap** -- training window stops growing at 1000 days.
   Per-refit cost flattens at ~2.8s (refit 30 onward).
   GPU memory peaks at 5.2 GB (vs 9.7 GB in v1, since X_train is bounded).
   Future-proof: in 2027 with T=2200, scan still takes ~3 min, not ~5 min.

2. **warm-start across refits** -- model object persists, prior refit's
   means/covs/edges/starts seed the next EM. Implemented by re-using the
   same `DenseHMM` instance across calls (not by constructor re-injection,
   since pomegranate Normal lazy-builds internal caches on first fit and
   cannot be re-built from numpy means/covs alone).

   Measured speedup is only ~10% beyond T_max alone, because pomegranate's
   `fit()` runs `max_iter` EM steps regardless of tolerance convergence on
   subsequent refits (warm-start in pomegranate v1 does not short-circuit
   max_iter the way hmmlearn does). To extract the full warm-start benefit,
   max_iter must also be reduced post-warmup -- left for a follow-up.

3. **state-freq KL drift guard** -- after each refit, compare posterior
   argmax distribution vs prior refit. If KL > 0.30, log warning (would
   trigger cold-restart in prod). 0/66 warnings fired in this run.

## **Known issue: feature-scale collapse (NOT a XAR-466 blocker)**

Posterior quality degenerated to a single active state across all refits.
Root cause is a panel builder bug, NOT the HMM or GPU code:

```
Feature std spread (panel-wide):
  ret             std=0.06    range=[-7.88, +8.37]
  mom_120d        std=0.27
  range_20d       std=1.22    range=[0, 715]          <- outlier
  kurt_20d        std=1.53    range=[-2.10, +13.29]
  amihud_20d      std=171     range=[0, 115129]       <- catastrophic outlier
  vol_accel       std=93      range=[0, 84293]        <- catastrophic outlier
```

`amihud_20d = |ret| / amount` blows up on low-volume penny stocks.
`vol_accel = vol_5d / vol_20d` blows up when vol_20d approx 0.

With shared params across B=7504 series, diagonal-cov emissions are
dominated by these two columns. EM lumps 99% of (B*T) observations into
one state (the "near-zero" mode); the other 7 states starve.

**This degeneracy occurs in BOTH v1 and v2** -- not caused by warm-start
or window cap. It is a feature engineering issue carried in from
`build_universe_panel_full.py` and would happen identically on CPU.

The benchmark **wall-time numbers are still valid** -- GPU EM and FB
ran the full computation. But the resulting posteriors are not usable
for actual regime labelling until features are standardized.

Follow-up ticket (TBD: XAR-467 candidate):
- Winsorize amihud_20d and vol_accel to p99/p1 in the panel builder, OR
- Drop those two columns (D=20 -> 18), OR
- Apply z-score normalization per-feature on the cross-section before HMM.

## Decision: close XAR-466 Phase 2

Speed contract met. Quality is downstream of feature engineering -- the
GPU batched scan ran correctly, gave us a measured speedup envelope,
and proved the pomegranate path stays viable until B*K*T grows another
order of magnitude. Feature standardization is a separate concern and
should be tracked as its own piece of work.

## Files

- `.tmp-port/build_universe_panel_full.py` -- B=7504 panel builder (DATE_FLOOR=2018)
- `.tmp-port/universe_panel_full.npz` -- (7504, 1889, 20) panel + mask
- `.tmp-port/bench_full_scan.py` -- v1 bench (expanding, cold init)
- `.tmp-port/bench_full_scan_v2.py` -- v2 bench (T_max=1000, warm-start)
- `.tmp-port/bench_full_scan_results.json` -- v1 raw results
- `.tmp-port/bench_full_scan_v2_results.json` -- v2 raw results
- `app/research/methodology/regime.py` -- production module (docstring already updated with B=1500 numbers in prior commit 859c829)
