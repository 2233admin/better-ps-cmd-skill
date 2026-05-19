"""A-share tradability constraints kept outside the shared factor core."""

from __future__ import annotations

import polars as pl


def fill_tradability_defaults(frame: pl.DataFrame) -> pl.DataFrame:
    """Provide stable defaults for missing A-share tradability columns."""

    defaults = {
        "is_st": False,
        "is_suspended": False,
        "limit_up": None,
        "limit_down": None,
        "listed_days": None,
        "is_tradable": True,
        "reason": None,
    }
    out = frame
    for column, value in defaults.items():
        if column not in out.columns:
            out = out.with_columns(pl.lit(value).alias(column))
    return out
