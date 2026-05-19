"""Crypto mother-factor transforms (G1, G2, G4, G6).

Produces 12 PIT-safe B-layer features from 1H kline + funding + open-interest
columns of the combined crypto PIT parquet.

Groups:
  G1: funding 二阶 (3 features)
      funding_z, funding_1d_delta, funding_abs
  G2: OI 衍生 (3 features)
      oi_change_1d, oi_change_1d_z, oi_volume_ratio
  G4: vol 多尺度 (3 features)
      realized_vol_5d, vol_ratio_5_30, parkinson_vol_24h
  G6: momentum 多尺度 (3 features)
      log_ret_lag48, log_ret_lag168, reversal_5d_sign

Bar cadence: 1 HOUR.  All window constants are in hourly-bar units
(_BARS_PER_DAY = 24).

PIT contract (mirrors A-share contract):
  - Time-series ops use .shift(N).over("inst_id") or rolling.over("inst_id")
    -- never cross-instrument bleed.
  - Cross-sectional ops use .over("event_time") -- only uses the
    cross-section at time t.
  - Sparse columns (funding_rate, open_interest) are forward-filled per
    instrument before consumption: PIT-safe because the last publication is
    genuinely available at time t.

Origin: ported from archive/crypto-w1-complete:backend/app/research/factors/derive/derived_features.py
        during the A-axis merge (PR-1).  See docs/rollouts/factor-framework-merge-handoff-2026-05-19.md.
"""

from __future__ import annotations

import math

import polars as pl

# ---------------------------------------------------------------------------
# Bar-count constants (data is 1H bars)
# ---------------------------------------------------------------------------

_BARS_PER_DAY = 24
_BARS_5D = 5 * _BARS_PER_DAY      # 120
_BARS_30D = 30 * _BARS_PER_DAY    # 720
_BARS_48H = 48
_BARS_168H = 168                  # 1 week at 1H


# ---------------------------------------------------------------------------
# Internal helpers (mirrors markets/ashare/features.py conventions)
# ---------------------------------------------------------------------------

def _pit_forward_fill(df: pl.DataFrame, col: str) -> pl.Expr:
    """PIT-safe forward-fill of a sparse column within each instrument."""
    return pl.col(col).forward_fill().over("inst_id")


def _cs_zscore_expr(col: str) -> pl.Expr:
    """Cross-sectional z-score at each event_time over all inst_ids.

    Uses population std (ddof=0). Returns 0.0 when std == 0 within a
    cross-section. PIT-safe.
    """
    mean_e = pl.col(col).mean().over("event_time")
    std_e = pl.col(col).std(ddof=0).over("event_time")
    return (
        pl.when(std_e == 0.0)
        .then(pl.lit(0.0))
        .otherwise((pl.col(col) - mean_e) / std_e)
    )


# ---------------------------------------------------------------------------
# G1: funding 二阶 (3 features)
# ---------------------------------------------------------------------------

def add_g1_funding_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add G1 funding second-order features.

    Requires: funding_rate column (sparse OK -- will be forward-filled).
    Adds: funding_z, funding_1d_delta, funding_abs.

    Input df must be sorted by ["inst_id", "event_time"].
    """
    df = df.with_columns(
        _pit_forward_fill(df, "funding_rate").alias("_funding_ff"),
    )

    df = df.with_columns(
        _cs_zscore_expr("_funding_ff").alias("funding_z"),
        (pl.col("_funding_ff") - pl.col("_funding_ff").shift(1).over("inst_id"))
        .alias("funding_1d_delta"),
        pl.col("_funding_ff").abs().alias("funding_abs"),
    )

    return df.drop("_funding_ff")


# ---------------------------------------------------------------------------
# G2: OI 衍生 (3 features)
# ---------------------------------------------------------------------------

def add_g2_oi_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add G2 open-interest derived features.

    Requires: open_interest, volume columns.
    Adds: oi_change_1d, oi_change_1d_z, oi_volume_ratio.

    Input df must be sorted by ["inst_id", "event_time"].
    """
    df = df.with_columns(
        _pit_forward_fill(df, "open_interest").alias("_oi_ff"),
    )

    df = df.with_columns(
        (
            (pl.col("_oi_ff") / pl.col("_oi_ff").shift(1).over("inst_id")).log(base=math.e)
        ).alias("oi_change_1d"),
        pl.col("volume").rolling_sum(_BARS_PER_DAY).over("inst_id").alias("_vol_24h"),
    )

    df = df.with_columns(
        _cs_zscore_expr("oi_change_1d").alias("oi_change_1d_z"),
        (pl.col("_oi_ff") / pl.col("_vol_24h")).alias("oi_volume_ratio"),
    )

    return df.drop(["_oi_ff", "_vol_24h"])


