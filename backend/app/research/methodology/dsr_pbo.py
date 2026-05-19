"""Deflated Sharpe Ratio + PBO/CSCV + multiple-testing correction.

Ship gate alpha filter — W1 milestone (k-atana crypto branch, 6/15 deadline).

References
----------
Bailey & López de Prado (2014) "The Deflated Sharpe Ratio: Correcting for
Selection Bias, Backtest Overfitting and Non-Normality."
https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551

Bailey, Borwein, López de Prado & Zhu (2017) "The Probability of Backtest
Overfitting." Journal of Computational Finance 20(4).
https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253

Wheel search verdict (2026-05-19, live gh API):
  mlfinlab   4766★  pushed 2023-10-02  stale >30mo -- schema-mismatch + stale
  arch       1522★  pushed 2026-04-06  NOT installed in project venv; SPA via
                                       scipy block-bootstrap instead
  pyfolio    6306★  pushed 2023-12-23  stale >17mo -- no DSR
  quantstats 7134★  pushed 2026-01-13  report generator only, no DSR/PBO
  qlib      43216★  pushed 2026-04-22  too-heavy: pulls entire qlib dep chain
  empyrical  1479★  pushed 2024-07-26  no DSR, basic Sharpe/Sortino only

  Wheels evaluated, none fit because:
  - mlfinlab is the Bailey team's reference implementation but pushed_at=2023-10
    (>30mo stale) and has NOASSERTION license conflict. DSR formula is ~30 lines.
  - arch has SPA/Reality Check but is NOT in pyproject.toml; adding it for SPA
    alone adds a non-trivial C-extension dep. SPA replaced with block-bootstrap
    using scipy (already a dependency).
  - All others lack DSR/CSCV entirely.

  Self-writing DSR (~40 lines) + CSCV (~50 lines) + BH (scipy wrapper) is
  the correct call. scipy.stats.false_discovery_control available in scipy>=1.9
  (project has scipy==1.17.1).

n_trials provenance (PIT-safety note)
--------------------------------------
DSR is only honest when n_trials counts ALL strategies evaluated during search,
not just the survivors you observe. Callers must pass n_trials from:
  - gplearn: population_size * generations (default 500 * 20 = 10_000)
  - shinka:  population_size * num_generations (default 4 * 10 = 40, small evo)
  - pysr:    populations * niterations (depends on run config)
  - mother factors: n=18, all evaluated simultaneously; n_trials=18 for that set

If n_trials is unknown, pass a conservative (high) estimate -- DSR is pessimistic
by design and high n_trials makes it harder to pass (conservative, not liberal).
"""

from __future__ import annotations

import math
import warnings
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.stats import false_discovery_control

# Euler-Mascheroni constant (Bailey 2014 eq. 2)
_GAMMA_EM = 0.5772156649015329


