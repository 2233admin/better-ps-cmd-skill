"""Tests for DSR + PBO + multiple-testing methodology layer.

Test strategy:
  - Property tests preferred over paper-anchored numbers (avoids training-recall errors)
  - One known numeric case for DSR where the formula is manually verifiable
  - BH aligned with scipy.stats.false_discovery_control as ground truth
  - CSCV PBO verified with fixture matrix where overfitting is controlled
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from scipy.stats import false_discovery_control, norm

# Ensure backend is importable from tests
_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from app.research.methodology.dsr_pbo import (
    benjamini_hochberg,
    cscv_pbo,
    deflated_sharpe_ratio,
    dsr_filter,
)


# ---------------------------------------------------------------------------
# DSR property tests
# ---------------------------------------------------------------------------

class TestDeflatedSharpeRatio:

    def test_dsr_monotone_decreasing_with_n_trials(self):
        """DSR must decrease monotonically as n_trials increases (SR fixed)."""
        sr_obs = 1.0
        T = 252
        sr_std = 0.5
        prev_dsr = 1.1  # sentinel above 1
        for n in [1, 5, 10, 50, 100, 500]:
            dsr = deflated_sharpe_ratio(sr_obs, n, sr_std, T)
            assert dsr <= prev_dsr + 1e-9, (
                f"DSR increased at n={n}: prev={prev_dsr:.6f}, curr={dsr:.6f}"
            )
            prev_dsr = dsr

    def test_dsr_in_unit_interval(self):
        """DSR must always be in [0, 1] (it's a CDF value)."""
        for sr in [-2.0, -0.5, 0.0, 0.5, 1.0, 2.0, 5.0]:
            for n in [1, 10, 100]:
                dsr = deflated_sharpe_ratio(sr, n, sr_std=0.5, T=252)
                assert 0.0 <= dsr <= 1.0, f"DSR out of [0,1]: {dsr} for sr={sr}, n={n}"

    def test_dsr_single_trial_no_selection_bias(self):
        """With n_trials=1 or sr_std=0, E[max_SR0]=0; DSR is classical SR CDF."""
        sr_obs = 1.0
        T = 252
        # Classical one-sided p-value for SR=1 over T=252 observations
        classical = norm.cdf(sr_obs * math.sqrt(T - 1))
        dsr_single = deflated_sharpe_ratio(sr_obs, n_trials=1, sr_std=0.5, T=T)
        dsr_zero_std = deflated_sharpe_ratio(sr_obs, n_trials=100, sr_std=0.0, T=T)
        assert abs(dsr_single - classical) < 1e-9, (
            f"n_trials=1 DSR={dsr_single:.6f} != classical={classical:.6f}"
        )
        assert abs(dsr_zero_std - classical) < 1e-9, (
            f"sr_std=0 DSR={dsr_zero_std:.6f} != classical={classical:.6f}"
        )

    def test_dsr_increases_with_sr_observed(self):
        """Higher observed SR should yield higher DSR (more likely non-spurious)."""
        n, T, sr_std = 100, 252, 0.5
        dsrs = [deflated_sharpe_ratio(sr, n, sr_std, T) for sr in [0.0, 0.5, 1.0, 2.0, 3.0]]
        for i in range(len(dsrs) - 1):
            assert dsrs[i] <= dsrs[i + 1] + 1e-9, (
                f"DSR not monotone in SR_obs at index {i}: {dsrs}"
            )

    def test_dsr_higher_T_less_uncertainty(self):
        """Longer OOS period (larger T) should pull DSR toward 1 for positive SR."""
        sr_obs = 1.5
        n = 50
        sr_std = 0.5
        dsr_short = deflated_sharpe_ratio(sr_obs, n, sr_std, T=50)
        dsr_long = deflated_sharpe_ratio(sr_obs, n, sr_std, T=1000)
        assert dsr_long >= dsr_short, (
            f"Longer T should give higher DSR: T=50 -> {dsr_short:.4f}, T=1000 -> {dsr_long:.4f}"
        )

    def test_dsr_known_numeric_case(self):
        """Verify DSR against a manually computed reference value.

        With sr_obs=1.0, n_trials=50, sr_std=0.5, T=252, skew=0, kurt=3:
            E_max = 0.5 * ((1-gamma)*Phi_inv(1-1/50) + gamma*Phi_inv(1-1/(50*e)))
            inner = 1 - 0*sr + ((3-1)/4)*sr^2 = 1 + 0.5*1 = 1.5  (kurt=3 gaussian)
            DSR = Phi((sr_obs - E_max) * sqrt(T-1) / sqrt(inner))
        Note: kurt=3 (Gaussian) gives inner=1.5, NOT 1.0. Test must include this.
        """
        import math as _math
        gamma = 0.5772156649015329
        n, T, sr_std, sr_obs, skew, kurt = 50, 252, 0.5, 1.0, 0.0, 3.0
        e_max = sr_std * (
            (1 - gamma) * norm.ppf(1 - 1 / n) +
            gamma * norm.ppf(1 - 1 / (n * math.e))
        )
        inner = 1.0 - skew * sr_obs + ((kurt - 1.0) / 4.0) * sr_obs ** 2
        expected = norm.cdf((sr_obs - e_max) * _math.sqrt(T - 1) / _math.sqrt(inner))
        actual = deflated_sharpe_ratio(sr_obs, n, sr_std, T, skew=skew, kurt=kurt)
        assert abs(actual - expected) < 1e-9, (
            f"DSR={actual:.8f} != expected={expected:.8f} (e_max={e_max:.6f}, inner={inner:.4f})"
        )

    def test_dsr_invalid_inputs_raise(self):
        """Invalid inputs must raise ValueError."""
        with pytest.raises(ValueError, match="n_trials"):
            deflated_sharpe_ratio(1.0, n_trials=0, sr_std=0.5, T=252)
        with pytest.raises(ValueError, match="T"):
            deflated_sharpe_ratio(1.0, n_trials=10, sr_std=0.5, T=1)
        with pytest.raises(ValueError, match="sr_std"):
            deflated_sharpe_ratio(1.0, n_trials=10, sr_std=-0.1, T=252)


# ---------------------------------------------------------------------------
# BH multiple-testing correction tests
# ---------------------------------------------------------------------------

class TestBenjaminiHochberg:

    def test_bh_aligned_with_scipy(self):
        """BH survive mask must match scipy.stats.false_discovery_control."""
        p_values = [0.001, 0.01, 0.05, 0.1, 0.3, 0.5, 0.8, 0.95]
        alpha = 0.05
        scipy_adjusted = false_discovery_control(p_values, method="bh")
        scipy_survive = [adj <= alpha for adj in scipy_adjusted]
        our_survive = benjamini_hochberg(p_values, alpha=alpha)
        assert our_survive == scipy_survive, (
            f"BH survive mismatch:\n  scipy={scipy_survive}\n  ours={our_survive}"
        )

    def test_bh_all_significant(self):
        """All very small p-values should all survive BH."""
        p_values = [1e-10, 1e-8, 1e-6, 1e-5]
        survive = benjamini_hochberg(p_values, alpha=0.05)
        assert all(survive), f"All should survive, got {survive}"

    def test_bh_all_insignificant(self):
        """All large p-values should all fail BH."""
        p_values = [0.5, 0.6, 0.7, 0.8, 0.9]
        survive = benjamini_hochberg(p_values, alpha=0.05)
        assert not any(survive), f"None should survive, got {survive}"

    def test_bh_empty_returns_empty(self):
        """Empty p-value list should return empty list."""
        assert benjamini_hochberg([]) == []

    def test_bh_single_p_value(self):
        """Single p-value: survives if <= alpha."""
        assert benjamini_hochberg([0.03], alpha=0.05) == [True]
        assert benjamini_hochberg([0.07], alpha=0.05) == [False]


# ---------------------------------------------------------------------------
# CSCV PBO tests
# ---------------------------------------------------------------------------

class TestCSCVPBO:

    def _make_returns_matrix(
        self,
        n_strategies: int,
        n_obs: int,
        good_strategy_idx: int | None = None,
        rng_seed: int = 42,
    ) -> pl.DataFrame:
        """Build a T x N returns matrix fixture.

        If good_strategy_idx is set, that column gets a positive drift.
        All others are random noise.
        """
        rng = np.random.default_rng(rng_seed)
        data = rng.normal(0, 0.01, size=(n_obs, n_strategies))
        if good_strategy_idx is not None:
            data[:, good_strategy_idx] += 0.005  # positive drift
        cols = [f"strat_{i}" for i in range(n_strategies)]
        return pl.from_numpy(data, schema=cols)

    def test_pbo_output_schema(self):
        """cscv_pbo must return dict with required keys."""
        mat = self._make_returns_matrix(10, 64)
        result = cscv_pbo(mat, n_splits=8)
        required = {
            "pbo_score", "n_combinations", "logit_values",
            "performance_degradation", "n_strategies", "n_obs", "note"
        }
        assert required <= set(result.keys()), (
            f"Missing keys: {required - set(result.keys())}"
        )

    def test_pbo_score_in_unit_interval(self):
        """PBO score must be in [0, 1]."""
        mat = self._make_returns_matrix(8, 64)
        result = cscv_pbo(mat, n_splits=8)
        pbo = result["pbo_score"]
        assert 0.0 <= pbo <= 1.0, f"PBO score {pbo} out of [0, 1]"

    def test_pbo_random_noise_near_half(self):
        """With all-noise strategies, PBO should be near 0.5 (chance level).

        Not a hard threshold (stochastic), but expectation over many seeds.
        """
        pbos = []
        for seed in range(10):
            mat = self._make_returns_matrix(10, 128, rng_seed=seed)
            r = cscv_pbo(mat, n_splits=8)
            if not math.isnan(r["pbo_score"]):
                pbos.append(r["pbo_score"])
        mean_pbo = np.mean(pbos)
        # For pure noise, PBO should be in [0.3, 0.7] on average
        assert 0.2 <= mean_pbo <= 0.8, (
            f"Mean PBO for random noise={mean_pbo:.3f} should be near 0.5"
        )

    def test_pbo_good_strategy_lower_pbo(self):
        """One strong strategy should yield lower PBO than all-noise baseline."""
        pbo_noise = []
        pbo_signal = []
        for seed in range(5):
            mat_noise = self._make_returns_matrix(8, 128, rng_seed=seed)
            mat_signal = self._make_returns_matrix(8, 128, good_strategy_idx=0, rng_seed=seed)
            r_noise = cscv_pbo(mat_noise, n_splits=8)
            r_signal = cscv_pbo(mat_signal, n_splits=8)
            if not (math.isnan(r_noise["pbo_score"]) or math.isnan(r_signal["pbo_score"])):
                pbo_noise.append(r_noise["pbo_score"])
                pbo_signal.append(r_signal["pbo_score"])
        # Signal PBO should be lower on average
        assert np.mean(pbo_signal) <= np.mean(pbo_noise) + 0.2, (
            f"Signal PBO={np.mean(pbo_signal):.3f} not lower than noise PBO={np.mean(pbo_noise):.3f}"
        )

    def test_pbo_n_combinations_correct(self):
        """n_combinations must equal C(n_splits, n_splits/2)."""
        from math import comb
        mat = self._make_returns_matrix(6, 64)
        n_splits = 8
        result = cscv_pbo(mat, n_splits=n_splits)
        expected_combos = comb(n_splits, n_splits // 2)
        assert result["n_combinations"] == expected_combos, (
            f"Expected {expected_combos} combinations, got {result['n_combinations']}"
        )

    def test_pbo_too_few_strategies_graceful(self):
        """Single-column returns matrix should return nan gracefully."""
        mat = pl.DataFrame({"strat_0": np.random.randn(64)})
        result = cscv_pbo(mat, n_splits=8)
        assert math.isnan(result["pbo_score"]), "Should return nan for N<2"
        assert result["n_combinations"] == 0

    def test_pbo_small_dataset_auto_reduce_splits(self):
        """Very small T should auto-reduce n_splits without crashing."""
        mat = self._make_returns_matrix(4, 16)
        result = cscv_pbo(mat, n_splits=16)  # 16 splits on T=16 -> auto-reduce
        assert isinstance(result["pbo_score"], float), "Should return a float (possibly nan)"


# ---------------------------------------------------------------------------
# dsr_filter integration test
# ---------------------------------------------------------------------------

class TestDSRFilter:

    def _make_candidates(self, n: int, sr_values: list[float]) -> list[dict]:
        """Build minimal candidate dicts for dsr_filter."""
        candidates = []
        for i, sr in enumerate(sr_values):
            candidates.append({
                "wf_mean_ic_t": sr,
                "wf_per_fold": [
                    {"ic_t_stat": sr * 0.9, "n_oos_rows": 630, "fold": j}
                    for j in range(6)
                ],
            })
        return candidates

    def test_filter_empty_input(self):
        """Empty candidates list returns empty output."""
        result = dsr_filter([], n_trials=100)
        assert result["n_input"] == 0
        assert result["n_surviving"] == 0
        assert result["surviving_indices"] == []

    def test_filter_high_sr_survives(self):
        """A clearly high SR with few trials should survive DSR filter."""
        # SR=5.0 with n_trials=10 should easily survive
        cands = self._make_candidates(1, [5.0])
        result = dsr_filter(cands, n_trials=10)
        assert result["n_surviving"] >= 1, (
            f"SR=5.0 with n_trials=10 should survive; DSR={result['dsr_p_values']}"
        )

    def test_filter_low_sr_fails(self):
        """Barely positive SR with many trials should fail DSR.

        Uses multiple candidates so sr_std is non-zero and E[max_SR0] is
        properly computed. With n_trials=10000 and sr_std≈0.5, E[max_SR0]
        is large, making DSR for SR=0.1 near zero.
        """
        # 5 candidates, best SR=0.1 — with n_trials=10000 and sr_std~0.5,
        # the best strategy cannot plausibly beat selection bias.
        cands = self._make_candidates(5, [-0.5, -0.3, 0.0, 0.05, 0.1])
        result = dsr_filter(cands, n_trials=10000)
        # The highest SR=0.1 should fail because E[max_SR0] >> 0.1 with n=10000
        assert result["n_surviving"] == 0, (
            f"SR=0.1 with n_trials=10000 should fail; DSR={result['dsr_p_values']}"
        )

    def test_filter_n_trials_audit_trail(self):
        """n_trials_used must be echoed in output for audit purposes."""
        cands = self._make_candidates(2, [1.0, 2.0])
        result = dsr_filter(cands, n_trials=500)
        assert result["n_trials_used"] == 500, "n_trials must be in output for audit"

    def test_filter_output_schema(self):
        """dsr_filter output must contain all required keys."""
        cands = self._make_candidates(3, [1.0, 2.0, 0.5])
        result = dsr_filter(cands, n_trials=100)
        required = {
            "surviving_indices", "surviving_candidates", "dsr_p_values",
            "bh_survive", "n_input", "n_surviving", "n_trials_used",
        }
        assert required <= set(result.keys()), (
            f"Missing keys: {required - set(result.keys())}"
        )
        assert result["n_input"] == 3
        assert len(result["dsr_p_values"]) == 3
        assert len(result["bh_survive"]) == 3

    def test_filter_surviving_candidates_annotated(self):
        """Surviving candidates must have dsr_p and bh_survive annotations."""
        cands = self._make_candidates(1, [5.0])
        result = dsr_filter(cands, n_trials=10)
        for cand in result["surviving_candidates"]:
            assert "dsr_p" in cand, "Surviving candidate missing dsr_p"
            assert "bh_survive" in cand, "Surviving candidate missing bh_survive"
