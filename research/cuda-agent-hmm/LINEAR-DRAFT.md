# Linear draft -- XAR-468: GPU HMM kernel sprint (direct CUDA C++ via CUDA-Agent)

Team: **XAR**  
Priority: **High** (regime is one of many algo families; daily prod refit cost compounds)  
Estimate: **1-2 weeks**  
Status: **In Progress**  
Parent / blocks: blocks downstream factor framework consumers of `expanding_fit_hmm_batched`

## Title

XAR-468: GPU HMM forward-backward kernel (direct CUDA C++, drop-in replace pomegranate)

## Background

Pomegranate v1.1.2 is the current batched HMM runtime. Validated 2026-05-20:

- per-refit fit time: **2.6s** (B=7504 T=1000 D=20 K=8 diag-Gauss, RTX 5090 cu132 sm_120)
- full 66-refit walk-forward scan: **6m02s**
- GPU utilization: **<0.1% of theoretical FP32** (~100 TFLOPS on 5090)
- pomegranate per-sequence B-loop inside `summarize` is not fused into a single (B,T,K,D) kernel — see `pomegranate/hmm/_base.py:626`

Daily prod refit cost = 2.6s × N algo families (regime is just one). Backtest grid amortization (100 configs × 6min = 10hr) also matters. Squeezing GPU is required.

## Wheel-search verdict (2026-05-20)

Full report in repo at `research/cuda-agent-hmm/WHEEL-SEARCH.md`. gh-API-verified candidate matrix:

- `probml/dynamax` — schema fit (DiagonalGaussianHMM batched fit_em on (B,T,D)) but **JAX has no Windows GPU wheels**, Linux-only, WSL2 experimental
- pyro/numpyro HMM dist-only (no `.fit()`), SVI rewrite exceeds self-write
- all torch-HMM repos stale 5-8 years
- pomegranate v1 = incumbent we're beating
- mamba = continuous SSM, not discrete K HMM

**Conclusion: self-write CUDA C++ via CUDA-Agent loop.**

## Cheap probe verdict (2026-05-20)

Script: `.tmp-port/bench_warmstart_tol_probe.py` (gitignored). Warm-start + tol sweep {1e-4, 1e-3, 5e-3} on production-shape panel:

| tol | avg_fit | em_iters/refit | n_active | verdict |
|---|---:|---:|---:|---|
| 1e-4 | 1986ms | 11 | 1.58/8 | quality degrades |
| 1e-3 | 1978ms | 11 | 1.58/8 | quality degrades |
| 5e-3 | 1974ms | 11 | 1.58/8 | quality degrades |

- short-circuit on `improvement < tol` IS in source (`pomegranate/hmm/_base.py:641`) but **never fires** in our panel shape (improvement always > 5e-3 even on warm-start)
- warm-start collapses quality (refit 0 cold = 8/8 active, refit 1+ warm = 1/8 active) -- v2 finding confirmed
- 1-2 wk kernel sprint is the only path. probe killed the easy lever before sunk-cost set in

## Codex design memo (2026-05-20)

Full memo in `research/cuda-agent-hmm/CODEX-DESIGN-MEMO.md`. Key decisions:

- **layout**: (B, T, K) row-major, K=8 innermost (coalesced 128-byte loads)
- **three separate kernels**: emission compute / FB recurrence / M-step
- **fuse backward + gamma extraction** into one pass over T (saves one full (B,T,K) read-back)
- **log-space throughout** (Rabiner scaling not needed at K=8 T=1000 fp32)
- **shared memory** for K*K=8*8 transition matrix (256 bytes, trivial)
- **expose `fit()` and `filter()` separately** — daily prod refit doesn't need backward, free 40-50% speedup
- **multi-stream** parallelism for hyperparam grid (one cudaStream per HMM instance; 5090 has 170 SMs, HMM is bandwidth-bound so SM time-slicing is fine)
- **NOT useful**: tensor cores (K=8 < WMMA 16x16 tile), HMM-on-attention (continuous-score vs discrete state)

