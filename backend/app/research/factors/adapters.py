"""Interfaces for market-specific schema adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class MarketFactorAdapter(Protocol):
    """Maps market-local PIT data into the shared factor-core panel."""

    market: str

    def to_factor_panel(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Return a canonical factor-core panel frame."""

