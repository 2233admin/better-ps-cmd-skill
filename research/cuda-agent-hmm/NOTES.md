# cuda-agent-hmm — XAR-468 workspace

In-tree research workspace for batched HMM forward-backward GPU kernel.
"会有能复用" — kept in main repo so kernel + bindings are reusable.

## Layout

- `triton/` — Triton FB kernel prototype + numeric parity tests
- `cuda/` — hand-tuned CUDA C++ (produced by Codex via CUDA-Agent loop)
- `rust/` — cudarc orchestrator crate (multi-stream refit, pyo3 bindings)

## Status

Phase 0a — workspace skeleton created (this commit).

## Pre-flight gate — wheel-search result (2026-05-20)

Full report: `WHEEL-SEARCH.md`. Verdict:

```
RECOMMENDED INTEGRATION: none-fit
FALLBACK: self-write
```

- `dynamax` is the only schema-fit candidate (DiagonalGaussianHMM + batched fit_em on (B,T,D))
- **Hard blocker: JAX has no Windows GPU wheels** (Linux-only, WSL2 experimental)
- Everything else stale 5-8 yr / CPU-only / wrong problem class (Mamba = continuous SSM)
- pomegranate v1.1.2 is the incumbent we're trying to beat (2.6s/refit baseline)

## **Cheap-probe lever before kernel sprint** (~1hr)

WHEEL-SEARCH surfaced a contradiction with our v2 conclusion.

v2 bench note (XAR-466-PHASE2.md L65-68): "pomegranate's `fit()` runs `max_iter` EM
steps regardless of tolerance convergence on subsequent refits".

Wheel-search source inspection of `pomegranate/hmm/_base.py` EM loop:
```python
for i in range(self.max_iter):
    ...
    if improvement < self.tol:
        self._reset_cache()
        return self
```
**The short-circuit IS there.** It fires on warm-start (params barely change -> tiny
improvement on iter 1). No open upstream bug. v2's "warm-start has zero speedup"
finding may have been a `tol` config issue, not a pomegranate bug.

If warm-start in v1 actually converges in 1-2 iters per refit with proper tol ->
**< 1s per refit achievable without writing any new kernel**. Cost of probe (1hr)
<< cost of self-write (1-2 weeks). Per `cheap-probe-evidence-generation` rule, run
the probe before committing.

## Probe result (2026-05-20)

Script: `.tmp-port/bench_warmstart_tol_probe.py` -- 3 tol values x 12 refits each
with v3 normalization + warm-start. JSON at `.tmp-port/bench_warmstart_tol_probe_results.json`.

```
     tol   avg_fit_ms   em_iters_warm   n_active   verdict
  1e-04         1986           11.00       1.58    quality degrades
  1e-03         1978           11.00       1.58    quality degrades
  5e-03         1974           11.00       1.58    quality degrades
```

Two findings:

1. **Short-circuit never fires in our panel shape.** Every refit runs the full
   max_iter=10 EM iters (verbose counter reads 11 = 10 internal + 1 final).
   Wall time identical across tol in {1e-4, 1e-3, 5e-3}. The `if improvement < tol`
   line in `_base.py:641` is in the source but `improvement` is consistently
   above 5e-3 even on warm-start. v2 finding confirmed.

2. **Warm-start collapses quality.** Refit 0 (cold-init) = 8/8 active. Refit 1+
   (warm-start) = 1/8 active **every single refit** across all 3 tol values.
   Same pattern v2 hit. Cold-init each refit is the right path; v3's design holds.

## Decision: kernel work justified

Probe killed the easy lever. To beat 2.6s/refit we need different compute.
Lane C (regime.py production port) shipped with cold-init + seed-retry.
Lane B (kernel sprint) is unblocked.

Options for lane B going forward:

| path | wall target | effort | risk |
|---|---|---|---|
| Triton FB kernel in PyTorch | < 1s/refit | 3-5 days | medium (kernel debugging) |
| Triton + Rust+cudarc orchestrator | < 1s/refit, multi-stream parallel refit | 1-2 wk | high (PTX/cudarc + pyo3 stack on Windows) |
| WSL2 + dynamax (JAX) | unknown, probably < 2s/refit | 2-3 days | medium (cross-OS compute lane in Windows project) |
| do nothing, ship regime.py as-is | 2.6s/refit baseline | 0 | quality work covered, speed deferred |

## Targets (Curry confirmed 2026-05-20: C + B)

Two independent lanes, can ship in parallel:

### Lane C — quality (collapse 治理)

NOT addressed by kernel speed. Owned by task #692.

- 9% bad-seed collapse rate (6/66 refits in v3)
- Fix: seed-retry guard (if n_active < 4 -> re-init w/ different seed, retry up to N times)
- Stretch: k-means++ on flattened panel for mu init, EM plateau early-stop
- Lives in `regime.py` production path, separate from XAR-468

### Lane B — daily prod speed

Critique premise "现在算子不多 不代表就不做了":
- regime is ONE of many algo families that will run daily
- daily refit cost = 2.6s x N families (compounds as algo zoo grows)
- backtest grid amortization (full scan / 6min) also matters

Wheel-search agent (background) evaluates: dynamax (JAX), pyro-ppl, mamba-ssm,
torch HMM candidates. Source of truth: gh API live numbers.

If dynamax (or other wheel) covers batched-GPU diag-Gauss HMM with EM and
acceptable Windows install path -> integrate, skip kernel sprint.
If nothing fits -> Triton + Rust + CUDA-Agent loop proceeds.

Per-refit wall-clock target: **< 1s** (vs 2.6s pomegranate).
Full-scan target: **< 1 min** (vs 6m02s pomegranate).
