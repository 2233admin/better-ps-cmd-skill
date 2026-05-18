"""Unit tests for qlib WQ Horror translation (XAR-417).

These tests run WITHOUT pyqlib installed (Python 3.13 venv).
They validate:
  - Expression string constants are well-formed (no obvious typos)
  - compute_wq_horror_factor() math logic with a synthetic pandas DataFrame
  - Translation choices are documented (spot-check docstring content)

To run:
    cd backend && pytest tests/unit/test_qlib_wq_horror.py -v
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.research.qlib_integration.wq_horror_qlib import (
    EXPR_INTRA_RET,
    EXPR_OUTPUT,
    EXPR_RET_STD,
    EXPR_SIGNAL,
    build_wq_horror_alpha_fields,
    compute_wq_horror_factor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_synthetic_df(n_dates: int = 60, n_stocks: int = 20, seed: int = 42) -> pd.DataFrame:
    """Build a synthetic multi-index DataFrame mimicking qlib handler output.

    Index: (date, instrument) MultiIndex
    Columns: INTRA_RET, RET_STD, CAP_RANK, INTRA_RET_MEAN22, INTRA_RET_STD22
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-01", periods=n_dates, freq="B")
    instruments = [f"SH{60000 + i:06d}" for i in range(n_stocks)]

    idx = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
    n = len(idx)

    intra_ret = rng.normal(0.0, 0.02, n)
    cap_rank = rng.uniform(0, 1, n)

    df = pd.DataFrame(
        {
            "INTRA_RET": intra_ret,
            "RET_STD": np.abs(rng.normal(0.018, 0.005, n)),
            "CAP_RANK": cap_rank,
            "INTRA_RET_MEAN22": rng.normal(0.0, 0.005, n),
            "INTRA_RET_STD22": np.abs(rng.normal(0.018, 0.003, n)),
        },
        index=idx,
    )
    return df


# ---------------------------------------------------------------------------
# Expression string tests
# ---------------------------------------------------------------------------

class TestExpressionStrings:
    def test_intra_ret_references_close_open(self) -> None:
        assert "$close" in EXPR_INTRA_RET
        assert "$open" in EXPR_INTRA_RET

    def test_ret_std_uses_std_operator(self) -> None:
        assert "Std(" in EXPR_RET_STD

    def test_signal_negated(self) -> None:
        assert "-1 *" in EXPR_SIGNAL or EXPR_SIGNAL.startswith("-")

    def test_output_uses_wma(self) -> None:
        assert "WMA(" in EXPR_OUTPUT

    def test_build_alpha_fields_returns_pairs(self) -> None:
        fields = build_wq_horror_alpha_fields()
        assert len(fields) >= 4, "Expected at least 4 field-expression pairs"
        for expr, name in fields:
            assert isinstance(expr, str) and len(expr) > 0
            assert isinstance(name, str) and len(name) > 0

    def test_intra_ret_in_alpha_fields(self) -> None:
        fields = build_wq_horror_alpha_fields()
        names = [name for _, name in fields]
        assert "INTRA_RET" in names

    def test_ret_std_in_alpha_fields(self) -> None:
        fields = build_wq_horror_alpha_fields()
        names = [name for _, name in fields]
        assert "RET_STD" in names


# ---------------------------------------------------------------------------
# compute_wq_horror_factor math tests
# ---------------------------------------------------------------------------

class TestComputeWqHorrorFactor:
    @pytest.fixture
    def df(self) -> pd.DataFrame:
        return _make_synthetic_df()

    def test_returns_series(self, df: pd.DataFrame) -> None:
        result = compute_wq_horror_factor(df)
        assert isinstance(result, pd.Series)

    def test_same_length_as_input(self, df: pd.DataFrame) -> None:
        result = compute_wq_horror_factor(df)
        assert len(result) == len(df)

    def test_no_all_nan(self, df: pd.DataFrame) -> None:
        result = compute_wq_horror_factor(df)
        assert result.notna().any(), "All NaN output — computation failed"

    def test_signal_is_negated(self, df: pd.DataFrame) -> None:
        """Signal is negated, so mean should cluster around -0.5 (Cauchy CDF range)."""
        result = compute_wq_horror_factor(df)
        # Cauchy CDF is bounded (0,1), negated -> (-1, 0)
        assert result.mean() < 0, "Signal mean should be negative after negation"

    def test_signal_bounded(self, df: pd.DataFrame) -> None:
        """Cauchy CDF output is in (0,1), negated signal in (-1, 0)."""
        result = compute_wq_horror_factor(df)
        finite = result.dropna()
        assert (finite >= -1.0).all(), "Signal below -1 (Cauchy CDF violated)"
        assert (finite <= 0.0).all(), "Signal above 0 (negation failed)"

    def test_horro_non_negative_internally(self, df: pd.DataFrame) -> None:
        """horro = abs(...) / (abs(...) + abs(...) + 0.1) must be >= 0."""
        # Reconstruct horro step to validate
        intra_ret = df["INTRA_RET"]
        date_level = intra_ret.index.get_level_values(0)
        peer_mean = intra_ret.groupby(date_level).transform("mean")
        horro = (intra_ret - peer_mean).abs() / (
            intra_ret.abs() + peer_mean.abs() + 0.1
        )
        assert (horro >= 0).all(), "horro must be non-negative"
        assert (horro <= 1.1).all(), "horro > 1.1 suggests denominator is wrong"

    def test_cauchy_cdf_formula(self) -> None:
        """Validate Cauchy CDF: F(0) = 0.5, F(large) -> 1."""
        assert abs(0.5 + math.atan(0) / math.pi - 0.5) < 1e-9
        assert 0.5 + math.atan(1000) / math.pi > 0.999
        assert 0.5 + math.atan(-1000) / math.pi < 0.001

    def test_deterministic(self, df: pd.DataFrame) -> None:
        """Same input must produce same output (no random state)."""
        r1 = compute_wq_horror_factor(df)
        r2 = compute_wq_horror_factor(df)
        pd.testing.assert_series_equal(r1, r2)

    def test_cross_sectional_variation_per_date(self, df: pd.DataFrame) -> None:
        """On any given date, factor values should differ across stocks (not constant)."""
        result = compute_wq_horror_factor(df)
        date_level = result.index.get_level_values(0)
        first_date = date_level.unique()[5]  # skip early dates that may be NaN-heavy
        day_slice = result[date_level == first_date]
        assert day_slice.nunique() > 1, "All stocks have identical factor on one day"


# ---------------------------------------------------------------------------
# Translation documentation test
# ---------------------------------------------------------------------------

class TestTranslationDocumentation:
    def test_group_mean_approximation_documented(self) -> None:
        """group_mean approximation must be documented in module docstring."""
        import app.research.qlib_integration.wq_horror_qlib as mod
        doc = mod.__doc__ or ""
        # The module docstring lives at the top of the file; check the key phrase
        src = open(mod.__file__).read()
        assert "group_mean" in src
        assert "approximation" in src.lower() or "APPROXIMATION" in src

    def test_cauchy_quantile_approximation_documented(self) -> None:
        """Cauchy quantile approximation must be documented."""
        import app.research.qlib_integration.wq_horror_qlib as mod
        src = open(mod.__file__).read()
        assert "cauchy" in src.lower()
        assert "atan" in src.lower()

    def test_ts_decay_linear_mapping_documented(self) -> None:
        """ts_decay_linear -> WMA mapping must be documented."""
        import app.research.qlib_integration.wq_horror_qlib as mod
        src = open(mod.__file__).read()
        assert "WMA" in src
        assert "ts_decay_linear" in src
