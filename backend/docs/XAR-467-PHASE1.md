# XAR-467 Phase 1 — feature standardization for batched HMM

Spun out from XAR-466 Phase 2 close-out: the GPU scan timings (4-6 min)
are valid, but the resulting posteriors collapse to a single active
state at B=7504. Root cause is feature-scale heterogeneity, not the
HMM or GPU code.

## Problem

Raw panel features span 5 orders of magnitude in std:

```
ret             std=0.06
mom_120d        std=0.27
range_20d       std=1.22       max=715
kurt_20d        std=1.53
amihud_20d      std=171        max=115129
vol_accel       std=93         max=84294
```

`amihud_20d = (|ret| / amount).rolling_mean(20)` blows up on low-volume
penny stocks. `vol_accel = vol_5d / (vol_20d + eps)` blows up when
vol_20d approx 0.

With shared cross-section params and diagonal Gaussian emissions, EM
log-likelihood is dominated by these two columns. The result: 99% of
(B*T) observations land in a single "near-zero" mode; the other 7
states starve.

## Fix (validated in `.tmp-port/bench_full_scan_v3.py`)

Two layers, both PIT-safe:

### Layer 1: log1p in panel builder (once, monotonic)

For non-negative heavy-tailed columns:

```python
LOG1P_COLS = (10, 12, 18)   # range_20d, amihud_20d, vol_accel
for c in LOG1P_COLS:
    panel[:, :, c] = np.log1p(np.clip(panel[:, :, c], 0.0, None))
```

This is Amihud 2002 standard practice for the illiquidity factor
and generalizes to any non-negative ratio with structural exponential
tail. log1p is PIT-safe (each cell maps independently, no temporal
leakage).

Std before / after:
```
range_20d   1.22 -> 0.07
amihud_20d  171  -> 0.05
vol_accel   93   -> 0.29
```

### Layer 2: expanding-window z-score per refit

In `expanding_fit_hmm_batched`, before each refit:

```python
def _normalize_features(panel, fit_end_idx):
    train = panel[:, :fit_end_idx, :].reshape(-1, D)
    mu = train.mean(axis=0)
    sigma = np.where(train.std(axis=0) < 1e-3, 1e-3, train.std(axis=0))
    z = (panel - mu) / sigma
    z = np.clip(z, -10, 10).astype(np.float32)
    z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
    z += jitter_rng.normal(0, 1e-3, size=z.shape).astype(np.float32)
    return z
```

- sigma floor 1e-3 (NOT 1e-8): tighter floor prevents post-clip values
  from creating zero-variance partitions inside EM, which trip
  pomegranate's "Variances must be positive" check.
- tiny Gaussian jitter (sigma=1e-3) breaks degenerate ties that
  collapse EM components.

PIT contract: mu/sigma computed only on `panel[:, :fit_end_idx, :]`.
Normalizing the out-of-sample slice with training stats is safe --
those stats carry no future information.

### Cold-init each refit (NOT warm-start)

Warm-start across refits causes catastrophic state collapse in this
panel shape. Verified:

- v3 with warm-start: refit 0 = 8/8 active, refit 1+ = 1/8 collapsed
- v3 with cold-init:  avg 6.4/8 active across 66 refits, 6 sporadic collapses

The cross-section multi-modal distribution lets EM converge to a
single dominant mode if the prior refit's params strongly biased it
that way. Cold init each refit forces the EM to find diversity from
scratch -- slower per refit but quality-preserving.

This contradicts the rescue-agent recommendation that warm-start has
"zero quality loss" -- in our setting it has total quality loss.

## Results

| Variant | Wall | n_active avg | State 5 mass |
|---|---:|---:|---:|
| v1 (no norm, expand) | 4m06s | 1.0/8 | 100% |
| v2 (no norm, T_max=1000, warm) | 3m05s | 1.0/8 | 100% |
| **v3 (norm, T_max=1000, cold)** | **6m02s** | **6.4/8** | spread 24/26/30/...% |

GPU peak memory: 5.2 GB across all variants with T_max=1000.

## Cost breakdown (v3, per refit avg)

```
fit:    2.65s    (EM forward-backward x 10 iter)
norm:   2.54s    (expanding z-score across train+era window)
python: ~0.3s
```

Norm cost is comparable to fit cost. Each refit recomputes mu/sigma
across `B * fit_end_idx * D` cells. Incremental Welford-style update
would cut this in half but is not blocking.

## Remaining gaps (NOT shipping with this doc)

1. **~9% collapse rate from bad cold-init seed.** 6 of 66 refits land
   in a degenerate local optimum where one state captures all
   observations. Production needs a seed-retry guard:
   `if n_active < 4: re-init with different seed and re-fit`.

2. **Incremental norm**: ~2.5s -> ~1s per refit via streaming
   mean/var update. Saves ~100s on full scan.

3. **Module wiring**: `_normalize_features` must be ported into
   `app/research/methodology/regime.py` and `expanding_fit_hmm_batched`,
   with optional `normalize=True` flag. Tests must be added. Panel
   builder must also apply the log1p transform.

4. **Ablation on T_max**: 1000 was picked without empirical justification.
   Sweep T_max=500/750/1000/1500 and pick where IC stabilizes (separate
   ticket).

## Next ticket scope: XAR-467 Phase 2

Production wire-up of all four gaps above. ~1-2 days estimate.

## Files (.tmp-port, gitignored)

- `bench_full_scan_v3.py` -- production-shape bench with log1p + expand z-score + cold-init
- `bench_full_scan_v3.log` -- run log (66 refits, 362.5s wall)
- `bench_full_scan_v3_results.json` -- raw per-refit metrics
