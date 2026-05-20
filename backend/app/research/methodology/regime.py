"""3-state Gaussian HMM regime detection with PIT-safe rolling refit.

P1.3 alt-data / meta-feature for k-atana ship gate. Emits per-`as_of` state
probabilities (bull / neutral / bear) over index returns; downstream consumers
use it as factor-weighting gate or meta-feature input.

References
----------
Hamilton (1989) "A New Approach to the Economic Analysis of Nonstationary Time
Series and the Business Cycle." Econometrica 57(2): 357-384.

Rabiner (1989) "A Tutorial on Hidden Markov Models and Selected Applications in
Speech Recognition." Proc. IEEE 77(2): 257-286.

Wheel search verdict (2026-05-20, live gh API; full audit in docs/P1-WHEEL-AUDIT.md):
  hmmlearn     3375★  pushed 2024-10-31  BSD-3   fit-primary: Gaussian HMM,
                                                  predict_proba returns posterior
  ruptures     2030★  pushed 2026-04-06  BSD-2   change-point only, no probs
  pomegranate  3532★  pushed 2025-03-06  MIT     torch dep too heavy for P1
  numpyro       2681★  pushed 2026-05-17  Apache  Bayesian HMM — research only
  arch         1522★  pushed 2026-04-06  none    no Markov-switching in repo

  Wheels evaluated, hmmlearn fit because: it's the only one with
  Gaussian/multivariate HMM + posterior probabilities + minimal deps (numpy +
  scipy + scikit-learn), all already in the project venv.

PIT contract
------------
The fit is monotone-in-time: `expanding_fit_hmm(returns, t)` only uses rows
0..t-1 to produce the prob row for date t-1. Re-fitting every day is expensive
on a 5000-row series so we refit every `refit_every` days (default 21). State
labels are sorted by mean return so state 0 = bear, state 1 = neutral, state 2
= bull, stable across refits.

Algorithm: incremental forward filter
-------------------------------------
For each refit era [era_start, era_end):
  1. EM-fit hmmlearn GaussianHMM on returns[:era_start]
  2. Run ONE full forward pass on returns[:era_start] to seed log_alpha
  3. For each subsequent day in the era, do a single incremental step:
       log_alpha[t] = logsumexp(log_alpha[t-1] + log_trans, axis=0) + log_frame[t]
This is O(T) total in forward filtering (was O(T^2) in the day-by-day
prefix-recompute baseline). CSI300 21yr / 246 refits: 82s -> 12s (~7x):
numba JIT (3.3x), then n_iter=15 with cold-init each refit (~2x more).
Warm-start across refits was tested and rejected: it sticks at local
optima as the regime distribution shifts (bear-state mass collapsed from
~35% to 0% in 2015 Q3 crash with warm-start enabled).

Anti-pattern this guards against: `model.fit(full_series); model.predict_proba(full_series)`
would leak future returns into past regime labels (Viterbi smoothing uses the
entire sequence). We use forward-only filtering on truncated prefixes instead.

GPU path (`device='cuda'`)
--------------------------
Single-series throughput on CPU is sub-second; GPU adds kernel-launch overhead
that dwarfs the K^2=9 ops per step, so single-series stays CPU by default. The
GPU path exists for cross-section batched regime detection (many series in
parallel), where a (B, K) tensor step is fully parallel and the 5090 actually
earns its keep. See `expanding_fit_hmm_batched` (TODO XAR-466).

Multivariate vs univariate (2026-05-20 smoke on CSI300 2005-2026)
----------------------------------------------------------------
expanding_fit_hmm now accepts a multi-column feature panel (ret, vol_20d,
mom_60d, ...). Empirical episode capture comparing univariate vs MV1:

  Episode             Uni-bear%  MV1-bear%
  2015 Q3 crash       50.7%      40.0%
  2018 slow grind     21.4%      10.3%
  2020 covid          25.0%      45.0%
  2021-22 slow grind  15.5%      28.6%

MV1 trades crash precision for slow-bear sensitivity (2021-22 +13pp, covid
+20pp). 2018 stays intractable for either model: it was a year-long quiet
drift indistinguishable from low-vol neutral even at the 250d horizon.

Feature standardization (z-score with expanding window) was tested and
rejected: shrinks state separation, broke 2015 Q3 (16% bear) and 2021-22
(14% bear). Raw features outperform standardized ones for this model.

Choose the variant based on use case:
  - crash-detection meta-feature (sharp drops) -> univariate
  - slow-grind detection (2021-22 style)       -> MV1 (ret + vol_20d + mom_60d)
  - production ship-gate consumes both as separate signals.
"""

