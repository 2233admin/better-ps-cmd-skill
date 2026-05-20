"""Tests for methodology.regime — PIT invariant + smoke.

PIT invariant: row t of expanding_fit_hmm output must be unaffected by any
returns at row > t. This is the load-bearing safety property.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.research.methodology.regime import expanding_fit_hmm, expanding_fit_hmm_batched


def _synthetic_regime_returns(seed: int = 0) -> pl.DataFrame:
    """500 days: 200 bull (mu=0.001) + 150 bear (mu=-0.002) + 150 neutral (mu=0)."""
    rng = np.random.default_rng(seed)
    bull = rng.normal(0.001, 0.01, 200)
    bear = rng.normal(-0.002, 0.015, 150)
    neut = rng.normal(0.0, 0.008, 150)
    r = np.concatenate([bull, bear, neut])
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(len(r))]
    return pl.DataFrame({"date": dates, "ret": r})


def test_output_schema():
    df = _synthetic_regime_returns()
    out = expanding_fit_hmm(df, min_train=120, refit_every=30)
    assert out.columns == ["date", "p_bear", "p_neutral", "p_bull", "state_argmax"]
    assert out.height == df.height
    # warmup rows are NaN
    assert np.isnan(out["p_bear"][0])
    # post-warmup rows sum to ~1
    last = out.row(out.height - 1, named=True)
    s = last["p_bear"] + last["p_neutral"] + last["p_bull"]
    assert abs(s - 1.0) < 1e-6


def test_pit_invariant_truncation():
    """Row t computed on returns[:N] == row t computed on returns[:N+k] for any k>=0.

    This catches the classic leak: Viterbi smoothing or full-series predict_proba.
    """
    df = _synthetic_regime_returns()
    full = expanding_fit_hmm(df, min_train=120, refit_every=30, seed=42)
    # Truncate the last 50 rows; everything before that must match exactly.
    truncated_input = df.head(df.height - 50)
    truncated = expanding_fit_hmm(
        truncated_input, min_train=120, refit_every=30, seed=42
    )
    n = truncated.height
    # The probability rows for date t in `truncated` must equal those in `full`
    # at the same date — modulo HMM EM converging to identical local optima on
    # the same training slice with same seed.
    for col in ("p_bear", "p_neutral", "p_bull"):
        full_col = full[col].head(n).to_numpy()
        trunc_col = truncated[col].to_numpy()
        diff = np.nanmax(np.abs(full_col - trunc_col))
        assert diff < 1e-10, (
            f"PIT leak: {col} differs by {diff} between full and truncated runs"
        )


def test_probs_sum_to_one():
    df = _synthetic_regime_returns()
    out = expanding_fit_hmm(df, min_train=120, refit_every=30)
    post = out.filter(pl.col("p_bear").is_not_nan())
    sums = (post["p_bear"] + post["p_neutral"] + post["p_bull"]).to_numpy()
    assert np.allclose(sums, 1.0, atol=1e-6)


def test_state_labels_ordered_by_mean_on_clear_signal():
    """Once HMM sees all 3 regimes (we feed enough warmup), high-confidence
    rows (p>0.7) should be ordered correctly by mean return."""
    rng = np.random.default_rng(0)
    bull = rng.normal(0.002, 0.005, 400)
    bear = rng.normal(-0.003, 0.012, 300)
    neut = rng.normal(0.0, 0.004, 300)
    r = np.concatenate([bull, bear, neut, bull, bear, neut])
    dates = [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(len(r))]
    df = pl.DataFrame({"date": dates, "ret": r})
    out = expanding_fit_hmm(df, min_train=1000, refit_every=60, seed=0)
    df_idx = df.with_row_index("row_idx")
    out_idx = out.with_row_index("row_idx").filter(
        pl.col("p_bear").is_not_nan()
    )
    joined = df_idx.join(out_idx, on="row_idx", how="inner")
    # Only look at high-confidence rows.
    high_bull = joined.filter(pl.col("p_bull") > 0.7)["ret"]
    high_bear = joined.filter(pl.col("p_bear") > 0.7)["ret"]
    # Need enough high-confidence calls on both sides
    if high_bull.len() < 10 or high_bear.len() < 10:
        pytest.skip(f"insufficient high-conf rows bull={high_bull.len()} bear={high_bear.len()}")
    assert high_bull.mean() > high_bear.mean(), (
        f"label ordering broken on high-conf rows: "
        f"bull_mean={high_bull.mean()} bear_mean={high_bear.mean()}"
    )


def test_warmup_enforced():
    df = _synthetic_regime_returns()
    with pytest.raises(ValueError, match="need >="):
        expanding_fit_hmm(df.head(50), min_train=120)


# ---------- Batched (multi-symbol) cross-section HMM ----------


def _synthetic_batched_panel(B: int = 6, T: int = 300, D: int = 3, seed: int = 0):
    """Random panel of B series, T steps, D features for batched smoke."""
    rng = np.random.default_rng(seed)
    out = np.empty((B, T, D), dtype=np.float32)
    bull_n = (2 * T) // 3
    bear_n = T - bull_n
    for b in range(B):
        bull = rng.normal(0.001, 0.01, (bull_n, D))
        bear = rng.normal(-0.002, 0.015, (bear_n, D))
        out[b] = np.concatenate([bull, bear]).astype(np.float32)
    return out


def test_batched_output_schema_cpu():
    panel = _synthetic_batched_panel(B=4, T=250, D=2)
    probs = expanding_fit_hmm_batched(
        panel, n_states=3, min_train=120, refit_every=30, device="cpu", max_iter=5
    )
    assert probs.shape == (4, 250, 3)
    assert np.isnan(probs[:, :120, :]).all(), "warmup must be NaN"
    assert not np.isnan(probs[:, 120:, :]).any(), "post-warmup must be clean"


def test_batched_probs_sum_to_one_cpu():
    panel = _synthetic_batched_panel(B=4, T=250, D=2)
    probs = expanding_fit_hmm_batched(
        panel, n_states=3, min_train=120, refit_every=30, device="cpu", max_iter=5
    )
    sums = probs[:, 120:, :].sum(axis=-1)
    assert np.allclose(sums, 1.0, atol=1e-4)


def test_batched_warmup_enforced():
    panel = _synthetic_batched_panel(B=2, T=50, D=2)
    with pytest.raises(ValueError, match="need >="):
        expanding_fit_hmm_batched(panel, min_train=120, device="cpu")


def test_batched_gpu_smoke_if_available():
    """Skip if no CUDA; smoke that GPU path runs and matches CPU shape."""
    try:
        import torch
    except ImportError:
        pytest.skip("torch not available")
    if not torch.cuda.is_available():
        pytest.skip("CUDA not available")
    panel = _synthetic_batched_panel(B=4, T=250, D=2)
    probs = expanding_fit_hmm_batched(
        panel, n_states=3, min_train=120, refit_every=30, device="cuda", max_iter=5
    )
    assert probs.shape == (4, 250, 3)
    assert not np.isnan(probs[:, 120:, :]).any()
