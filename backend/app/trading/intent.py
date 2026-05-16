"""Trading mode and order intent primitives."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class TradingMode(str, Enum):
    RESEARCH = "research"
    PAPER = "paper"
    LIVE_TEST = "live_test"
    LIVE = "live"


def get_trading_mode() -> TradingMode:
    raw = os.environ.get("KATANA_TRADING_MODE", TradingMode.RESEARCH.value).strip().lower()
    try:
        return TradingMode(raw)
    except ValueError:
        return TradingMode.RESEARCH


def require_trading_mode(*allowed: TradingMode) -> TradingMode:
    mode = get_trading_mode()
    if mode not in allowed:
        allowed_values = ", ".join(item.value for item in allowed)
        raise RuntimeError(
            f"KATANA_TRADING_MODE={mode.value} does not allow this action; "
            f"allowed: {allowed_values}"
        )
    return mode


@dataclass(frozen=True)
class OrderIntent:
    code: str
    direction: str
    price: float
    volume: int
    strategy: str = "manual"
    market: int | None = None

    @property
    def notional(self) -> float:
        return self.price * self.volume

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "direction": self.direction,
            "price": self.price,
            "volume": self.volume,
            "strategy": self.strategy,
            "market": self.market,
        }
