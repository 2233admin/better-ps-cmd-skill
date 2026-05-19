"""Compatibility shim for legacy imports."""

from app.research.factors.primitives.transforms import (
    WinsorizeBounds,
    sector_neutralize,
    winsorize,
    zscore,
)

__all__ = ["zscore", "winsorize", "sector_neutralize", "WinsorizeBounds"]
