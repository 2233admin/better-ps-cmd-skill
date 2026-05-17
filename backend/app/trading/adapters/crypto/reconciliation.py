"""Deterministic crypto paper/sim reconciliation.

The replay consumes Katana intents and PIT prices only. It deliberately has no
exchange client dependency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import polars as pl

from .exchange_adapter import CryptoOrderIntent


@dataclass(frozen=True)
class CryptoSimFill:
    request_id: str
    inst_id: str
    side: str
    qty: float
    price: float
    fee: float
    event_time: datetime


@dataclass(frozen=True)
class CryptoSimReconciliationReport:
    accepted: int
    rejected: int
    fills: tuple[CryptoSimFill, ...]
    cash_diff: float
    position_diff: dict[str, float]
    reject_reasons: tuple[str, ...]

    @property
    def fill_count(self) -> int:
        return len(self.fills)

    def to_payload(self) -> dict:
        payload = asdict(self)
        payload["fill_count"] = self.fill_count
        payload["fills"] = [
            {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in item.items()
            }
            for item in payload["fills"]
        ]
        return payload


def replay_crypto_intents(
    intents: tuple[CryptoOrderIntent, ...],
    pit_frame: pl.DataFrame,
    *,
    allowed_pairs: set[str] | None = None,
    max_notional: float = 1_000.0,
    taker_fee_rate: float = 0.0005,
) -> CryptoSimReconciliationReport:
    price_by_inst = _last_visible_prices(pit_frame)
    allowed = {item.upper() for item in allowed_pairs} if allowed_pairs else None
    cash = 0.0
    positions: dict[str, float] = {}
    fills: list[CryptoSimFill] = []
    reject_reasons: list[str] = []

    for intent in intents:
        pair = _base_pair(intent.inst_id)
        if allowed is not None and pair not in allowed:
            reject_reasons.append(f"{intent.request_id}:pair_not_allowed")
            continue
        if intent.notional > max_notional:
            reject_reasons.append(f"{intent.request_id}:notional_exceeds_limit")
            continue
        if intent.inst_id not in price_by_inst:
            reject_reasons.append(f"{intent.request_id}:missing_price")
            continue
        event_time, reference_price = price_by_inst[intent.inst_id]
        price = intent.limit_price or reference_price
        fee = intent.qty * price * taker_fee_rate
        signed_qty = intent.qty if intent.side in {"buy", "long", "cover"} else -intent.qty
        positions[intent.inst_id] = positions.get(intent.inst_id, 0.0) + signed_qty
        cash -= signed_qty * price + fee
        fills.append(
            CryptoSimFill(
                request_id=intent.request_id,
                inst_id=intent.inst_id,
                side=intent.side,
                qty=intent.qty,
                price=price,
                fee=fee,
                event_time=event_time,
            )
        )

    return CryptoSimReconciliationReport(
        accepted=len(fills),
        rejected=len(reject_reasons),
        fills=tuple(fills),
        cash_diff=cash,
        position_diff=positions,
        reject_reasons=tuple(reject_reasons),
    )


def _last_visible_prices(pit_frame: pl.DataFrame) -> dict[str, tuple[datetime, float]]:
    prices: dict[str, tuple[datetime, float]] = {}
    if pit_frame.is_empty():
        return prices
    for row in pit_frame.sort(["inst_id", "event_time"]).iter_rows(named=True):
        prices[str(row["inst_id"])] = (row["event_time"], float(row["close"]))
    return prices


def _base_pair(inst_id: str) -> str:
    if inst_id.endswith("-SWAP"):
        return inst_id.removesuffix("-SWAP").upper()
    parts = inst_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0].upper()}-{parts[1].upper()}"
    return inst_id.upper()
