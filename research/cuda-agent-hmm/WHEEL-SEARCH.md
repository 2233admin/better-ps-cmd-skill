# WHEEL-SEARCH — Batched GPU HMM Forward-Backward (XAR-468)

Date: 2026-05-20
Constraints: B=7504, T=1000, D=20, K=8 diag-Gauss HMM, ≤1s per refit, RTX 5090 cu132 sm_120, Win11 + Python 3.11 + uv, ≤1e-5 logL parity, PIT-safe.

## Candidate matrix (source: gh API live, 2026-05-20)

| repo | stars | pushedAt | license | verdict | rationale |
|------|-------|----------|---------|---------|-----------|
| probml/dynamax | 964 | 2026-01-06 | MIT | **too-heavy:JAX-no-Win-GPU** | Has `DiagonalGaussianHMM` + batched `fit_em` on `(B,T,D)`. But JAX pip wheels are Linux-only; Windows = CPU or WSL2. |
| pyro-ppl/pyro | 9004 | 2025-07-09 | Apache-2.0 | schema-mismatch:no-.fit() | `GaussianHMM`/`DiscreteHMM` are dist objects with `log_prob` only; EM/SVI must be hand-coded. |
| pyro-ppl/numpyro | 2683 | 2026-05-17 | Apache-2.0 | too-heavy:JAX-no-Win-GPU | Same JAX/Win blocker as dynamax; also no canned diag-Gauss HMM EM. |
| lindermanlab/ssm | 700 | 2025-05-09 | MIT | stale:pushed_at=2025-05-09 | NumPy/Autograd backend, ~12mo idle, no native GPU path. |
| state-spaces/mamba | 18274 | 2026-05-10 | Apache-2.0 | schema-mismatch:continuous-SSM | Selective SSM for sequence modeling, not discrete latent-state HMM. |
| lindermanlab/ssm-jax | 62 | 2024-06-17 | MIT | stale:pushed_at=2024-06-17 | Superseded by dynamax; ~23mo stale. |
| eonu/sequentia | 68 | 2024-12-30 | MIT | stale + schema-mismatch | scikit-style wrapper around hmmlearn; CPU-only, ~17mo stale. |
| hmmlearn/hmmlearn | 3375 | 2024-10-31 | BSD-3-Clause | schema-mismatch:cpu-only | Pure NumPy, no batched GPU path; used only as parity oracle. |
| jmschrei/pomegranate | 3533 | 2025-03-06 | MIT | schema-mismatch + stale:14mo | Current incumbent (v1.1.2). Torch GPU but B-loop is per-sequence inside `summarize`; not the (B,T,D) batched FB kernel we need. 2.6s/refit is the baseline we are trying to beat. |
| slinderman/torchhmm | 1 | 2018-07-05 | — | stale:pushed_at=2018 | Dead 8yr. |
| bnpy/bnpy | 234 | 2023-07-24 | NOASSERTION | stale:pushed_at=2023-07-24 | ~34mo dead, also license unclear. |
| mattjj/pyhsmm | 577 | 2025-01-25 | MIT | stale:pushed_at=2025-01-25 | ~16mo idle, Cython CPU, semi-Markov focus. |
| lorenlugosch/pytorch_HMM | 143 | 2021-03-20 | Apache-2.0 | stale:pushed_at=2021 | 5yr dead, categorical emissions only. |
| TreB1eN/HiddenMarkovModel_Pytorch | 56 | 2019-02-18 | none | stale:7yr | Toy implementation, no batched fit. |
| georgepar/gmmhmm-pytorch | 10 | 2020-12-28 | none | stale:5yr | Toy, no diag-Gauss path. |
| guxd/deepHMM | 58 | 2024-07-25 | — | stale + schema-mismatch | Deep HMM (variational), not diag-Gauss EM. |
| google-deepmind/PGMax | — | 2026-05-10 | — | schema-mismatch + JAX-Win | Factor graph BP on discrete vars; not Gauss-emission HMM. |

