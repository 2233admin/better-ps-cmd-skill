"""Research-side crypto paper reconciliation.

This module consumes research signals and PIT prices only. It stays inside the
research package so the crypto pipeline does not import broker adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import polars as pl

from .factors import CryptoSignal


@dataclass(frozen=True)
class PaperFill:
    inst_id: str
    side: str
    qty: float
    price: float
    fee: float
    event_time: datetime


@dataclass(frozen=True)
class PaperReconciliationReport:
    accepted: int
    rejected: int
    fill_count: int
    cash_diff: float
    position_diff: dict[str, float]
    reject_reasons: tuple[str, ...]
    fills: tuple[PaperFill, ...]

    def to_payload(self) -> dict:
        payload = asdict(self)
        payload["fills"] = [
            {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in item.items()
            }
            for item in payload["fills"]
        ]
        return payload


def reconcile_crypto_paper(
    signals: tuple[CryptoSignal, ...],
    pit_frame: pl.DataFrame,
    *,
    position_notional: float,
    max_notional: float,
    fee_rate: float = 0.0005,
) -> PaperReconciliationReport:
    prices = _prices_by_time(pit_frame)
    cash = 0.0
    positions: dict[str, float] = {}
    fills: list[PaperFill] = []
    reject_reasons: list[str] = []

    for signal in signals:
        key = (signal.inst_id, signal.timestamp)
        price = prices.get(key)
        if price is None:
            reject_reasons.append(f"{signal.inst_id}@{signal.timestamp.isoformat()}:missing_price")
            continue
        if position_notional > max_notional:
            reject_reasons.append(f"{signal.inst_id}@{signal.timestamp.isoformat()}:notional_exceeds_limit")
            continue
        qty = round(position_notional / price, 8) if price > 0 else 0.0
        if qty <= 0:
            reject_reasons.append(f"{signal.inst_id}@{signal.timestamp.isoformat()}:qty_below_min_order")
            continue
        signed_qty = qty if signal.side in {"buy", "long", "cover"} else -qty
        fee = qty * price * fee_rate
        cash -= signed_qty * price + fee
        positions[signal.inst_id] = positions.get(signal.inst_id, 0.0) + signed_qty
        fills.append(
            PaperFill(
                inst_id=signal.inst_id,
                side=signal.side,
                qty=qty,
                price=price,
                fee=fee,
                event_time=signal.timestamp,
            )
        )

    return PaperReconciliationReport(
        accepted=len(fills),
        rejected=len(reject_reasons),
        fill_count=len(fills),
        cash_diff=cash,
        position_diff=positions,
        reject_reasons=tuple(reject_reasons),
        fills=tuple(fills),
    )


def _prices_by_time(frame: pl.DataFrame) -> dict[tuple[str, datetime], float]:
    prices: dict[tuple[str, datetime], float] = {}
    for row in frame.iter_rows(named=True):
        prices[(str(row["inst_id"]), row["event_time"])] = float(row["close"])
    return prices
