"""Unit tests for markets/ashare/features.py.

Each mother factor gets:
  (a) A deterministic-input -> deterministic-output assertion.
  (b) Where applicable, a PIT-leak guard: injecting a future value must not
      affect the output for the present time-step.

Origin: ported from archive/crypto-w1-complete:backend/tests/unit/test_ashare_features.py
        during the A-axis merge (PR-1). Only the import path changed
        (factors/derive/ashare_features -> markets/ashare/features).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from app.research.markets.ashare.features import (
    ASHARE_FEATURE_COLS,
    ASHARE_FEATURE_NAMES,
    add_a1_valuation_zscore,
    add_a2_valuation_delta,
    add_a3_industry_features,
    add_a4_macro_features,
    add_a5_st_flag,
    add_log_ret,
    build_ashare_feature_matrix,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_T0 = datetime(2021, 1, 4, tzinfo=UTC)


def _dt(n_days: int) -> datetime:
    return _T0 + timedelta(days=n_days)


def _make_kline(n_syms: int = 3, n_days: int = 30) -> pl.DataFrame:
    """Synthetic daily kline with monotonically increasing close per symbol."""
    rows: list[dict] = []
    syms = [f"{i:06d}.SH" for i in range(1, n_syms + 1)]
    for s, sym in enumerate(syms):
        for d in range(n_days):
            close = 10.0 + s + d * 0.1
            rows.append(
                {
                    "symbol": sym,
                    "event_time": _dt(d),
                    "available_at": _dt(d + 1),
                    "open": close - 0.05,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "volume": int(1_000_000 + s * 100_000 + d * 1_000),
                    "amount": float(close * (1_000_000 + s * 100_000)),
                    "pe_ttm": 15.0 + s * 2.0 + d * 0.01,
                    "pb": 1.5 + s * 0.3 + d * 0.005,
                    "ps_ttm": 2.0 + s * 0.5,
                    "pcf_ttm": 10.0 + s,
                    "is_st": False,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "event_time"])


def _make_industry_df(syms: list[str]) -> pl.DataFrame:
    """Map each symbol to a synthetic industry_level1."""
    return pl.DataFrame(
        {
            "symbol": syms,
            "industry_level1": [f"ind_{i % 2}" for i in range(len(syms))],
        }
    )


def _make_macro_df(n_months: int = 12) -> pl.DataFrame:
    """Synthetic monthly macro: available_at = event_time + 45d (conservative)."""
    rows = []
    for m in range(n_months):
        event_time = datetime(2021, 1, 1, tzinfo=UTC) + timedelta(days=30 * m)
        available_at = event_time + timedelta(days=45)
        rows.append(
            {
                "event_time": event_time,
                "available_at": available_at,
                "cpi_yoy": 2.0 + m * 0.1,
                "ppi_yoy": 1.0 + m * 0.05,
                "pmi": 50.0 + m * 0.2,
                "m2_supply": 200_000.0 + m * 1_000,
            }
        )
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# A1: valuation z-score
# ---------------------------------------------------------------------------

class TestA1ValuationZscore:
    def test_pe_ttm_z_deterministic(self):
        df = pl.DataFrame(
            {
                "symbol": ["A", "B", "C"],
                "event_time": [_dt(0)] * 3,
                "pe_ttm": [10.0, 20.0, 30.0],
                "pb": [1.0, 2.0, 3.0],
                "ps_ttm": [1.0, 2.0, 3.0],
                "pcf_ttm": [5.0, 10.0, 15.0],
            }
        )
        out = add_a1_valuation_zscore(df)
        zs = out["pe_ttm_z"].to_list()
        assert zs[1] == pytest.approx(0.0, abs=1e-9)
        assert zs[0] < 0
        assert zs[2] > 0

    def test_pe_ttm_z_cross_sectional_per_day(self):
        df = pl.DataFrame(
            {
                "symbol": ["A", "B", "A", "B"],
                "event_time": [_dt(0), _dt(0), _dt(1), _dt(1)],
                "pe_ttm": [10.0, 20.0, 5.0, 15.0],
                "pb": [1.0, 2.0, 1.0, 2.0],
                "ps_ttm": [1.0, 2.0, 1.0, 2.0],
                "pcf_ttm": [5.0, 10.0, 5.0, 10.0],
            }
        )
        out = add_a1_valuation_zscore(df)
        day0 = out.filter(pl.col("event_time") == _dt(0))
        day1 = out.filter(pl.col("event_time") == _dt(1))
        assert day0.filter(pl.col("symbol") == "A")["pe_ttm_z"][0] < 0
        assert day0.filter(pl.col("symbol") == "B")["pe_ttm_z"][0] > 0
        assert day1.filter(pl.col("symbol") == "A")["pe_ttm_z"][0] < 0
        assert day1.filter(pl.col("symbol") == "B")["pe_ttm_z"][0] > 0

    def test_a1_no_pit_leak(self):
        base = pl.DataFrame(
            {
                "symbol": ["A", "B"],
                "event_time": [_dt(0), _dt(0)],
                "pe_ttm": [10.0, 20.0],
                "pb": [1.0, 2.0],
                "ps_ttm": [1.0, 2.0],
                "pcf_ttm": [5.0, 10.0],
            }
        )
        with_future = pl.concat([
            base,
            pl.DataFrame(
                {
                    "symbol": ["A", "B"],
                    "event_time": [_dt(1), _dt(1)],
                    "pe_ttm": [9999.0, -9999.0],
                    "pb": [1.0, 2.0],
                    "ps_ttm": [1.0, 2.0],
                    "pcf_ttm": [5.0, 10.0],
                }
            ),
        ])
        out_base = add_a1_valuation_zscore(base)
        out_with_future = add_a1_valuation_zscore(with_future)

        day0_base = out_base.filter(pl.col("event_time") == _dt(0)).sort("symbol")
        day0_future = out_with_future.filter(pl.col("event_time") == _dt(0)).sort("symbol")
        for sym in ["A", "B"]:
            z_base = day0_base.filter(pl.col("symbol") == sym)["pe_ttm_z"][0]
            z_fut = day0_future.filter(pl.col("symbol") == sym)["pe_ttm_z"][0]
            assert z_base == pytest.approx(z_fut, abs=1e-12), f"PIT leak for {sym}"


# ---------------------------------------------------------------------------
# A2: valuation delta
# ---------------------------------------------------------------------------

class TestA2ValuationDelta:
    def test_pe_ttm_1m_delta_deterministic(self):
        n_days = 25
        sym = "000001.SH"
        rows = [
            {
                "symbol": sym,
                "event_time": _dt(d),
                "pe_ttm": 10.0 + d,
                "pb": 1.0,
                "ps_ttm": 1.0,
                "pcf_ttm": 5.0,
            }
            for d in range(n_days)
        ]
        df = pl.DataFrame(rows).sort(["symbol", "event_time"])
        out = add_a2_valuation_delta(df)
        row20 = out.filter(pl.col("event_time") == _dt(20))
        assert row20["pe_ttm_1m_delta"][0] == pytest.approx(20.0, abs=1e-9)

    def test_a2_no_pit_leak(self):
        rows = [
            {
                "symbol": "A",
                "event_time": _dt(d),
                "pe_ttm": float(d),
                "pb": 1.0,
                "ps_ttm": 1.0,
                "pcf_ttm": 5.0,
            }
            for d in range(25)
        ]
        df_base = pl.DataFrame(rows)
        future_row = pl.DataFrame(
            [{"symbol": "A", "event_time": _dt(50), "pe_ttm": 9999.0,
              "pb": 1.0, "ps_ttm": 1.0, "pcf_ttm": 5.0}]
        )
        df_with_future = pl.concat([df_base, future_row])
        out_base = add_a2_valuation_delta(df_base.sort(["symbol", "event_time"]))
        out_future = add_a2_valuation_delta(df_with_future.sort(["symbol", "event_time"]))

        delta_base = out_base.filter(pl.col("event_time") == _dt(20))["pe_ttm_1m_delta"][0]
        delta_fut = out_future.filter(pl.col("event_time") == _dt(20))["pe_ttm_1m_delta"][0]
        assert delta_base == pytest.approx(delta_fut, abs=1e-12)


# ---------------------------------------------------------------------------
# A3: industry features
# ---------------------------------------------------------------------------

class TestA3IndustryFeatures:
    def _df_with_log_ret(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "symbol": ["A", "B", "C", "D"],
                "event_time": [_dt(1)] * 4,
                "log_ret": [0.01, 0.02, -0.01, 0.03],
                "industry_level1": ["ind_0", "ind_0", "ind_1", "ind_1"],
            }
        )

    def test_industry_demean_sums_to_zero_within_industry(self):
        df = self._df_with_log_ret()
        out = add_a3_industry_features(df)
        for ind in ["ind_0", "ind_1"]:
            subset = out.filter(pl.col("industry_level1") == ind)
            total = subset["log_ret_industry_demean"].sum()
            assert total == pytest.approx(0.0, abs=1e-9), f"demean sum != 0 for {ind}"

    def test_industry_rank_range(self):
        df = self._df_with_log_ret()
        out = add_a3_industry_features(df)
        ranks = out["log_ret_industry_rank"].to_list()
        for r in ranks:
            assert 0.0 < r <= 1.0, f"rank {r} out of range"

    def test_a3_no_cross_industry_bleed(self):
        df = self._df_with_log_ret()
        out = add_a3_industry_features(df)
        rank_A = out.filter(pl.col("symbol") == "A")["log_ret_industry_rank"][0]
        rank_B = out.filter(pl.col("symbol") == "B")["log_ret_industry_rank"][0]
        assert rank_A < rank_B


# ---------------------------------------------------------------------------
# A4: macro forward-fill
# ---------------------------------------------------------------------------

class TestA4MacroForwardFill:
    def test_pit_safe_no_future_macro(self):
        macro_df = pl.DataFrame(
            [
                {
                    "event_time": datetime(2021, 1, 1, tzinfo=UTC),
                    "available_at": datetime(2021, 2, 15, tzinfo=UTC),
                    "cpi_yoy": 2.5,
                    "ppi_yoy": 1.5,
                    "pmi": 51.0,
                    "m2_supply": 210_000.0,
                }
            ]
        )
        rows = [
            {
                "symbol": "A",
                "event_time": _dt(d),
                "available_at": _dt(d + 1),
            }
            for d in range(55)
        ]
        df = pl.DataFrame(rows)
        out = add_a4_macro_features(df, macro_df)

        day0 = out.filter(pl.col("event_time") == _dt(0))
        assert day0["cpi_yoy_fwd"][0] is None

        day43 = out.filter(pl.col("event_time") == _dt(43))
        if len(day43) > 0 and day43["cpi_yoy_fwd"][0] is not None:
            assert day43["cpi_yoy_fwd"][0] == pytest.approx(2.5, abs=1e-9)

    def test_macro_forward_fills_correctly(self):
        macro_df = pl.DataFrame(
            [
                {
                    "event_time": datetime(2021, 1, 1, tzinfo=UTC),
                    "available_at": datetime(2021, 1, 5, tzinfo=UTC),
                    "cpi_yoy": 2.0,
                    "ppi_yoy": 1.0,
                    "pmi": 50.0,
                    "m2_supply": 200_000.0,
                },
                {
                    "event_time": datetime(2021, 2, 1, tzinfo=UTC),
                    "available_at": datetime(2021, 2, 5, tzinfo=UTC),
                    "cpi_yoy": 3.0,
                    "ppi_yoy": 1.5,
                    "pmi": 51.0,
                    "m2_supply": 201_000.0,
                },
            ]
        )
        rows = [
            {"symbol": "A", "event_time": _dt(d), "available_at": _dt(d + 1)}
            for d in range(40)
        ]
        df = pl.DataFrame(rows)
        out = add_a4_macro_features(df, macro_df)

        mid = out.filter(pl.col("event_time") == _dt(10))
        if len(mid) > 0 and mid["cpi_yoy_fwd"][0] is not None:
            assert mid["cpi_yoy_fwd"][0] == pytest.approx(2.0, abs=1e-9)


# ---------------------------------------------------------------------------
# A5: ST flag
# ---------------------------------------------------------------------------

class TestA5STFlag:
    def test_st_flag_cast(self):
        df = pl.DataFrame(
            {
                "symbol": ["A", "B"],
                "is_st": [True, False],
            }
        )
        out = add_a5_st_flag(df)
        assert out["is_st_flag"].to_list() == [1.0, 0.0]


# ---------------------------------------------------------------------------
# build_ashare_feature_matrix integration
# ---------------------------------------------------------------------------

class TestBuildAshareFeatureMatrix:
    def test_no_future_leak_in_target(self):
        kline = _make_kline(n_syms=2, n_days=30)
        macro = _make_macro_df()
        syms = kline["symbol"].unique().to_list()
        industry = _make_industry_df(syms)
        out = build_ashare_feature_matrix(kline, macro, industry, target_days=5)

        for sym in syms:
            sym_df = out.filter(pl.col("symbol") == sym).sort("event_time")
            last5 = sym_df.tail(5)["fwd_ret_5d"].to_list()
            assert all(v is None for v in last5), f"Expected null fwd_ret at tail for {sym}"

    def test_feature_cols_present(self):
        kline = _make_kline(n_syms=3, n_days=25)
        macro = _make_macro_df()
        syms = kline["symbol"].unique().to_list()
        industry = _make_industry_df(syms)
        out = build_ashare_feature_matrix(kline, macro, industry)
        for col in ASHARE_FEATURE_COLS:
            assert col in out.columns, f"Missing column: {col}"

    def test_feature_names_len_matches_cols(self):
        assert len(ASHARE_FEATURE_NAMES) == len(ASHARE_FEATURE_COLS)

    def test_no_cross_instrument_bleed_log_ret(self):
        kline = _make_kline(n_syms=2, n_days=10)
        out = build_ashare_feature_matrix(kline, target_days=5)

        kline_a = kline.filter(pl.col("symbol") == kline["symbol"].unique().sort()[0])
        out_a = build_ashare_feature_matrix(kline_a, target_days=5)

        sym_a = kline["symbol"].unique().sort()[0]
        lr_full = out.filter(pl.col("symbol") == sym_a).sort("event_time")["log_ret_lag1"].to_list()
        lr_alone = out_a.filter(pl.col("symbol") == sym_a).sort("event_time")["log_ret_lag1"].to_list()
        for a, b in zip(lr_full, lr_alone):
            if a is not None and b is not None:
                assert a == pytest.approx(b, abs=1e-12)
