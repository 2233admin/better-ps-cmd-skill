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
on a 5000-row series so we refit every `refit_every` days (default 21), but
predictions between refits use the last fit's transition matrix + emission
params with forward-only filtering. State labels are sorted by mean return so
state 0 = bear, state 1 = neutral, state 2 = bull, stable across refits.

Anti-pattern this guards against: `model.fit(full_series); model.predict_proba(full_series)`
would leak future returns into past regime labels (i.e. the Viterbi smoothing
uses the entire sequence). We use forward-only `score_samples` on truncated
slices instead.

Known limitation (2026-05-20 smoke on CSI300 2007-2026)
-------------------------------------------------------
Univariate Gaussian HMM on log returns learns a **volatility regime**, not a
**trend regime**. Empirically:
  - state 0 (low mean, high std)  -- captures crash/panic episodes well
    (2015-Q3: 35% bear days, 2020 covid: 53% bear days)
  - state 2 (high mean, low std)  -- captures quiet uptrend
  - state 1 (middle)              -- noisy mixed days
Slow-grind bear markets (2018, 2021-2022, daily mean ~-0.17%) have low daily
vol and get labelled bull/neutral, not bear. Tracked as follow-up: add 20d
vol + 60d momentum features (multivariate HMM) or switch to Markov-switching
AR (statsmodels). Linear: XAR-464.

Use this module as a crash-detection meta-feature / execution guard, not as a
standalone trend label. Pair with a momentum feature for the trend dimension.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from hmmlearn.hmm import GaussianHMM


def _label_states_by_mean(model: GaussianHMM) -> np.ndarray:
    """Return permutation that orders states from lowest mean to highest.

    State 0 (bear), 1 (neutral), 2 (bull) after permutation."""
    means = model.means_.flatten()
    return np.argsort(means)


def _forward_proba(model: GaussianHMM, returns: np.ndarray) -> np.ndarray:
    """Forward-only posterior P(state_t | r_0..r_t), no Viterbi smoothing.

    Returns (T, n_states). Row t uses only returns[0..t]."""
    log_startprob = np.log(model.startprob_ + 1e-30)
    log_transmat = np.log(model.transmat_ + 1e-30)
    log_frameprob = model._compute_log_likelihood(returns.reshape(-1, 1))

    T, K = log_frameprob.shape
    log_alpha = np.zeros((T, K))
    log_alpha[0] = log_startprob + log_frameprob[0]
    for t in range(1, T):
        for k in range(K):
            log_alpha[t, k] = (
                np.logaddexp.reduce(log_alpha[t - 1] + log_transmat[:, k])
                + log_frameprob[t, k]
            )
    log_norm = np.logaddexp.reduce(log_alpha, axis=1, keepdims=True)
    return np.exp(log_alpha - log_norm)


def expanding_fit_hmm(
    returns: pl.DataFrame,
    *,
    n_states: int = 3,
    min_train: int = 252,
    refit_every: int = 21,
    seed: int = 0,
) -> pl.DataFrame:
    """PIT-safe expanding HMM regime probabilities.

    Parameters
    ----------
    returns : pl.DataFrame with columns (date: Date, ret: Float64)
        Daily index returns, sorted by date ascending.
    n_states : 3 by default (bear / neutral / bull).
    min_train : warmup window; rows < min_train get null probs.
    refit_every : refit cadence in trading days; between refits we apply forward
        filtering with the prior fit's parameters.
    seed : reproducibility for HMM EM init.

    Returns
    -------
    pl.DataFrame with columns (date, p_bear, p_neutral, p_bull, state_argmax).
    Rows before min_train are null. Row t depends only on returns[0..t].
    """
    if returns.height < min_train + 1:
        raise ValueError(
            f"returns has {returns.height} rows, need >= {min_train + 1}"
        )
    if returns.columns[:2] != ["date", "ret"] and not (
        "date" in returns.columns and "ret" in returns.columns
    ):
        raise ValueError(f"returns must have date+ret columns, got {returns.columns}")

    dates = returns["date"].to_numpy()
    r = returns["ret"].to_numpy().astype(float)
    T = len(r)
    probs = np.full((T, n_states), np.nan)

    model: GaussianHMM | None = None
    perm: np.ndarray | None = None
    last_refit = -1
    for t in range(min_train, T):
        needs_refit = (model is None) or (t - last_refit >= refit_every)
        if needs_refit:
            train = r[:t].reshape(-1, 1)
            model = GaussianHMM(
                n_components=n_states,
                covariance_type="diag",
                n_iter=50,
                random_state=seed,
                tol=1e-3,
            )
            model.fit(train)
            perm = _label_states_by_mean(model)
            last_refit = t
        slice_r = r[: t + 1]
        p = _forward_proba(model, slice_r)[-1]
        probs[t] = p[perm]

    out = pl.DataFrame(
        {
            "date": dates,
            "p_bear": probs[:, 0],
            "p_neutral": probs[:, 1],
            "p_bull": probs[:, 2],
        }
    ).with_columns(
        pl.when(pl.col("p_bear").is_null())
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
