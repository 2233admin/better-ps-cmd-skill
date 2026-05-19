"""A-share mother-factor transforms (A1..A5).

Produces ~13 PIT-safe B-layer features from daily A-share raw data:

  A1 Valuation z-score / rank (4):
      pe_ttm_z, pb_z, ps_ttm_z, pcf_z

  A2 Valuation delta (3):
      pe_ttm_1m_delta, pe_ttm_3m_delta, pb_3m_delta

  A3 Industry-demean / rank (2):
      log_ret_industry_demean, log_ret_industry_rank

  A4 Macro forward-fill daily (4):
      cpi_yoy_fwd, ppi_yoy_fwd, pmi_fwd, m2_yoy_fwd

  A5 ST flag (1):
      is_st_flag  (float 0.0 / 1.0)

Bar cadence: DAILY.  All window constants are in trading-day units.
PIT contract:
  - Time-series ops use .shift(N).over("symbol") or rolling.over("symbol")
    -- never cross-instrument bleed.
  - Cross-sectional ops use .over("event_time") -- only uses the
    cross-section at time t, no future timestamps.
  - Macro join uses PIT-safe available_at filtering before join:
    only rows where macro.available_at <= kline.available_at are eligible
    (implemented via as-of / forward-fill after date-alignment).

Origin: ported from archive/crypto-w1-complete:backend/app/research/factors/derive/ashare_features.py
        during the A-axis merge (PR-1).  See docs/rollouts/factor-framework-merge-handoff-2026-05-19.md.

WHEEL_AUDIT decision:
  qlib  (43k+, pushed 2026-04-22, MIT) -- alive and relevant.
    Alpha158/360 factor catalogue is used as *design reference* for factor
    selection but NOT as a dependency.  Reasons:
      (a) qlib data layer expects qlib-format CSVs / bin files, incompatible
          with our polars PIT parquet lake.
      (b) qlib expression engine (ExpressionD) requires their provider stack.
      (c) Our 13 factors are a strict subset of Alpha158; self-implementing
          in polars is ~120 LOC vs a ~50-file dependency with a C++ backend.
    Verdict: "wheels evaluated, none fit because schema-mismatch:
    qlib needs its own data layer; zipline-reloaded stale (2026-01); pandas-ta
    stale (404 on gh) -- self-implement in polars."

  pandas-ta / ta (bukosabino, 5k+, pushed 2026-03) -- TA indicators.
    Not relevant here: we need valuation z-scores and macro forward-fill,
    not MACD/RSI.  Skip.

  zipline-reloaded (1.7k+, pushed 2026-01) -- pipeline factor framework.
    Stale, and requires zipline data bundle format.  Skip.
"""

from __future__ import annotations

import polars as pl

# ---------------------------------------------------------------------------
# Daily bar constants
# ---------------------------------------------------------------------------

_DAYS_1M: int = 20    # ~1 calendar month of trading days
_DAYS_3M: int = 60    # ~3 calendar months


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cs_zscore_expr(col: str) -> pl.Expr:
    """Cross-sectional z-score over ("event_time") -- PIT-safe.

    Uses population std (ddof=0).  Returns 0.0 when std == 0 within a
    cross-section.  Only uses the current timestamp's cross-section.
    """
    mean_e = pl.col(col).mean().over("event_time")
    std_e = pl.col(col).std(ddof=0).over("event_time")
    return (
        pl.when(std_e == 0.0)
        .then(pl.lit(0.0))
        .otherwise((pl.col(col) - mean_e) / std_e)
    )


def _cs_rank_expr(col: str) -> pl.Expr:
    """Cross-sectional rank (0..1) over ("event_time") -- PIT-safe.

    Uses min method; ties share the minimum rank.
    """
    return pl.col(col).rank(method="min").over("event_time") / (
        pl.col(col).count().over("event_time")
    )


# ---------------------------------------------------------------------------
# A1: Valuation z-score / rank
# ---------------------------------------------------------------------------

def add_a1_valuation_zscore(df: pl.DataFrame) -> pl.DataFrame:
    """A1: cross-sectional z-score of pe_ttm, pb, ps_ttm, pcf_ttm.

    Requires columns: pe_ttm, pb, ps_ttm, pcf_ttm.
    Input df must be sorted by ["symbol", "event_time"].

    Adds: pe_ttm_z, pb_z, ps_ttm_z, pcf_z.
    NaN preserved -- null valuation rows stay null (no fillna).
    """
    df = df.with_columns(
        _cs_zscore_expr("pe_ttm").alias("pe_ttm_z"),
        _cs_zscore_expr("pb").alias("pb_z"),
        _cs_zscore_expr("ps_ttm").alias("ps_ttm_z"),
        _cs_zscore_expr("pcf_ttm").alias("pcf_z"),
    )
    return df


# ---------------------------------------------------------------------------
# A2: Valuation delta (change over N days)
# ---------------------------------------------------------------------------

