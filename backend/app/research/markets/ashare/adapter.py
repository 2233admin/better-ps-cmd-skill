"""A-share schema adapter for the shared factor core."""

from __future__ import annotations

import polars as pl


def to_factor_panel(frame: pl.DataFrame) -> pl.DataFrame:
    """Project an A-share PIT frame into the canonical factor-core panel."""

    renamed = frame
    if "date" in renamed.columns and "event_time" not in renamed.columns:
        renamed = renamed.rename({"date": "event_time"})
    if "symbol" in renamed.columns:
        renamed = renamed.rename({"symbol": "asset_id"})
    if "amount" in renamed.columns and "notional" not in renamed.columns:
        renamed = renamed.rename({"amount": "notional"})

    if "market" not in renamed.columns:
        renamed = renamed.with_columns(pl.lit("ashare").alias("market"))

    return renamed