from __future__ import annotations

import logging

import numpy as np
import polars as pl
from hmmlearn.hmm import GaussianHMM
from numba import njit
from scipy.stats import multivariate_normal

_log = logging.getLogger(__name__)

# Column names targeted by log1p in `expanding_fit_hmm_batched` when caller
# passes feat_names. Validated on the 20-feature universe panel (XAR-467):
# range_20d std 1.22 -> 0.07, amihud_20d std 171 -> 0.05, vol_accel std 93 -> 0.29.
# Standard Amihud-2002 transform on non-negative heavy-tail ratios. PIT-safe.
LOG1P_FEATURE_NAMES: tuple[str, ...] = ("range_20d", "amihud_20d", "vol_accel")

# Fallback positional indices if caller does NOT pass feat_names. Matches the
# canonical feature order in build_universe_panel_full.py.
LOG1P_DEFAULT_INDICES: tuple[int, ...] = (10, 12, 18)


def _resolve_log1p_indices(
    D: int, feat_names: list[str] | None
) -> tuple[int, ...]:
    if feat_names is None:
        return tuple(i for i in LOG1P_DEFAULT_INDICES if i < D)
    name_to_idx = {n: i for i, n in enumerate(feat_names)}
    return tuple(
        name_to_idx[n] for n in LOG1P_FEATURE_NAMES if n in name_to_idx
    )


def _normalize_features(
    panel_raw: np.ndarray,
    fit_end_idx: int,
    D: int,
    jitter_rng: np.random.Generator,
) -> np.ndarray:
    """Expanding-window z-score over panel[:, :fit_end_idx, :].

    PIT-safe: mu/sigma are computed ONLY on the training slice; the out-of-sample
    tail is normalized using those training stats (no future leakage). Sigma
    floor 1e-3 (NOT 1e-8) prevents post-clip zero-variance partitions that
    trip pomegranate's "Variances must be positive" check inside EM. Jitter
    sigma=1e-3 breaks degenerate ties that collapse EM components.

    See XAR-467-PHASE1.md for the v3 bench that validated these constants.
    """
    train = panel_raw[:, :fit_end_idx, :].reshape(-1, D)
    mu = train.mean(axis=0)
    sigma = train.std(axis=0)
    sigma = np.where(sigma < 1e-3, 1e-3, sigma)
    z = (panel_raw - mu) / sigma
    z = np.clip(z, -10, 10).astype(np.float32)
    z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
    z += jitter_rng.normal(0, 1e-3, size=z.shape).astype(np.float32)
    return z


@njit(cache=True, fastmath=True)
def _logsumexp_axis0(x: np.ndarray) -> np.ndarray:
    """logsumexp over axis 0 of a 2D array. Returns 1D."""
    K = x.shape[1]
    out = np.empty(K)
    for k in range(K):
        col = x[:, k]
        m = col.max()
        s = 0.0
        for i in range(col.shape[0]):
            s += np.exp(col[i] - m)
        out[k] = m + np.log(s)
    return out


def _gaussian_log_emission_mv(
    X: np.ndarray, means: np.ndarray, covars: np.ndarray
) -> np.ndarray:
    """Multivariate Gaussian log emission, K states. X (T, D), means (K, D),
    covars (K, D, D) -- always expanded shape, works for diag (returned as
    diagonal matrix by hmmlearn .covars_ getter) and full alike. Returns (T, K).
    """
    T = X.shape[0]
    K = means.shape[0]
    out = np.empty((T, K))
    for k in range(K):
        out[:, k] = multivariate_normal.logpdf(
            X, mean=means[k], cov=covars[k], allow_singular=True
        )
    return out