def add_a2_valuation_delta(df: pl.DataFrame) -> pl.DataFrame:
    """A2: 1M and 3M change in pe_ttm; 3M change in pb.

    Uses shift(N).over("symbol") -- no cross-instrument bleed.
    Adds: pe_ttm_1m_delta, pe_ttm_3m_delta, pb_3m_delta.
    """
    df = df.with_columns(
        (
            pl.col("pe_ttm") - pl.col("pe_ttm").shift(_DAYS_1M).over("symbol")
        ).alias("pe_ttm_1m_delta"),
        (
            pl.col("pe_ttm") - pl.col("pe_ttm").shift(_DAYS_3M).over("symbol")
        ).alias("pe_ttm_3m_delta"),
        (
            pl.col("pb") - pl.col("pb").shift(_DAYS_3M).over("symbol")
        ).alias("pb_3m_delta"),
    )
    return df


# ---------------------------------------------------------------------------
# A3: Industry-demean / industry-rank of log return
# ---------------------------------------------------------------------------

def add_a3_industry_features(df: pl.DataFrame) -> pl.DataFrame:
    """A3: log_ret demeaned and ranked within industry_level1 at each day.

    Requires columns: log_ret, industry_level1.
    log_ret must be pre-computed (see build_ashare_feature_matrix).

    Cross-sectional within industry at each event_time -- PIT-safe:
    only uses the current timestamp's cross-section.

    Adds: log_ret_industry_demean, log_ret_industry_rank.
    """
    df = df.with_columns(
        (
            pl.col("log_ret")
            - pl.col("log_ret").mean().over(["event_time", "industry_level1"])
        ).alias("log_ret_industry_demean"),
        (
            pl.col("log_ret").rank(method="min").over(["event_time", "industry_level1"])
            / pl.col("log_ret").count().over(["event_time", "industry_level1"])
        ).alias("log_ret_industry_rank"),
    )
    return df


# ---------------------------------------------------------------------------
# A4: Macro forward-fill to daily
# ---------------------------------------------------------------------------

def add_a4_macro_features(
    df: pl.DataFrame,
    macro_df: pl.DataFrame,
) -> pl.DataFrame:
    """A4: Forward-fill monthly macro to daily, PIT-safe via available_at.

    macro_df schema: event_time (monthly), available_at, cpi_yoy, ppi_yoy,
                     pmi, m2_supply (from macro_cn_monthly_pit.parquet).

    Algorithm:
      1. For each trading day t in df, find the most recent macro row where
         macro.available_at <= t (PIT-safe: only use already-released macro).
         This is an as-of join.
      2. Forward-fill the last known value at each t.

    Adds: cpi_yoy_fwd, ppi_yoy_fwd, pmi_fwd, m2_yoy_fwd.
    NaN preserved -- null macro periods stay null.
    """
    macro_for_join = macro_df.select(
        pl.col("available_at").dt.date().alias("_macro_avail_date"),
        "cpi_yoy",
        "ppi_yoy",
        "pmi",
        "m2_supply",
    ).sort("_macro_avail_date")

    dates_df = (
        df.select(pl.col("event_time").dt.date().alias("_trade_date"))
        .unique()
        .sort("_trade_date")
    )

    dates_joined = dates_df.join_asof(
        macro_for_join,
        left_on="_trade_date",
        right_on="_macro_avail_date",
        strategy="backward",
    ).select(
        "_trade_date",
        pl.col("cpi_yoy").alias("cpi_yoy_fwd"),
        pl.col("ppi_yoy").alias("ppi_yoy_fwd"),
        pl.col("pmi").alias("pmi_fwd"),
        pl.col("m2_supply").alias("m2_yoy_fwd"),
    )

    df = df.with_columns(
        pl.col("event_time").dt.date().alias("_trade_date")
    )
    df = df.join(dates_joined, on="_trade_date", how="left").drop("_trade_date")
    return df


# ---------------------------------------------------------------------------
# A5: ST flag
# ---------------------------------------------------------------------------

def add_a5_st_flag(df: pl.DataFrame) -> pl.DataFrame:
    """A5: cast is_st boolean to float 0.0/1.0 for gplearn.

    Adds: is_st_flag.
    """
    df = df.with_columns(
        pl.col("is_st").cast(pl.Float64).alias("is_st_flag"),
    )
    return df


# ---------------------------------------------------------------------------
# Log return (base transform, required by A3)
# ---------------------------------------------------------------------------

def add_log_ret(df: pl.DataFrame) -> pl.DataFrame:
    """Compute 1-bar log return per symbol.

    Adds: log_ret.  Uses shift(1).over("symbol") -- no cross-instrument bleed.
    df must be sorted by ["symbol", "event_time"].
    """
    df = df.with_columns(
        (pl.col("close").log(base=2.718281828).diff().over("symbol")).alias("log_ret"),
    )
    return df


# ---------------------------------------------------------------------------
# Convenience: build full A-share feature matrix
# ---------------------------------------------------------------------------

