"""HMC/materialist dynamics research boundary.

This module is intentionally adapter-shaped: HMC state estimation may influence
research signals, but it must not call trading adapters or submit orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import polars as pl

from .models import HMCStateEstimate


@dataclass(frozen=True)
class HMCInputWindow:
    symbol: str
    as_of: datetime
    bars: pl.DataFrame
    model_version: str = "hmc-boundary-v0"


class HMCResearchAdapter:
    """Deterministic placeholder boundary for future HMC implementation."""

    def estimate_state(self, window: HMCInputWindow) -> HMCStateEstimate:
        if window.bars.is_empty():
            raise ValueError("HMC input bars are required")
        if "close" not in window.bars.columns:
            raise ValueError("HMC input bars must include close")

        closes = window.bars["close"].to_list()
        first = float(closes[0])
        last = float(closes[-1])
        change = (last - first) / max(abs(first), 1e-12)
        risk_score = min(1.0, max(0.0, abs(change) * 5.0))
        regime = "trend_up" if change > 0.03 else "trend_down" if change < -0.03 else "range"

        return HMCStateEstimate(
            symbol=window.symbol,
            timestamp=window.as_of,
            as_of=window.as_of,
            regime=regime,
            risk_score=risk_score,
            posterior={"window_return": change},
            model_version=window.model_version,
        )