@njit(cache=True, fastmath=True)
def _forward_prefix_jit(
    log_startprob: np.ndarray,
    log_transmat: np.ndarray,
    log_frameprob: np.ndarray,
) -> np.ndarray:
    """JIT forward pass. Returns log_alpha of shape (T, K)."""
    T, K = log_frameprob.shape
    log_alpha = np.empty((T, K))
    for k in range(K):
        log_alpha[0, k] = log_startprob[k] + log_frameprob[0, k]
    buf = np.empty((K, K))
    for t in range(1, T):
        # buf[i, k] = log_alpha[t-1, i] + log_transmat[i, k]
        for i in range(K):
            a = log_alpha[t - 1, i]
            for k in range(K):
                buf[i, k] = a + log_transmat[i, k]
        # logsumexp_i(buf[:, k]) for each k
        for k in range(K):
            m = buf[0, k]
            for i in range(1, K):
                if buf[i, k] > m:
                    m = buf[i, k]
            s = 0.0
            for i in range(K):
                s += np.exp(buf[i, k] - m)
            log_alpha[t, k] = m + np.log(s) + log_frameprob[t, k]
    return log_alpha


@njit(cache=True, fastmath=True)
def _forward_step_jit(
    log_alpha_prev: np.ndarray,
    log_transmat: np.ndarray,
    log_frame_t: np.ndarray,
) -> np.ndarray:
    """One incremental forward step (JIT)."""
    K = log_alpha_prev.shape[0]
    out = np.empty(K)
    for k in range(K):
        m = log_alpha_prev[0] + log_transmat[0, k]
        for i in range(1, K):
            v = log_alpha_prev[i] + log_transmat[i, k]
            if v > m:
                m = v
        s = 0.0
        for i in range(K):
            s += np.exp(log_alpha_prev[i] + log_transmat[i, k] - m)
        out[k] = m + np.log(s) + log_frame_t[k]
    return out


def _label_states_by_mean(model: GaussianHMM, label_idx: int = 0) -> np.ndarray:
    """Return permutation that orders states from lowest mean to highest along
    feature column `label_idx` (default 0 = first feature, typically ret)."""
    return np.argsort(model.means_[:, label_idx])


