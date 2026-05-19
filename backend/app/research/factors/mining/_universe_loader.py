"""Universe-agnostic data loading bridge for the gplearn mining runner.

This module owns the transition between raw PIT parquet files and the
canonical factor-core panel consumed by mine.py.  It is the single place
where:

  - raw data is read from disk
  - universe-specific base transforms are applied (log_ret, lags, target col)
  - market-specific derived features are appended (from markets/<universe>/features.py)
  - schema is normalized via the market adapter (to_factor_panel)
  - feature column lists and human-readable names are exported to the caller

After this module's ``load_for_mining()`` returns, mine.py sees only the
canonical schema (asset_id, event_time, available_at, market, close) plus
the derived feature columns.  Neither _get_inst_col() nor any rename(symbol
-> inst_id) hacks are needed downstream.

PERF-NOTE: all feature construction happens in polars; the only numpy
materialization is in mine.py::_to_numpy_generic at fit time.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, TypedDict

import polars as pl

# ---------------------------------------------------------------------------
# Crypto features (PR-1 stable artifacts -- do NOT modify)
# ---------------------------------------------------------------------------

from app.research.markets.crypto.adapter import to_factor_panel as crypto_to_panel
from app.research.markets.crypto.features import (
    CRYPTO_DERIVED_FEATURE_COLS,
    CRYPTO_DERIVED_FEATURE_NAMES,
    add_all_derived_features as crypto_add_derived,
)

# ---------------------------------------------------------------------------
# A-share features (PR-1 stable artifacts -- do NOT modify)
# ---------------------------------------------------------------------------

from app.research.markets.ashare.adapter import to_factor_panel as ashare_to_panel
from app.research.markets.ashare.features import (
    ASHARE_FEATURE_COLS,
    ASHARE_FEATURE_NAMES,
    build_ashare_feature_matrix,
)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class UniverseBundle(TypedDict):
    """Everything mine.py needs after loading a universe."""
    df_feat: pl.DataFrame          # full feature panel, canonical schema
    feature_cols: list[str]        # columns consumed by gplearn X matrix
    feature_names: list[str]       # human-readable names passed to gplearn
    target_col: str                # column used as y
    asset_col: str                 # column holding asset identifier (always asset_id)


# ---------------------------------------------------------------------------
# Crypto base feature constants
# ---------------------------------------------------------------------------

# Base 6 features computed inside this module (not in markets/crypto/features.py)
_CRYPTO_BASE_FEATURE_COLS: list[str] = [
    "log_ret_lag1",
    "log_ret_lag5",
    "log_ret_lag24",
    "vol_proxy",
    "volume_ratio",
    "funding_filled",
]

_CRYPTO_BASE_FEATURE_NAMES: list[str] = [
    "logret_lag1",
    "logret_lag5",
    "logret_lag24",
    "vol_proxy",
    "volume_ratio",
    "funding",
]

# Full crypto feature list = base 6 + 12 G1/G2/G4/G6
CRYPTO_FEATURE_COLS: list[str] = _CRYPTO_BASE_FEATURE_COLS + CRYPTO_DERIVED_FEATURE_COLS
CRYPTO_FEATURE_NAMES: list[str] = _CRYPTO_BASE_FEATURE_NAMES + CRYPTO_DERIVED_FEATURE_NAMES
CRYPTO_TARGET_COL: str = "fwd_ret24"

# Rolling window constants (hourly bars)
_ROLLING_VOL_WINDOW: int = 24
_ROLLING_VOL_MEAN_WINDOW: int = 24


# ---------------------------------------------------------------------------
# Internal: crypto base transform
# ---------------------------------------------------------------------------

def _build_crypto_base(df: pl.DataFrame) -> pl.DataFrame:
    """Compute base 6 crypto features + fwd_ret24 target (PIT-safe).

    Operates on the raw combined parquet schema (inst_id, event_time, close,
    volume, funding_rate, open_interest, high, low, ...).

    Sorts by ["inst_id", "event_time"] in-place.
    All rolling/lag ops use .over("inst_id") -- no cross-instrument bleed.
    """
    df = df.sort(["inst_id", "event_time"])

    # 1-bar log return (prerequisite for G4 features in crypto_add_derived)
    df = df.with_columns(
        (pl.col("close").log(base=math.e).diff().over("inst_id")).alias("log_ret"),
    )

    df = df.with_columns(
        pl.col("log_ret").shift(1).over("inst_id").alias("log_ret_lag1"),
        pl.col("log_ret").shift(5).over("inst_id").alias("log_ret_lag5"),
        pl.col("log_ret").shift(24).over("inst_id").alias("log_ret_lag24"),
        (
            pl.col("log_ret")
            .rolling_std(_ROLLING_VOL_WINDOW)
            .over("inst_id")
        ).alias("vol_proxy"),
        (
            pl.col("volume")
            / pl.col("volume").rolling_mean(_ROLLING_VOL_MEAN_WINDOW).over("inst_id")
        ).alias("volume_ratio"),
        pl.col("funding_rate").fill_null(0.0).alias("funding_filled"),
    )

    # 24-bar forward log-return (training target)
    df = df.with_columns(
        pl.col("log_ret").shift(-24).over("inst_id").alias(CRYPTO_TARGET_COL),
    )

    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_crypto(combined_parquet: str | Path) -> UniverseBundle:
    """Load and feature-engineer crypto PIT data.

    Parameters
    ----------
    combined_parquet:
        Path to kline_pit_combined.parquet (inst_id, event_time, close,
        high, low, volume, funding_rate, open_interest, ...).

    Returns
    -------
    UniverseBundle with canonical schema (asset_id replaces inst_id).
    """
    df_raw = pl.read_parquet(combined_parquet)

    # Base 6 features + fwd_ret24 target (computed here, not in features.py)
    df = _build_crypto_base(df_raw)

    # G1/G2/G4/G6 derived features (12 -- PR-1 stable, do not inline)
    df = crypto_add_derived(df)

    # Schema normalization: inst_id -> asset_id, add market="crypto"
    df = crypto_to_panel(df)

    return UniverseBundle(
        df_feat=df,
        feature_cols=CRYPTO_FEATURE_COLS,
        feature_names=CRYPTO_FEATURE_NAMES,
        target_col=CRYPTO_TARGET_COL,
        asset_col="asset_id",
    )


def load_ashare(
    kline_parquet: str | Path,
    valuation_parquet: str | Path,
    industry_parquet: str | Path | None = None,
    macro_parquet: str | Path | None = None,
    target_days: int = 5,
    sample_symbols: int | None = None,
) -> UniverseBundle:
    """Load and feature-engineer A-share daily PIT data.

    Parameters
    ----------
    kline_parquet:
        Path to kline_daily_pit.parquet (symbol, event_time, available_at,
        open, high, low, close, volume, amount).
    valuation_parquet:
        Path to valuation_daily_pit.parquet (symbol, event_time, pe_ttm,
        pb, ps_ttm, pcf_ttm, is_st, available_at).
    industry_parquet:
        Optional industry_classification_pit.parquet (symbol, industry_level1).
        A3 features are null columns when omitted.
    macro_parquet:
        Optional macro_cn_monthly_pit.parquet.
        A4 features are null columns when omitted.
    target_days:
        Forward return horizon in trading days (default 5).
    sample_symbols:
        Randomly sample N symbols before feature engineering (smoke runs).

    Returns
    -------
    UniverseBundle with canonical schema (asset_id replaces symbol).
    target_col is f"fwd_ret_{target_days}d".
    """
    kline_df = pl.read_parquet(kline_parquet)
    val_df = pl.read_parquet(valuation_parquet)

    # Merge kline + valuation on (symbol, event_time)
    merge_cols = ["symbol", "event_time", "open", "high", "low", "close", "volume", "amount"]
    kline_core = kline_df.select([c for c in merge_cols if c in kline_df.columns])
    val_core = val_df.select(
        [c for c in ["symbol", "event_time", "pe_ttm", "pb", "ps_ttm", "pcf_ttm", "is_st", "available_at"]
         if c in val_df.columns]
    )
    combined = kline_core.join(val_core, on=["symbol", "event_time"], how="inner")

    if sample_symbols is not None:
        syms = combined["symbol"].unique().sample(n=sample_symbols, seed=42).to_list()
        combined = combined.filter(pl.col("symbol").is_in(syms))

    macro_df = pl.read_parquet(macro_parquet) if macro_parquet is not None else None
    industry_df = pl.read_parquet(industry_parquet) if industry_parquet is not None else None

    # All A1..A5 features + fwd_ret_{target_days}d target (PR-1 stable)
    df = build_ashare_feature_matrix(
        combined,
        macro_df=macro_df,
        industry_df=industry_df,
        target_days=target_days,
    )

    # Schema normalization: symbol -> asset_id, add market="ashare"
    df = ashare_to_panel(df)

    target_col = f"fwd_ret_{target_days}d"
    return UniverseBundle(
        df_feat=df,
        feature_cols=ASHARE_FEATURE_COLS,
        feature_names=ASHARE_FEATURE_NAMES,
        target_col=target_col,
        asset_col="asset_id",
    )


def load_for_mining(universe: str, **paths: Any) -> UniverseBundle:
    """Universe-agnostic dispatch.

    Parameters
    ----------
    universe:
        "crypto" or "ashare"
    **paths:
        Forwarded to load_crypto() or load_ashare() as kwargs.
        Crypto: combined_parquet
        Ashare: kline_parquet, valuation_parquet, industry_parquet,
                macro_parquet, target_days, sample_symbols

    Raises
    ------
    ValueError for unknown universe.
    """
    if universe == "crypto":
        return load_crypto(**paths)
    if universe == "ashare":
        return load_ashare(**paths)
    raise ValueError(f"Unknown universe: {universe!r}. Expected 'crypto' or 'ashare'.")