# ---------------------------------------------------------------------------
# G4: vol 多尺度 (3 features)
# ---------------------------------------------------------------------------

def add_g4_vol_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add G4 multi-scale volatility features.

    Requires: log_ret column (pre-computed: log(close).diff().over(inst_id)).
    Requires: high, low columns (for Parkinson estimator).
    Adds: realized_vol_5d, vol_ratio_5_30, parkinson_vol_24h.

    Input df must be sorted by ["inst_id", "event_time"].
    """
    df = df.with_columns(
        pl.col("log_ret").rolling_std(_BARS_5D).over("inst_id").alias("realized_vol_5d"),
        pl.col("log_ret").rolling_std(_BARS_30D).over("inst_id").alias("_vol_30d"),
    )

    df = df.with_columns(
        (pl.col("realized_vol_5d") / pl.col("_vol_30d")).alias("vol_ratio_5_30"),
        (
            (pl.col("high") / pl.col("low")).log(base=math.e).pow(2)
            .rolling_mean(_BARS_PER_DAY)
            .over("inst_id")
            / (4.0 * math.log(2.0))
        ).sqrt().alias("parkinson_vol_24h"),
    )

    return df.drop("_vol_30d")


# ---------------------------------------------------------------------------
# G6: momentum 多尺度 (3 features)
# ---------------------------------------------------------------------------

def add_g6_momentum_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add G6 multi-scale momentum features.

    Requires: close column.
    Adds: log_ret_lag48, log_ret_lag168, reversal_5d_sign.

    Input df must be sorted by ["inst_id", "event_time"].
    """
    log_close = pl.col("close").log(base=math.e)

    df = df.with_columns(
        (log_close - log_close.shift(_BARS_48H).over("inst_id")).alias("log_ret_lag48"),
        (log_close - log_close.shift(_BARS_168H).over("inst_id")).alias("log_ret_lag168"),
        (-(log_close - log_close.shift(_BARS_5D).over("inst_id")).sign()).alias("reversal_5d_sign"),
    )

    return df


# ---------------------------------------------------------------------------
# Convenience: add all 12 features in one call
# ---------------------------------------------------------------------------

def add_all_derived_features(df: pl.DataFrame) -> pl.DataFrame:
    """Add all 12 G1+G2+G4+G6 derived features in sequence.

    Expects df sorted by ["inst_id", "event_time"] with columns:
        close, high, low, volume, funding_rate, open_interest, log_ret

    ``log_ret`` must be pre-computed (e.g. by mine.py::build_feature_matrix).
    Returns df with 12 new columns appended.
    """
    df = add_g1_funding_features(df)
    df = add_g2_oi_features(df)
    df = add_g4_vol_features(df)
    df = add_g6_momentum_features(df)
    return df


# ---------------------------------------------------------------------------
# Feature column lists (consumed by mining-side universe dispatch in PR-3)
# ---------------------------------------------------------------------------

#: 12 new feature columns introduced by this module (subset of full crypto
#: mining feature set; the base 6 features live alongside mine.py's
#: build_feature_matrix until PR-3 unifies the dispatch).
CRYPTO_DERIVED_FEATURE_COLS: list[str] = [
    "funding_z",
    "funding_1d_delta",
    "funding_abs",
    "oi_change_1d",
    "oi_change_1d_z",
    "oi_volume_ratio",
    "realized_vol_5d",
    "vol_ratio_5_30",
    "parkinson_vol_24h",
    "log_ret_lag48",
    "log_ret_lag168",
    "reversal_5d_sign",
]

#: Human-readable names for gplearn feature_names= (same order).
CRYPTO_DERIVED_FEATURE_NAMES: list[str] = list(CRYPTO_DERIVED_FEATURE_COLS)