def build_ashare_feature_matrix(
    kline_df: pl.DataFrame,
    macro_df: pl.DataFrame | None = None,
    industry_df: pl.DataFrame | None = None,
    target_days: int = 5,
) -> pl.DataFrame:
    """Build PIT-safe A-share feature matrix from daily kline + side tables.

    Parameters
    ----------
    kline_df:
        Daily OHLCV + valuation + is_st, schema:
          symbol, event_time, available_at, open, high, low, close,
          volume, amount, pe_ttm, pb, ps_ttm, pcf_ttm, is_st
    macro_df:
        macro_cn_monthly_pit.parquet (optional; A4 skipped if None).
    industry_df:
        industry_classification_pit.parquet (optional; A3 skipped if None).
    target_days:
        Forward return horizon in trading days (default 5 = ~1 week).

    Returns df with all mother factors + fwd_ret_Nd target.
    """
    df = kline_df.sort(["symbol", "event_time"])

    df = add_log_ret(df)

    df = df.with_columns(
        pl.col("log_ret").shift(1).over("symbol").alias("log_ret_lag1"),
        pl.col("log_ret").shift(5).over("symbol").alias("log_ret_lag5"),
        pl.col("log_ret").shift(20).over("symbol").alias("log_ret_lag20"),
    )

    df = df.with_columns(
        pl.col("log_ret").rolling_std(20).over("symbol").alias("vol_proxy_20d"),
    )

    df = df.with_columns(
        (
            pl.col("volume")
            / pl.col("volume").rolling_mean(20).over("symbol")
        ).alias("volume_ratio_20d"),
    )

    df = add_a1_valuation_zscore(df)
    df = add_a2_valuation_delta(df)

    if industry_df is not None:
        ind_map = industry_df.select(["symbol", "industry_level1"])
        df = df.join(ind_map, on="symbol", how="left")
        df = add_a3_industry_features(df)
    else:
        df = df.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("log_ret_industry_demean"),
            pl.lit(None, dtype=pl.Float64).alias("log_ret_industry_rank"),
        )

    if macro_df is not None:
        df = add_a4_macro_features(df, macro_df)
    else:
        df = df.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("cpi_yoy_fwd"),
            pl.lit(None, dtype=pl.Float64).alias("ppi_yoy_fwd"),
            pl.lit(None, dtype=pl.Float64).alias("pmi_fwd"),
            pl.lit(None, dtype=pl.Float64).alias("m2_yoy_fwd"),
        )

    df = add_a5_st_flag(df)

    df = df.with_columns(
        pl.col("log_ret").shift(-target_days).over("symbol").alias(f"fwd_ret_{target_days}d"),
    )

    return df


# ---------------------------------------------------------------------------
# Feature column lists (for mining-side universe dispatch in PR-3)
# ---------------------------------------------------------------------------

#: Core time-series features available without side tables.
ASHARE_CORE_FEATURE_COLS: list[str] = [
    "log_ret_lag1",
    "log_ret_lag5",
    "log_ret_lag20",
    "vol_proxy_20d",
    "volume_ratio_20d",
]

#: A1 valuation z-score (requires valuation in kline_df).
ASHARE_VALUATION_FEATURE_COLS: list[str] = [
    "pe_ttm_z",
    "pb_z",
    "ps_ttm_z",
    "pcf_z",
]

#: A2 valuation delta.
ASHARE_VALUATION_DELTA_COLS: list[str] = [
    "pe_ttm_1m_delta",
    "pe_ttm_3m_delta",
    "pb_3m_delta",
]

#: A3 industry features (requires industry_df join).
ASHARE_INDUSTRY_FEATURE_COLS: list[str] = [
    "log_ret_industry_demean",
    "log_ret_industry_rank",
]

#: A4 macro forward-fill (requires macro_df join).
ASHARE_MACRO_FEATURE_COLS: list[str] = [
    "cpi_yoy_fwd",
    "ppi_yoy_fwd",
    "pmi_fwd",
    "m2_yoy_fwd",
]

#: A5 ST flag.
ASHARE_ST_FEATURE_COLS: list[str] = [
    "is_st_flag",
]

#: Full feature set (all groups, used when all side tables are available).
ASHARE_FEATURE_COLS: list[str] = (
    ASHARE_CORE_FEATURE_COLS
    + ASHARE_VALUATION_FEATURE_COLS
    + ASHARE_VALUATION_DELTA_COLS
    + ASHARE_INDUSTRY_FEATURE_COLS
    + ASHARE_MACRO_FEATURE_COLS
    + ASHARE_ST_FEATURE_COLS
)

#: Human-readable names for gplearn feature_names= (same order as ASHARE_FEATURE_COLS).
ASHARE_FEATURE_NAMES: list[str] = [
    "logret_lag1",
    "logret_lag5",
    "logret_lag20",
    "vol_proxy_20d",
    "vol_ratio_20d",
    "pe_z",
    "pb_z",
    "ps_z",
    "pcf_z",
    "pe_1m_delta",
    "pe_3m_delta",
    "pb_3m_delta",
    "ind_demean",
    "ind_rank",
    "cpi_fwd",
    "ppi_fwd",
    "pmi_fwd",
    "m2_fwd",
    "is_st",
]