def expanding_fit_hmm(
    features: pl.DataFrame,
    *,
    label_col: str = "ret",
    n_states: int = 3,
    min_train: int = 252,
    refit_every: int = 21,
    seed: int = 0,
) -> pl.DataFrame:
    """PIT-safe expanding HMM regime probabilities (incremental forward filter).

    Parameters
    ----------
    features : pl.DataFrame with columns ['date', <feature_1>, <feature_2>, ...]
        All non-date columns become the multivariate observation vector. For
        univariate regime detection pass date+ret (D=1). For multivariate (the
        recommended setup that catches slow bears) pass date+ret+vol_20d+mom_60d.
    label_col : column used to order states; lowest mean = bear, highest = bull.
        Default 'ret'. Must be one of the feature columns.
    n_states : 3 by default (bear / neutral / bull).
    min_train : warmup window; rows < min_train get NaN probs.
    refit_every : refit cadence in trading days; between refits the same
        emission/transition params are used with incremental forward filtering.
    seed : reproducibility for HMM EM init.

    Returns
    -------
    pl.DataFrame with columns (date, p_bear, p_neutral, p_bull, state_argmax).
    Rows before min_train are NaN. Row t depends only on features[0..t].
    """
    if features.height < min_train + 1:
        raise ValueError(
            f"features has {features.height} rows, need >= {min_train + 1}"
        )
    if "date" not in features.columns:
        raise ValueError(f"features must include 'date' column, got {features.columns}")
    feature_cols = [c for c in features.columns if c != "date"]
    if not feature_cols:
        raise ValueError("features must include >= 1 non-date column")
    if label_col not in feature_cols:
        raise ValueError(f"label_col '{label_col}' not in features {feature_cols}")
    label_idx = feature_cols.index(label_col)

    dates = features["date"].to_numpy()
    X = features.select(feature_cols).to_numpy().astype(float)
    T, D = X.shape
    cov_type = "diag" if D == 1 else "full"
    probs = np.full((T, n_states), np.nan)

    t = min_train
    while t < T:
        # Refit on prefix X[:t]. Cold-init each era — warm-starting from prev era
        # sticks at local optima as the data distribution shifts (verified on
        # CSI300 21yr: bear-state mass collapsed from ~35% to 0% in 2015 crash
        # with warm-start enabled). EM cold-converges in ~10-15 iters per fit.
        train = X[:t]
        model = GaussianHMM(
            n_components=n_states,
            covariance_type=cov_type,
            n_iter=15,
            random_state=seed,
            tol=1e-4,
        )
        model.fit(train)
        perm = _label_states_by_mean(model, label_idx=label_idx)
        log_startprob = np.log(model.startprob_ + 1e-30)
        log_transmat = np.log(model.transmat_ + 1e-30)

        # Era: produce probs for rows [t, era_end). Compute emissions for the
        # whole era up to era_end in one scipy call (D-dimensional Gaussian).
        era_end = min(t + refit_every, T)
        log_frame_all = _gaussian_log_emission_mv(
            X[:era_end], model.means_, model.covars_
        )

        # Full forward pass on r[:t+1] (seed log_alpha for date t).
        log_alpha = _forward_prefix_jit(
            log_startprob, log_transmat, log_frame_all[: t + 1]
        )
        log_alpha_curr = log_alpha[-1]
        m = log_alpha_curr.max()
        p = np.exp(log_alpha_curr - m)
        probs[t] = (p / p.sum())[perm]

        # Incremental for the rest of the era.
        for tau in range(t + 1, era_end):
            log_alpha_curr = _forward_step_jit(
                log_alpha_curr, log_transmat, log_frame_all[tau]
            )
            m = log_alpha_curr.max()
            p = np.exp(log_alpha_curr - m)
            probs[tau] = (p / p.sum())[perm]

        t = era_end

    out = pl.DataFrame(
        {
            "date": dates,
            "p_bear": probs[:, 0],
            "p_neutral": probs[:, 1],
            "p_bull": probs[:, 2],
        }
    ).with_columns(
        pl.when(pl.col("p_bear").is_null() | pl.col("p_bear").is_nan())
        .then(None)
        .otherwise(
            pl.struct(["p_bear", "p_neutral", "p_bull"]).map_elements(
                lambda s: int(np.argmax([s["p_bear"], s["p_neutral"], s["p_bull"]])),
                return_dtype=pl.Int8,
            )
        )
        .alias("state_argmax")
    )
    return out


