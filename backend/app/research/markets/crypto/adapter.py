"""Crypto schema adapter for the shared factor core."""

from __future__ import annotations

import polars as pl


def to_factor_panel(frame: pl.DataFrame) -> pl.DataFrame:
    """Project a crypto PIT frame into the canonical factor-core panel."""

    renamed = frame
    if "inst_id" in renamed.columns:
        renamed = renamed.rename({"inst_id": "asset_id"})
    if "quote_volume" in renamed.columns and "notional" not in renamed.columns:
        renamed = renamed.rename({"quote_volume": "notional"})
    elif "amount" in renamed.columns and "notional" not in renamed.columns:
        renamed = renamed.rename({"amount": "notional"})

    if "market" not in renamed.columns:
        renamed = renamed.with_columns(pl.lit("crypto").alias("market"))

    return renamed
