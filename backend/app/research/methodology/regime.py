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

import numpy as np
import polars as pl
from hmmlearn.hmm import GaussianHMM
from numba import njit
from scipy.stats import multivariate_normal


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