## Decision: skip Triton, direct CUDA C++

Originally planned: Triton FB kernel (Python) → Rust+cudarc → CUDA-Agent loop. **Triton 3.x sm_120 support is uncertain as of early 2026** (Codex memo); on Windows cu132 it's unproven territory.

Rationale for direct CUDA C++:
1. CUDA-Agent's final output is hand-tuned CUDA C++ anyway; Triton would be discarded
2. Removes one moving part (sm_120 Triton maturity = unknown)
3. hmmlearn CPU oracle is enough verification contract for CUDA-Agent to iterate against

## Missing alternative flagged

**Parallel-prefix scan (Blelloch) over T** for forward pass: O(T) serial → O(log T) parallel. Worth evaluating after baseline lands. K=8 makes the associative operator (log-semiring matrix-vector) cheap. Blackwell has strong prefix-sum hardware.

## Phase breakdown

### Phase 0a (this ticket if split, otherwise inline): hmmlearn CPU oracle + LL parity fixture
- synthetic panel B=64 T=200 K=4 D=5 diag-Gauss
- per-series hmmlearn fit, lock LL + means/vars/transmat to fixture file
- tolerance: ≤1e-5 LL delta target for CUDA kernel verification
- output: `research/cuda-agent-hmm/oracle/build_oracle.py` + `oracle.npz`

### Phase 0b: CUDA-Agent workspace + first CUDA C++ FB kernel
- clone `BytedTsinghua-SIA/CUDA-Agent` template into `research/cuda-agent-hmm/cuda-agent/`
- adapt SKILL.md context to HMM forward-backward + diag-Gauss EM
- point verification.py at Phase 0a oracle
- Codex drives CUDA-Agent loop (generate → compile → verify → profile → iterate) until kernel passes ≤1e-5 LL parity
- output: `research/cuda-agent-hmm/cuda/hmm_fb.cu` + nvcc-compiled .so/.pyd via cudarc

### Phase 1: Rust+cudarc orchestrator + regime.py wire-in
- `research/cuda-agent-hmm/rust/hmm-engine/` crate loads .ptx via cudarc
- pyo3 exposes `fit()` + `filter()`
- multi-stream parallel refit (one stream per HMM-instance for hyperparam grid)
- replace pomegranate inside `regime.expanding_fit_hmm_batched`
- parity test `backend/tests/methodology/test_regime_batched_cuda.py`:
  - ≤1e-3 LL delta on real-shape panel (relaxed from kernel ≤1e-5 due to float accumulation)
  - ≤2% drift in n_active across 10 refits
  - per-refit wall < 1s target

### Phase 2 (stretch): parallel-prefix scan over T
- only after baseline lands and passes parity
- O(log T) forward scan via Blelloch
- additional 5-10x ceiling on top of naive batched FB

## Already done (this ticket)

- XAR-467 P2 production port shipped: `backend/app/research/methodology/regime.py` `expanding_fit_hmm_batched` now has log1p + expanding z-score + diag cov + min_cov + spread init + jitter + seed-retry. 9 existing tests pass. Commit 600deb0.
- Research workspace skeleton at `research/cuda-agent-hmm/` (NOTES.md, WHEEL-SEARCH.md, CODEX-DESIGN-MEMO.md).

## Out of scope

- Triton FB kernel (intentionally skipped per direct-CUDA-C++ decision)
- WSL2 + dynamax (alternative path, deferred — would need new Linux compute lane in Windows-native k-atana project)
- HMM-on-attention shortcuts (no model-semantics-preserving analog for K=8 discrete states)

## Gitea cross-ref

Commit 600deb0 `feat(regime): XAR-467 P2 production port + XAR-468 kernel research kickoff`  
Repo: `https://git.xart.top:8418/quant/k-atana.git`