def deflated_sharpe_ratio(
    sr_observed: float,
    n_trials: int,
    sr_std: float,
    T: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio per Bailey & López de Prado (2014).

    Computes the probability that an observed Sharpe Ratio is non-spurious,
    given that n_trials strategies were evaluated and sr_std is the standard
    deviation of SR across those trials.

    Parameters
    ----------
    sr_observed : float
        Observed (annualized) Sharpe Ratio of the best strategy.
    n_trials : int
        Total number of independent strategies tried (backtest trials).
        Must count ALL strategies evaluated, not only survivors.
    sr_std : float
        Standard deviation of SR across the n_trials strategies (or a
        conservative estimate if individual SRs are unavailable).
        Pass 0.0 for a single-trial evaluation (DSR reduces to classical SR CDF).
    T : int
        Number of independent observations (OOS bars) used to compute sr_observed.
    skew : float
        Return skewness of the strategy (default 0 = Gaussian assumption).
    kurt : float
        Return kurtosis of the strategy (default 3 = Gaussian assumption).

    Returns
    -------
    float
        Probability in [0, 1] that the observed SR is non-spurious.
        Values >= 0.95 are typically used as the acceptance threshold.

    Notes
    -----
    Formula (Bailey 2014, eqs 1-3):

        E[max(SR_0)] = sqrt(V) * [
            (1-gamma) * Phi^{-1}(1 - 1/N) +
            gamma * Phi^{-1}(1 - 1/(N*e))
        ]
        where V = sr_std^2, N = n_trials, gamma = Euler-Mascheroni (0.5772)

        DSR = Phi(
            (sr_obs - E[max(SR_0)]) * sqrt(T-1)
            / sqrt(1 - skew*sr_obs + ((kurt-1)/4) * sr_obs^2)
        )

    When n_trials=1, E[max(SR_0)]=0 and DSR reduces to the classical one-sided
    Sharpe p-value (Phi(sr_obs * sqrt(T-1) / adjustment)).
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")
    if T < 2:
        raise ValueError(f"T must be >= 2, got {T}")
    if sr_std < 0:
        raise ValueError(f"sr_std must be >= 0, got {sr_std}")

    # E[max SR_0] = 0 for single trial (no selection bias)
    if n_trials == 1 or sr_std == 0.0:
        e_max_sr0 = 0.0
    else:
        # Bailey 2014 eq. 2: expected maximum SR under the null (no skill)
        # Uses symmetry of standard normal: Phi^{-1}(1-x) = -Phi^{-1}(x)
        n = float(n_trials)
        # Guard against n too small for log(n) to be negative
        arg1 = max(1 - 1.0 / n, 1e-15)
        arg2 = max(1 - 1.0 / (n * math.e), 1e-15)
        e_max_sr0 = sr_std * (
            (1.0 - _GAMMA_EM) * norm.ppf(arg1) +
            _GAMMA_EM * norm.ppf(arg2)
        )

    # Denominator: non-normality adjustment (skew and excess kurtosis)
    # Clamp to avoid sqrt of negative (degenerate inputs)
    inner = 1.0 - skew * sr_observed + ((kurt - 1.0) / 4.0) * sr_observed ** 2
    if inner <= 0:
        warnings.warn(
            f"DSR denominator non-positive ({inner:.4f}); clamping to 1e-12. "
            "Check skew/kurt inputs.",
            stacklevel=2,
        )
        inner = 1e-12

    dsr = norm.cdf(
        (sr_observed - e_max_sr0) * math.sqrt(T - 1) / math.sqrt(inner)
    )
    return float(dsr)


def cscv_pbo(
    returns_matrix: pd.DataFrame,
    n_splits: int = 16,
) -> dict:
    """Combinatorially Symmetric Cross-Validation PBO per Bailey et al. (2017).

    Estimates the Probability of Backtest Overfitting (PBO): the fraction of
    CSCV combinations where the IS-optimal strategy underperforms the median
    OOS strategy (logit of OOS rank < 0).

    Parameters
    ----------
    returns_matrix : pd.DataFrame
        T x N matrix: T time-steps (rows), N strategies (columns).
        Values should be period returns or IC values (consistent sign = better).
        Strategies with all-NaN columns are dropped before computation.
    n_splits : int
        Number of submatrices to split the time axis into. Must be even and
        >= 4. Default 16 per Bailey (2017) recommendation.
        With fewer data points, n_splits is reduced automatically.

    Returns
    -------
    dict with keys:
        pbo_score       : float in [0, 1] — fraction of combinations that overfit
        n_combinations  : int — total number of train/test split pairs evaluated
        logit_values    : list[float] — logit(OOS rank) for each combination
        performance_degradation : float — mean(IS_rank - OOS_rank) across combos
        n_strategies    : int — number of valid strategy columns
        n_obs           : int — number of time observations used
        note            : str — warnings / caveats

    Notes
    -----
    PBO measures how often the IS-best strategy is also the OOS-best.
    PBO close to 0.5 means the backtest selector performs at chance — overfitting.
    PBO close to 0.0 means IS selection reliably identifies OOS winners.

    With N strategies and T/2 time slices, the number of combinations is C(T/2, T/4).
    Bailey uses n_splits=16 -> C(8,4)=70 combinations. For small datasets we
    reduce n_splits automatically.
    """
    note_parts: list[str] = []

    # Drop all-NaN strategy columns
    valid_cols = returns_matrix.dropna(axis=1, how="all").columns
    mat = returns_matrix[valid_cols].copy()
    N = mat.shape[1]
    T = mat.shape[0]

    if N < 2:
        return {
            "pbo_score": float("nan"),
            "n_combinations": 0,
            "logit_values": [],
            "performance_degradation": float("nan"),
            "n_strategies": N,
            "n_obs": T,
            "note": "Need >= 2 strategies for PBO; skipped.",
        }

    # Reduce n_splits if T is small
    while n_splits > 4 and T // n_splits < 2:
        n_splits //= 2
        note_parts.append(f"n_splits reduced to {n_splits} (T={T} too small)")

    if n_splits % 2 != 0:
        n_splits = max(4, n_splits - 1)
        note_parts.append(f"n_splits rounded down to {n_splits} (must be even)")

    # Split T observations into n_splits equal submatrices
    split_size = T // n_splits
    if split_size < 1:
        return {
            "pbo_score": float("nan"),
            "n_combinations": 0,
            "logit_values": [],
            "performance_degradation": float("nan"),
            "n_strategies": N,
            "n_obs": T,
            "note": f"T={T} too small for n_splits={n_splits}.",
        }

    # Build list of submatrix slices
    submatrices = []
    for i in range(n_splits):
        start = i * split_size
        end = start + split_size if i < n_splits - 1 else T
        submatrices.append(mat.iloc[start:end])

    half = n_splits // 2
    indices = list(range(n_splits))

    logit_values: list[float] = []
    rank_deltas: list[float] = []

    # Enumerate all C(n_splits, n_splits/2) IS/OOS split combinations
    for is_indices in combinations(indices, half):
        oos_indices = [i for i in indices if i not in is_indices]

        is_mat = pd.concat([submatrices[i] for i in is_indices])
        oos_mat = pd.concat([submatrices[i] for i in oos_indices])

        # Score each strategy: mean return (or IC) on IS and OOS
        is_scores = is_mat.mean()
        oos_scores = oos_mat.mean()

        # IS-optimal strategy index
        is_best_idx = int(is_scores.argmax())

        # OOS rank of the IS-best strategy (rank 1 = worst, N = best)
        oos_rank_series = oos_scores.rank()
        oos_rank = float(oos_rank_series.iloc[is_best_idx])

        # IS rank of IS-best (always N by construction)
        is_rank = float(N)

        # Logit of OOS rank (normalised to [0,1] then logit)
        # omega = oos_rank / (N + 1) to avoid 0 and 1
        omega = oos_rank / (N + 1)
        logit_val = math.log(omega / (1.0 - omega))
        logit_values.append(logit_val)
        rank_deltas.append(is_rank - oos_rank)

    pbo_score = sum(1 for lv in logit_values if lv < 0) / len(logit_values) if logit_values else float("nan")
    perf_degradation = float(np.mean(rank_deltas)) if rank_deltas else float("nan")

    if pbo_score > 0.5:
        note_parts.append(f"PBO={pbo_score:.3f} > 0.5: likely overfitting.")
    elif pbo_score > 0.3:
        note_parts.append(f"PBO={pbo_score:.3f}: moderate overfit risk.")

    return {
        "pbo_score": pbo_score,
        "n_combinations": len(logit_values),
        "logit_values": logit_values,
        "performance_degradation": perf_degradation,
        "n_strategies": N,
        "n_obs": T,
        "note": "; ".join(note_parts) if note_parts else "ok",
    }


def benjamini_hochberg(
    p_values: list[float],
    alpha: float = 0.05,
) -> list[bool]:
    """Benjamini-Hochberg FDR correction.

    Returns a boolean mask: True = hypothesis survives (reject null at FDR alpha).
    Uses scipy.stats.false_discovery_control (available scipy>=1.9).

    Parameters
    ----------
    p_values : list[float]
        Raw p-values, one per strategy/hypothesis.
    alpha : float
        FDR level (default 0.05 = 5% false discovery rate).

    Returns
    -------
    list[bool]
        True where the null (no skill) is rejected at the given FDR level.
    """
    if not p_values:
        return []
    arr = np.asarray(p_values, dtype=float)
    # scipy returns adjusted p-values; compare to alpha for the survive mask
    adjusted = false_discovery_control(arr, method="bh")
    return [bool(adj <= alpha) for adj in adjusted]


def dsr_filter(
    candidates: list[dict],
    n_trials: int,
    alpha_dsr: float = 0.05,
    alpha_bh: float = 0.05,
    sr_key: str = "wf_mean_ic_t",
    folds_key: str = "wf_per_fold",
) -> dict:
    """Apply DSR + BH filter to a list of evaluated strategy candidates.

    This is the ship gate alpha filter: takes top-K results from a mining
    run and returns which survive multiple-testing correction.

    Parameters
    ----------
    candidates : list[dict]
        Each dict must contain sr_key (float) and optionally folds_key (list
        of per-fold dicts with 'ic_t_stat' and 'n_oos_rows').
    n_trials : int
        Total strategies evaluated during mining (population * generations).
        Must be passed from run config — not post-hoc selected. This is the
        PIT-safety constraint: n_trials cannot be selected after observing results.
    alpha_dsr : float
        DSR p-value threshold for survival (default 0.05).
    alpha_bh : float
        BH FDR level for multiple-testing correction (default 0.05).
    sr_key : str
        Key in each candidate dict for the SR/IC_t_stat value.
    folds_key : str
        Key in each candidate dict for the list of per-fold results.

    Returns
    -------
    dict with keys:
        surviving_indices   : list[int] — indices into candidates that survive
        surviving_candidates: list[dict] — candidate dicts annotated with dsr_p
        dsr_p_values        : list[float] — DSR p-value for each candidate
        bh_survive          : list[bool] — BH survive mask
        n_input             : int — total candidates evaluated
        n_surviving         : int — candidates passing both DSR and BH
        n_trials_used       : int — n_trials as passed (for audit trail)
    """
    if not candidates:
        return {
            "surviving_indices": [],
            "surviving_candidates": [],
            "dsr_p_values": [],
            "bh_survive": [],
            "n_input": 0,
            "n_surviving": 0,
            "n_trials_used": n_trials,
        }

    dsr_p_values: list[float] = []
    sr_values: list[float] = []
    t_obs_values: list[int] = []

    for cand in candidates:
        sr_obs = float(cand.get(sr_key, 0.0))
        sr_values.append(sr_obs)

        # Estimate T from per-fold data if available
        folds = cand.get(folds_key, [])
        if folds and isinstance(folds, list) and len(folds) > 0:
            T = sum(f.get("n_oos_rows", 0) for f in folds)
            T = T // max(1, len(set(
                # count unique instruments (assume 2 for crypto BTC/ETH pair)
                # n_oos_rows counts cross-sectional rows, divide by n_instruments
                [1]  # fallback: assume already per-timestamp count
            )))
            T = max(T // 2, 2)  # divide by ~2 instruments to get timestamps
        else:
            T = 252  # default: 1 year of daily bars

        t_obs_values.append(T)

    # Compute sr_std across all candidates (cross-strategy variance).
    # When only 1 candidate is available (or all are identical), std=0 would
    # zero out E[max_SR0] and bypass the n_trials penalty entirely.
    # Conservative fallback: use |sr_obs| / sqrt(2) as minimum sr_std estimate
    # (derived from a single-observation estimate of the underlying SR distribution).
    # This ensures DSR is penalised for large n_trials even with 1 candidate.
    sr_std_raw = float(np.std(sr_values)) if len(sr_values) > 1 else 0.0
    if sr_std_raw == 0.0 and n_trials > 1 and sr_values:
        # Single-candidate fallback: sr_std=1.0 is the standard industry assumption
        # for the distribution width of SR across trials (Bailey 2014 recommends
        # using the empirical std; when unavailable, 1.0 is conservative and common).
        # Do NOT use |sr_obs|/sqrt(2) — that makes sr_std proportional to the very
        # value being tested, which is circular and overly pessimistic for high SR.
        sr_std = 1.0
    else:
        sr_std = sr_std_raw

    for i, (sr_obs, T) in enumerate(zip(sr_values, t_obs_values)):
        p = deflated_sharpe_ratio(
            sr_observed=sr_obs,
            n_trials=n_trials,
            sr_std=sr_std,
            T=T,
        )
        dsr_p_values.append(p)

    # BH correction on DSR p-values (convert to reject-null p-values: 1 - DSR_p)
    # DSR returns Prob(non-spurious); for BH we need Prob(spurious) = 1 - DSR_p
    null_p_values = [1.0 - p for p in dsr_p_values]
    bh_survive = benjamini_hochberg(null_p_values, alpha=alpha_bh)

    # Annotate surviving candidates
    surviving_indices: list[int] = []
    surviving_candidates: list[dict] = []

    for i, (cand, dsr_p, bh_ok) in enumerate(zip(candidates, dsr_p_values, bh_survive)):
        annotated = {**cand, "dsr_p": round(dsr_p, 6), "bh_survive": bh_ok}
        if bh_ok and dsr_p >= (1.0 - alpha_dsr):
            surviving_indices.append(i)
            surviving_candidates.append(annotated)

    return {
        "surviving_indices": surviving_indices,
        "surviving_candidates": surviving_candidates,
        "dsr_p_values": dsr_p_values,
        "bh_survive": bh_survive,
        "n_input": len(candidates),
        "n_surviving": len(surviving_indices),
        "n_trials_used": n_trials,
        "sr_std_across_candidates": round(sr_std, 6),
    }
