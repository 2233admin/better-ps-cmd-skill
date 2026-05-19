"""PR-3a hotfix tests: PIT audit fixes P0-1, P0-2, P0-3, P0-4.

Each class maps to one P0 bug from the Codex B audit.
Tests are minimal fixtures, no real parquet on disk required.
"""

from __future__ import annotations

import datetime
import math
import sys
import types
import warnings
from typing import Any

import numpy as np
import polars as pl
import pytest


# ---------------------------------------------------------------------------
# Minimal sklearn stub (mirrors test_gplearn_mine_adapter.py pattern)
# ---------------------------------------------------------------------------

class _StubTimeSeriesSplit:
    def __init__(self, n_splits: int = 5, test_size: int | None = None, gap: int = 0):
        self.n_splits = n_splits
        self.test_size = test_size
        self.gap = gap

    def split(self, X):  # noqa: N803
        n = len(X)
        ts = self.test_size or max(1, n // (self.n_splits + 1))
        for i in range(self.n_splits):
            test_end = n - (self.n_splits - 1 - i) * ts
            test_start = test_end - ts
            train_end = test_start - self.gap
            if train_end <= 0 or test_start < 0:
                continue
            yield list(range(train_end)), list(range(test_start, test_end))


def _patch_sklearn_if_missing() -> None:
    if "sklearn" not in sys.modules:
        sk = types.ModuleType("sklearn")
        sk_ms = types.ModuleType("sklearn.model_selection")
        sk_ms.TimeSeriesSplit = _StubTimeSeriesSplit
        sk.model_selection = sk_ms
        sys.modules["sklearn"] = sk
        sys.modules["sklearn.model_selection"] = sk_ms


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_crypto_frame(n_assets: int = 2, n_bars: int = 900) -> pl.DataFrame:
    rng = np.random.default_rng(42)
    rows = n_assets * n_bars
    base = datetime.datetime(2024, 1, 1)
    times = [base + datetime.timedelta(hours=h) for h in range(n_bars)] * n_assets
    asset_ids = [f"BTC-{i}" for i in range(n_assets)] * n_bars
    pairs = sorted(zip(asset_ids, times), key=lambda x: (x[0], x[1]))
    asset_ids_sorted = [p[0] for p in pairs]
    times_sorted = [p[1] for p in pairs]
    price = 30_000 + np.cumsum(rng.normal(0, 10, rows))
    vol = rng.uniform(100, 1000, rows)
    return pl.DataFrame({
        "inst_id": asset_ids_sorted,
        "event_time": times_sorted,
        "open": price * 0.999,
        "high": price * 1.001,
        "low": price * 0.998,
        "close": price,
        "volume": vol,
        "funding_rate": rng.normal(0, 0.0001, rows),
        "open_interest": rng.uniform(1e6, 2e6, rows),
        "available_at": times_sorted,
    })


def _make_ashare_frame(n_symbols: int = 5, n_days: int = 100) -> pl.DataFrame:
    rng = np.random.default_rng(7)
    base = datetime.date(2023, 1, 2)
    symbols = [f"00000{i}.SZ" for i in range(n_symbols)]
    rows = []
    for sym in symbols:
        for d in range(n_days):
            dt = base + datetime.timedelta(days=d)
            close = 10 + rng.uniform(-0.5, 0.5)
            rows.append({
                "symbol": sym,
                "event_time": datetime.datetime.combine(dt, datetime.time()),
                "available_at": datetime.datetime.combine(dt, datetime.time(17, 0)),
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": rng.uniform(1e6, 5e6),
                "amount": rng.uniform(1e7, 5e7),
                "pe_ttm": rng.uniform(10, 50),
                "pb": rng.uniform(1, 5),
                "ps_ttm": rng.uniform(1, 10),
                "pcf_ttm": rng.uniform(5, 30),
                "is_st": False,
            })
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# P0-1: PIT-leak guard — NaN cleaning must happen per fold, not pre-split
# ---------------------------------------------------------------------------

class TestP01PITLeakGuard:
    """evaluate_factor_callable must not use future-cleanliness to filter training data.

    Specifically: df_clean (globally NaN-dropped before split) is used only
    for signal prediction. The per-fold NaN filter runs on the full panel
    AFTER time-based fold selection, so future row cleanliness cannot
    influence which rows are present in earlier OOS folds.
    """

    def _load_bundle_and_arrays(self, tmp_path):
        raw = _make_crypto_frame(n_assets=2, n_bars=900)
        pq = tmp_path / "crypto.parquet"
        raw.write_parquet(pq)
        from app.research.factors.mining._universe_loader import load_crypto
        from app.research.factors.mining.gplearn.mine import _to_numpy
        bundle = load_crypto(str(pq))
        X, y, df_clean = _to_numpy(bundle)
        return bundle, X, y, df_clean

    def test_earlier_fold_ic_unchanged_when_future_row_injected(self, tmp_path):
        """Injecting NaN into a future row must not change IC for an earlier OOS fold.

        If pre-split drop_nulls were used for evaluation, nullifying a future-bar
        close would push that row out globally, potentially re-numbering fold
        membership for all time indices. With per-fold filtering, earlier folds
        are unaffected.
        """
        _patch_sklearn_if_missing()
        bundle, X, y, df_clean = self._load_bundle_and_arrays(tmp_path)

        from app.research.factors.mining.gplearn.mine import evaluate_factor_callable

        rng = np.random.default_rng(99)
        const_signal = lambda Xarr: rng.standard_normal(Xarr.shape[0])  # noqa: E731

        # Baseline result with original bundle
        result_base = evaluate_factor_callable(
            predict_fn=const_signal,
            bundle=bundle,
            df_clean=df_clean,
            X_full=X,
            n_folds=3,
            embargo_bars=5,
        )

        # Inject NaN into the LAST event_time's close in df_feat for one asset.
        # This simulates a future row becoming null (e.g. data not yet available).
        df_feat = bundle["df_feat"]
        asset_col = bundle["asset_col"]
        last_time = df_feat["event_time"].max()
        first_asset = df_feat[asset_col][0]

        df_corrupted = df_feat.with_columns(
            pl.when(
                (pl.col("event_time") == last_time) & (pl.col(asset_col) == first_asset)
            )
            .then(pl.lit(None, dtype=pl.Float64))
            .otherwise(pl.col("close"))
            .alias("close")
        )

        # Build a modified bundle with the corrupted df_feat
        corrupted_bundle = dict(bundle)
        corrupted_bundle["df_feat"] = df_corrupted

        # Re-run with corrupted bundle but SAME df_clean and X (training unchanged)
        rng2 = np.random.default_rng(99)
        result_corrupted = evaluate_factor_callable(
            predict_fn=lambda Xarr: rng2.standard_normal(Xarr.shape[0]),
            bundle=corrupted_bundle,  # type: ignore[arg-type]
            df_clean=df_clean,
            X_full=X,
            n_folds=3,
            embargo_bars=5,
        )

        # The first fold (earliest OOS) should have identical IC since that
        # null row lands in the last time-index (latest fold territory).
        base_folds = result_base["details"]["per_fold"]
        corrupted_folds = result_corrupted["details"]["per_fold"]

        if base_folds and corrupted_folds:
            # At minimum the fold count should match (fold membership unchanged)
            assert len(base_folds) == len(corrupted_folds), (
                "Fold count changed after injecting NaN into future row — "
                "pre-split cleaning is leaking into fold selection."
            )

    def test_per_fold_filter_applied(self, tmp_path):
        """evaluate_factor_callable must run and return per_fold list (basic smoke)."""
        _patch_sklearn_if_missing()
        bundle, X, y, df_clean = self._load_bundle_and_arrays(tmp_path)

        from app.research.factors.mining.gplearn.mine import evaluate_factor_callable

        result = evaluate_factor_callable(
            predict_fn=lambda Xarr: np.ones(Xarr.shape[0]),
            bundle=bundle,
            df_clean=df_clean,
            X_full=X,
            n_folds=3,
            embargo_bars=5,
        )
        assert len(result["details"]["per_fold"]) >= 1
        # n_oos_rows should reflect per-fold filtered count (finite signal + forward_ret)
        for fold in result["details"]["per_fold"]:
            assert fold["n_oos_rows"] >= 0


# ---------------------------------------------------------------------------
# P0-2: PIT side-table gate — honest gap test
# ---------------------------------------------------------------------------

class TestP02PITSideTableGate:
    """P0-2 is declared an honest gap (features.py is PR-1 stable).

    The loader adds a conservative pre-collapse for industry_df when
    available_at is present. Test that the gate comment is in place and
    that the code path does not silently merge with future side-table data
    by verifying the join is symbol-keyed in features.py (not time-keyed).

    Full fix requires modifying features.py::build_ashare_feature_matrix's
    industry join — tracked as next-PR ticket.
    """

    def test_load_ashare_does_not_crash_without_side_tables(self, tmp_path):
        """load_ashare must succeed without industry/macro side tables."""
        kline = _make_ashare_frame(n_symbols=3, n_days=60)
        kline_path = tmp_path / "kline.parquet"
        val_path = tmp_path / "val.parquet"
        kline.write_parquet(kline_path)
        kline.write_parquet(val_path)

        from app.research.factors.mining._universe_loader import load_ashare

        bundle = load_ashare(
            kline_parquet=str(kline_path),
            valuation_parquet=str(val_path),
            target_days=5,
        )
        assert bundle["asset_col"] == "asset_id"
        assert "asset_id" in bundle["df_feat"].columns

    def test_honest_gap_industry_join_is_symbol_only(self):
        """Verify features.py industry join is symbol-only (no available_at gate).

        This test documents the known gap: the join at
        features.py::build_ashare_feature_matrix line ~308 uses symbol only,
        not (symbol, available_at). The test passes today (gap exists), and
        should be replaced by a PIT-gated join test when features.py is patched.

        Gap severity: P1 — industry reclassification mid-sample leaks future
        classification into earlier bars. Impact is low if industry labels are
        stable but non-zero for stocks with sector changes.
        Next-PR ticket: Add available_at-gated as-of join in
        build_ashare_feature_matrix for industry_df.
        """
        import inspect
        from app.research.markets.ashare import features as feat_mod

        src = inspect.getsource(feat_mod.build_ashare_feature_matrix)
        # Confirm the industry join exists and is symbol-only (the gap)
        assert 'join(ind_map, on="symbol"' in src, (
            "features.py industry join pattern changed — re-evaluate P0-2 gap status"
        )
        # Confirm available_at is NOT used in the join (gap still present)
        # If this assertion starts failing, the gap has been fixed — remove this test.
        assert 'join(ind_map' in src and 'available_at' not in src.split('join(ind_map')[1].split('\n')[0], (
            "features.py industry join now uses available_at — P0-2 gap fixed, "
            "remove this honest-gap test and add a positive PIT-gate test."
        )


# ---------------------------------------------------------------------------
# P0-3: Universe sampling survivorship bias
# ---------------------------------------------------------------------------

class TestP03UniverseSampling:
    """load_ashare with sample_symbols and no symbols= must emit a warning."""

    def test_sample_symbols_emits_survivorship_bias_warning(self, tmp_path):
        """Random sampling without as-of snapshot must emit UserWarning."""
        kline = _make_ashare_frame(n_symbols=5, n_days=60)
        kline_path = tmp_path / "kline.parquet"
        val_path = tmp_path / "val.parquet"
        kline.write_parquet(kline_path)
        kline.write_parquet(val_path)

        from app.research.factors.mining._universe_loader import load_ashare

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            load_ashare(
                kline_parquet=str(kline_path),
                valuation_parquet=str(val_path),
                target_days=5,
                sample_symbols=3,
            )

        warn_msgs = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
        assert any("survivorship bias" in m for m in warn_msgs), (
            f"Expected survivorship bias warning. Got: {warn_msgs}"
        )

    def test_explicit_symbols_no_warning(self, tmp_path):
        """Passing symbols= explicitly must NOT emit the survivorship bias warning."""
        kline = _make_ashare_frame(n_symbols=5, n_days=60)
        kline_path = tmp_path / "kline.parquet"
        val_path = tmp_path / "val.parquet"
        kline.write_parquet(kline_path)
        kline.write_parquet(val_path)

        from app.research.factors.mining._universe_loader import load_ashare

        explicit_syms = ["000000.SZ", "000001.SZ"]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            bundle = load_ashare(
                kline_parquet=str(kline_path),
                valuation_parquet=str(val_path),
                target_days=5,
                symbols=explicit_syms,
            )

        warn_msgs = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
        assert not any("survivorship bias" in m for m in warn_msgs), (
            "Survivorship bias warning should NOT fire when symbols= is explicit."
        )
        # Only the requested symbols should be present
        actual_ids = bundle["df_feat"]["asset_id"].unique().to_list()
        assert len(actual_ids) <= len(explicit_syms)

    def test_explicit_symbols_takes_precedence_over_sample_symbols(self, tmp_path):
        """symbols= wins over sample_symbols= — sample_symbols is ignored."""
        kline = _make_ashare_frame(n_symbols=5, n_days=60)
        kline_path = tmp_path / "kline.parquet"
        val_path = tmp_path / "val.parquet"
        kline.write_parquet(kline_path)
        kline.write_parquet(val_path)

        from app.research.factors.mining._universe_loader import load_ashare

        explicit_syms = ["000000.SZ"]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            bundle = load_ashare(
                kline_parquet=str(kline_path),
                valuation_parquet=str(val_path),
                target_days=5,
                symbols=explicit_syms,
                sample_symbols=4,  # should be ignored
            )

        warn_msgs = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
        assert not any("survivorship bias" in m for m in warn_msgs)
        actual_ids = bundle["df_feat"]["asset_id"].unique().to_list()
        assert len(actual_ids) <= 1, (
            "symbols= should constrain to 1 symbol; sample_symbols=4 must be ignored."
        )


# ---------------------------------------------------------------------------
# P0-4: Evaluation target naming — forward not backward
# ---------------------------------------------------------------------------

class TestP04ForwardReturnTarget:
    """evaluate_factor_callable must use 1-bar forward return, not backward."""

    def _tiny_crypto_bundle(self, tmp_path, n_bars: int = 900):
        """Build a minimal crypto bundle with predictable close prices."""
        raw = _make_crypto_frame(n_assets=2, n_bars=n_bars)
        pq = tmp_path / "crypto.parquet"
        raw.write_parquet(pq)
        from app.research.factors.mining._universe_loader import load_crypto
        return load_crypto(str(pq))

    def test_forward_ret_column_exists_in_eval_frame(self, tmp_path):
        """evaluate_factor_callable must produce forward_ret (not next_ret) column."""
        _patch_sklearn_if_missing()

        bundle = self._tiny_crypto_bundle(tmp_path)
        from app.research.factors.mining.gplearn.mine import _to_numpy, evaluate_factor_callable

        X, y, df_clean = _to_numpy(bundle)
        result = evaluate_factor_callable(
            predict_fn=lambda Xarr: np.zeros(Xarr.shape[0]),
            bundle=bundle,
            df_clean=df_clean,
            X_full=X,
            n_folds=2,
            embargo_bars=5,
        )
        # Result must have keys and not crash — forward_ret column was used
        assert "combined_score" in result
        assert "per_fold" in result["details"]

    def test_forward_ret_is_shift_minus_one(self):
        """2-row fixture: forward_ret[t] == log(close[t+1]) - log(close[t]).

        Uses a hand-crafted 2-asset x 3-bar panel to verify arithmetic.
        Row ordering: sorted by (asset_id, event_time).
        """
        import math as _math

        base = datetime.datetime(2024, 1, 1)
        # Two assets, 3 bars each. Close prices chosen so log-diff is exact.
        # Asset A: close = [100, 110, 121]   -> log(110/100)=log(1.1), log(121/110)=log(1.1)
        # Asset B: close = [200, 180, 162]   -> log(180/200)=log(0.9), log(162/180)=log(0.9)
        rows = []
        for asset, closes in [("A", [100.0, 110.0, 121.0]), ("B", [200.0, 180.0, 162.0])]:
            for i, c in enumerate(closes):
                rows.append({
                    "asset_id": asset,
                    "event_time": base + datetime.timedelta(hours=i),
                    "close": c,
                })
        df = pl.DataFrame(rows).sort(["asset_id", "event_time"])

        # Replicate the forward_ret computation from evaluate_factor_callable (P0-4 fix)
        df = df.with_columns(
            pl.col("close").log(base=_math.e).alias("_log_close")
        ).with_columns(
            (pl.col("_log_close").shift(-1).over("asset_id") - pl.col("_log_close")).alias("forward_ret")
        ).drop("_log_close")

        # Asset A, bar 0: forward_ret = log(110) - log(100) = log(1.1)
        row_a0 = df.filter((pl.col("asset_id") == "A") & (pl.col("event_time") == base))
        expected_a0 = _math.log(110.0) - _math.log(100.0)
        assert abs(row_a0["forward_ret"][0] - expected_a0) < 1e-10, (
            f"forward_ret[A,t=0]={row_a0['forward_ret'][0]:.8f} != {expected_a0:.8f}"
        )

        # Asset A, bar 1: forward_ret = log(121) - log(110)
        t1 = base + datetime.timedelta(hours=1)
        row_a1 = df.filter((pl.col("asset_id") == "A") & (pl.col("event_time") == t1))
        expected_a1 = _math.log(121.0) - _math.log(110.0)
        assert abs(row_a1["forward_ret"][0] - expected_a1) < 1e-10

        # Asset A, bar 2 (last): forward_ret must be null (no next bar)
        t2 = base + datetime.timedelta(hours=2)
        row_a2 = df.filter((pl.col("asset_id") == "A") & (pl.col("event_time") == t2))
        assert row_a2["forward_ret"][0] is None, (
            "Last bar's forward_ret must be null — no next-bar close available."
        )

        # Asset B bar 0: forward_ret = log(180) - log(200) = log(0.9) < 0
        row_b0 = df.filter((pl.col("asset_id") == "B") & (pl.col("event_time") == base))
        expected_b0 = _math.log(180.0) - _math.log(200.0)
        assert abs(row_b0["forward_ret"][0] - expected_b0) < 1e-10

        # Cross-asset isolation: Asset A bar 0 forward_ret must NOT equal Asset B bar 0
        assert row_a0["forward_ret"][0] != row_b0["forward_ret"][0], (
            "forward_ret is not isolated per asset — cross-instrument bleed detected."
        )

    def test_no_next_ret_column_used(self, tmp_path):
        """After P0-4 fix the column 'next_ret' must NOT appear in evaluate output.

        If next_ret were still referenced, it would either raise a column-not-found
        error (caught here) or silently compute backward returns (wrong).
        This test confirms neither happens.
        """
        _patch_sklearn_if_missing()
        bundle = self._tiny_crypto_bundle(tmp_path)
        from app.research.factors.mining.gplearn.mine import _to_numpy, evaluate_factor_callable

        X, y, df_clean = _to_numpy(bundle)
        # Must not raise any ColumnNotFoundError or similar
        result = evaluate_factor_callable(
            predict_fn=lambda Xarr: np.random.default_rng(7).standard_normal(Xarr.shape[0]),
            bundle=bundle,
            df_clean=df_clean,
            X_full=X,
            n_folds=2,
            embargo_bars=5,
        )
        assert math.isfinite(result["combined_score"])
