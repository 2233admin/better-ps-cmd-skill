"""Broker-neutral crypto exchange adapter protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CryptoOrderIntent:
    request_id: str
    inst_id: str
    side: str
    qty: float
    limit_price: float
    market_type: str = "spot"
    strategy: str = "manual"
    dry_run: bool = True

    @property
    def notional(self) -> float:
        return self.qty * self.limit_price


@dataclass(frozen=True)
class CryptoOrderResult:
    request_id: str
    status: str
    inst_id: str
    side: str
    order_id: str = ""
    reason: str = ""


class CryptoExchangeAdapter(Protocol):
    """Exchange adapters may only submit approved intents or fetch state."""

    def submit_intent(self, intent: CryptoOrderIntent) -> CryptoOrderResult:
        """Submit an already-approved intent through the adapter boundary."""

    def query_positions(self) -> list[dict]:
        """Return exchange positions without mutating state."""

    def query_balances(self) -> list[dict]:
        """Return exchange balances without mutating state."""