def expanding_fit_hmm_batched(
    panel: np.ndarray,
    *,
    label_dim: int = 0,
    n_states: int = 8,
    min_train: int = 504,
    refit_every: int = 21,
    seed: int = 0,
    device: str = "auto",
    max_iter: int = 15,
    tol: float = 1e-4,
    t_max: int | None = 1000,
    normalize: bool = True,
    feat_names: list[str] | None = None,
    n_retries: int = 3,
    min_active_states: int = 4,
) -> np.ndarray:
    """Batched cross-section HMM with PIT-safe expanding refit (XAR-466 / XAR-467).

    Fits ONE DenseHMM with `n_states` regimes SHARED across all B series.
    Each series gets its own posterior P(state_t | obs[1:t]) over the universal
    regime definitions -- the shared-params model defines what "bear" / "neutral"
    / "bull" mean uniformly across the cross-section, while each series'
    posterior reflects whether THAT series is in each state at each step.

    Backed by pomegranate v1.x; runs on cu132 + sm_120 (RTX 5090 Blackwell).
    Production-shape bench (XAR-466 Phase 2 v3, B=7504 T=1889 D=20 K=8, T_max=1000):
      total 66-refit walk-forward scan  6m02s
      per-refit fit  ~2.6s,  per-refit norm  ~2.5s,  GPU peak 5.2 GB
      avg n_active states  6.4 / 8 (6 sporadic collapses, see seed-retry below)

    Normalization
    -------------
    log1p applied once to heavy-tail non-negative ratios (range_20d, amihud_20d,
    vol_accel) -- Amihud-2002 standard for illiquidity factors. Cell-wise and
    monotonic, so PIT-safe. Then expanding-window z-score per refit, computed on
    `panel[:, :fit_end_idx, :]` only (out-of-sample slice normalized with training
    stats carries no future information). Sigma floor 1e-3, jitter sigma=1e-3.
    Set `normalize=False` for testing on already-standardized panels.

    Seed-retry
    ----------
    Cross-section multi-modal feature distribution lets EM converge to a single
    dominant mode on bad cold-init seeds. ~9% of refits land in degenerate local
    optima where one state captures >99% mass. If `n_active < min_active_states`
    after a fit, re-init with a fresh seed and retry up to `n_retries` times. If
    all attempts degenerate, the highest-n_active attempt is accepted and a
    WARNING is logged. Cold-init each refit (NOT warm-start) -- warm-start
    sticks at local optima in this panel shape (verified XAR-466 Phase 2 v2).

    Parameters
    ----------
    panel : np.ndarray shape (B, T, D)
        B series, T timesteps, D features per series. Features must be aligned
        on the same date axis across B. NaN/inf rows must be cleaned upstream.
    label_dim : feature column index used to order states by mean. Lowest mean
        on this dim = bear, highest = bull. Default 0 (typically ret).
    n_states : 8 by default (e.g. crash / bear / weak / neutral / weak-bull /
        bull / momentum / euphoria). Set 3 to mirror single-series semantics.
    min_train : warmup -- rows < min_train get NaN.
    refit_every : refit cadence in steps.
    seed : reproducibility for EM init.
    device : "cuda" | "cpu" | "auto" (auto picks cuda if torch.cuda.is_available).
    max_iter : EM max iterations per refit.
    tol : EM convergence tolerance.
    t_max : sliding cap on training window size. Per-refit cost is O(B*T_train*K^2);
        bounding T_train keeps wall-clock flat as T grows. Default 1000 (per
        XAR-466 Phase 2 v2 bench, drops 9.7 GB peak to 5.2 GB, ~1.33x speedup).
        Pass None to disable the cap (expanding window).
    normalize : apply log1p + expanding z-score before fit. Default True. The
        canonical k-atana 20-feature panel needs this; turning off is only for
        testing or already-standardized inputs.
    feat_names : optional list of D column names. If provided, log1p targets
        the subset of LOG1P_FEATURE_NAMES that appear in feat_names. If None,
        falls back to LOG1P_DEFAULT_INDICES.
    n_retries : retries with fresh seed when state collapse detected.
    min_active_states : threshold for accepting a fit. n_active = number of
        states whose labeled frequency in the era posterior exceeds 1%.

    Returns
    -------
    np.ndarray shape (B, T, n_states) float32
        Posterior P(state | obs[1:t]) per series, state-ordered along label_dim
        so index 0 = lowest-mean state. Rows < min_train are NaN.

    Notes
    -----
    Forward-only filtering is used (pomegranate's `model.forward(X)` returns
    log alpha = log P(state_t, obs[1:t])). NO future leakage. Refits happen
    every `refit_every` steps; in the era between refits, the forward filter
    rolls log_alpha forward with FIXED params from the most recent fit.
    """
    import torch
    from pomegranate.distributions import Normal
    from pomegranate.hmm import DenseHMM

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if panel.ndim != 3:
        raise ValueError(f"panel must be 3D (B,T,D), got shape {panel.shape}")
    B, T, D = panel.shape
    if T < min_train + 1:
        raise ValueError(f"panel has T={T}, need >= {min_train + 1}")
    if not (0 <= label_dim < D):
        raise ValueError(f"label_dim {label_dim} out of range [0,{D})")
    if feat_names is not None and len(feat_names) != D:
        raise ValueError(
            f"feat_names length {len(feat_names)} != D={D}"
        )
    if n_retries < 1:
        raise ValueError(f"n_retries must be >= 1, got {n_retries}")

    rng = np.random.default_rng(seed)
    probs = np.full((B, T, n_states), np.nan, dtype=np.float32)

    # log1p prep, once.
    if normalize:
        log1p_cols = _resolve_log1p_indices(D, feat_names)
        panel_norm_raw = panel.astype(np.float32, copy=True)
        for c in log1p_cols:
            panel_norm_raw[:, :, c] = np.log1p(
                np.clip(panel[:, :, c], 0.0, None)
            )
    else:
        panel_norm_raw = panel.astype(np.float32, copy=False)

    def _build_dists(local_rng: np.random.Generator) -> list:
        dists_out = []
        for _ in range(n_states):
            m = local_rng.uniform(-0.5, 0.5, D).tolist()
            cov = np.ones(D, dtype=np.float32).tolist()
            d = Normal(
                means=m,
                covs=cov,
                covariance_type="diag",
                min_cov=1e-4,
            )
            if device == "cuda":
                d = d.cuda()
            dists_out.append(d)
        return dists_out

    t = min_train
    while t < T:
        era_end = min(t + refit_every, T)
        train_start = max(0, t - t_max) if t_max is not None else 0
        train_T = t - train_start

        # Normalize the (train + era) window using training-slice stats only.
        if normalize:
            win = _normalize_features(
                panel_norm_raw[:, train_start:era_end, :],
                fit_end_idx=train_T,
                D=D,
                jitter_rng=rng,
            )
        else:
            win = panel_norm_raw[:, train_start:era_end, :]

        best_n_active = -1
        best_probs_slice: np.ndarray | None = None
        last_attempt_idx = 0

        for attempt in range(n_retries):
            attempt_rng = np.random.default_rng(
                int(rng.integers(0, 2**31 - 1))
            )
            dists = _build_dists(attempt_rng)
            model = DenseHMM(
                distributions=dists,
                max_iter=max_iter,
                tol=tol,
                verbose=False,
            )
            if device == "cuda":
                model.cuda()

            X_train = torch.tensor(
                win[:, :train_T, :], dtype=torch.float32
            )
            if device == "cuda":
                X_train = X_train.cuda()
            model.fit(X_train)

            X_era = torch.tensor(win, dtype=torch.float32)
            if device == "cuda":
                X_era = X_era.cuda()
            log_alpha = model.forward(X_era)
            rel_start = train_T
            rel_end = train_T + (era_end - t)
            post = (
                torch.softmax(log_alpha[:, rel_start:rel_end, :], dim=-1)
                .detach()
                .cpu()
                .numpy()
            )

            means_stack = torch.stack(
                [d.means for d in model.distributions]
            )
            perm = (
                means_stack[:, label_dim].argsort().detach().cpu().numpy()
            )
            labeled = post[..., perm].astype(np.float32)
            argmax = labeled.argmax(axis=-1)
            freq = np.bincount(
                argmax.flatten(), minlength=n_states
            ).astype(np.float64)
            freq /= freq.sum()
            n_active = int((freq > 0.01).sum())

            if device == "cuda":
                del X_train, X_era, log_alpha
                torch.cuda.empty_cache()

            last_attempt_idx = attempt
            if n_active > best_n_active:
                best_n_active = n_active
                best_probs_slice = labeled

            if n_active >= min_active_states:
                break

        if best_n_active < min_active_states:
            _log.warning(
                "regime HMM refit collapsed: t=%d n_active=%d/%d "
                "after %d retries; accepting best attempt",
                t,
                best_n_active,
                n_states,
                last_attempt_idx + 1,
            )

        assert best_probs_slice is not None  # at least one attempt ran
        probs[:, t:era_end, :] = best_probs_slice

        t = era_end

    return probs
