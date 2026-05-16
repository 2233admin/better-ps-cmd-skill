"""Trade intent contract for the EasyXT phase-1 bridge."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional
from uuid import uuid4


VALID_SIDES = {"buy", "sell"}


@dataclass(frozen=True)
class TradeIntent:
    """Broker-agnostic order intent emitted by research code."""

    request_id: str
    strategy: str
    account: str
    symbol: str
    side: str
    qty: int
    dry_run: bool = True
    note: Optional[str] = None

    def validate(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id is required")
        if not self.strategy.strip():
            raise ValueError("strategy is required")
        if not self.account.strip():
            raise ValueError("account is required")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.side not in VALID_SIDES:
            raise ValueError(f"side must be one of {sorted(VALID_SIDES)}")
        if self.qty <= 0:
            raise ValueError("qty must be positive")

    def to_bridge_payload(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)


def make_trade_intent(
    *,
    strategy: str,
    account: str,
    symbol: str,
    side: str,
    qty: int,
    dry_run: bool = True,
    note: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TradeIntent:
    """Create a validated research-side intent without broker-specific fields."""

    intent = TradeIntent(
        request_id=request_id or f"{strategy}-{symbol}-{uuid4().hex}",
        strategy=strategy,
        account=account,
        symbol=symbol,
        side=side,
        qty=qty,
        dry_run=dry_run,
        note=note,
    )
    intent.validate()
    return intent
