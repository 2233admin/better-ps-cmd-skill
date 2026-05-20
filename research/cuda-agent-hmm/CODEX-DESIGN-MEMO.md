# XAR-468 GPU HMM Kernel - Codex Design Memo

Source: codex-rescue agent (2026-05-20). Asked via codex-companion; companion
stdout was broken so rescue agent answered design memo directly.

## Rank-order of design concerns

| q | concern | verdict |
|---|---|---|
| Q5 | Triton-first vs CUDA-C++-first | REAL (decides Phase 0a) |
| Q1 | (B,T,K) data layout | REAL (8x bandwidth penalty if wrong) |
| Q8 | fit() vs filter() split | REAL (2x prod speedup, free) |
| Q2 | Fused vs separate FB | REAL (bandwidth not launch overhead) |
| Q3 | M-step kernel placement | REAL (reduction pattern matters) |
| Q9 | Numerical stability | REAL but easy (log-space) |
| Q4 | Multi-stream | NOISE at current parallelism |
| Q6 | Parity oracle | hmmlearn sufficient, pomegranate optional |
| Q7 | Blackwell sm_120 Triton maturity | REAL, must verify before sprinting |

## Key answers

- **(B,T,K) row-major, K innermost.** A warp of 32 threads = 4 series x 8 states,
  coalesced 128-byte loads. Do NOT transpose to (T,B,K) -- kills coalescing in
  the T-sequential recurrence.
- **Three kernels**: (a) emission compute (B,T,K,D) -> (B,T,K) log-prob,
  (b) FB recurrence, (c) M-step gamma -> params. Fuse backward + gamma
  accumulation into one pass over T (saves one full (B,T,K) read-back).
  K^2=64 fits in registers.
- **M-step**: backward kernel outputs (B,K) sufficient statistics to shared
  memory, separate warp-reduction kernel for final means/vars. Do NOT put
  B*T reduction in backward scan kernel -- bloats shared memory, kills
  occupancy.
- **Multi-stream**: one stream per HMM instance for hyperparam grid parallelism.
  5090 has 170 SMs; HMM kernel is bandwidth-bound so SM time-slicing is fine.
- **Triton first.** CUDA-Agent needs working oracle to iterate against. Triton
  gives verifiable batched baseline in ~200 lines, no FFI, compatible with our
  PyTorch context. Skipping it = CUDA-Agent starts blind.
- **Parity oracle**: hmmlearn single-seq CPU + <=1e-5 LL delta sufficient. Add
  pomegranate integration check only AFTER Triton baseline passes hmmlearn.
- **Blackwell/Triton gotcha**: Triton 3.x sm_120 support uncertain as of
  early 2026. **Verify `triton.runtime.driver.active.get_current_target()`
  returns sm_120 before investing.** Tensor cores not useful (K=8 < 16x16
  WMMA tile). Shared memory for K^2=8x8 transition matrix: always use,
  256 bytes is trivial.
- **fit() vs filter() split**: Expose both. filter() runs emission + forward
  only, NO backward. Free 40-50% speedup on daily prod refit.
- **Log-space throughout**: Rabiner scaling is for fp16 or very long T;
  with K=8, T=1000, fp32, log-space underflow is not a concern.

## Missing alternative (flagged)

**Parallel-prefix scan (Blelloch) over T** for the forward pass: O(T) serial
scan -> O(log T) parallel steps. For T=1000 B=7504 on Blackwell (strong
prefix-sum hardware support), worth evaluating. K=8 discrete states make
the associative operator (matrix-vector in log-semiring) cheap. **NOT in
the original plan, should be.**

**HMM-on-attention**: pointless. Attention is continuous-score; K=8 hard
discrete + Viterbi/EM has no attention analog that preserves model
semantics.

## Recommended Phase 0a sequence

1. Build hmmlearn CPU oracle on synthetic (B=64,T=200,K=4,D=5), lock LL
   reference values to file
2. Verify Triton 3.x compiles to sm_120 on Windows cu132 (one tl.store
   kernel, nothing fancy)
3. Implement Triton batched forward-only kernel, hit <=1e-5 LL parity
4. Extend to full FB + gamma extraction, hit parity
5. Hand off to CUDA-Agent with oracle file + Triton baseline as
   verification contract

**Fallback if step 2 fails** (Triton can't target sm_120 on Windows cu132):
pivot to CUDA C++ directly. Write oracle interface first, CUDA-Agent fills
implementation. Do not block the whole track on Triton maturity.