## Per-candidate notes

**dynamax** — Closest fit on API: `DiagonalGaussianHMM` + `fit_em(params, props, emissions)` accepts `(num_batches, num_timesteps, emission_dim)` natively. Internally uses JAX `vmap` for parallel sequence processing. **Blocker is JAX Windows GPU**: official docs say "These pip installations do not work with Windows, and may fail silently"; only Linux wheels exist for `jax[cuda12]`/`jax[cuda13]`. WSL2 is "experimental." Building JAX from source against cu132/sm_120 on Windows is a multi-day rabbit hole and not what XAR-468 is for.

**pyro / numpyro** — Pyro `GaussianHMM` is a distribution with `log_prob`/`filter` only; no `.fit()`. Going SVI route means rewriting EM as guide + ELBO + custom convergence — bigger than self-writing the FB kernel. NumPyro inherits the Windows-JAX blocker.

**lindermanlab/ssm** — Autograd/NumPy; was the canonical lab HMM toolkit pre-dynamax. Stale and CPU-bound.

**mamba / state-spaces** — Solves a different problem (continuous selective state-space sequence models). Not a discrete-K HMM with EM.

**hmmlearn / sequentia** — CPU-only; useful as **parity oracle** for the ≤1e-5 logL test but not as a runtime replacement.

**pomegranate v1.1.2** — Already in the stack. Torch backend works on cu132 sm_120. Batched fit is per-sequence-loop inside `summarize`, which is why we sit at 2.6s/refit. The candidate to **beat**, not to swap to.

**pyhsmm / bnpy / torchhmm / toy repos** — All stale ≥16mo and CPU-bound or feature-mismatched.

## Pomegranate v1 warm-start probe (bonus)

Source inspection of `pomegranate/hmm/_base.py` EM loop confirms early termination:

```python
for i in range(self.max_iter):
    ...
    if improvement < self.tol:
        self._reset_cache()
        return self
```

The short-circuit fires whether `max_iter` is reached or `improvement < tol`, and it works the same on warm-start (parameters already fit) — first iteration's `improvement` will be small. **No open issue in pomegranate tracker about HMM warm-start convergence failing.** The closest open bug (#1145, 2026-04-24) is GMM-only, about negative improvements counting as convergence. The warm-start path Curry abandoned in v2 is not blocked by an upstream bug; it should work in v1 with appropriate `tol`. No 1-line patch lever here.

## Verdict

```
RECOMMENDED INTEGRATION: none-fit
FALLBACK: self-write (PyTorch CUDA batched FB kernel)
JUSTIFICATION: Dynamax is the only schema-fit candidate but JAX has no Windows GPU wheels (Linux-only, WSL2 experimental), and the Windows + uv + cu132 constraint is hard. Every other candidate is stale ≥16mo, CPU-only, schema-mismatched (Pyro has no .fit, Mamba is continuous SSM), or is the incumbent we are trying to beat (pomegranate v1.1.2).
```

## Recommended self-write scope

- Custom PyTorch CUDA forward-backward over `(B, T, K)` log-α / log-β tensors
- Diagonal-Gauss log-emission as `(B, T, K)` via broadcasted means/log-vars
- M-step on diagonal cov closed-form (no Cholesky)
- Parity oracle: `hmmlearn` single-sequence GaussianHMM on synthetic, assert ≤1e-5 logL delta
- Optional later: revisit dynamax under WSL2 if a Linux-side compute lane becomes acceptable for this project (not currently in scope per Windows-uv constraint)

## Unresolved questions

- WSL2 + dynamax would clear the Windows-GPU blocker. Is opening a WSL2 lane in scope for k-atana? (Out of XAR-468 scope but worth a Linear note.)
- Is the 2.6s/refit baseline already pomegranate-with-warm-start, or cold-start each refit? If cold-start, warm-start retry in v1 (with proven short-circuit) might hit <1s without writing a new kernel. Worth a 1hr probe before committing to self-write.
