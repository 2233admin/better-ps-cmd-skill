"""Point-in-time query contracts.

The current implementation supports price bars through DuckDB and makes the
PIT boundary explicit. Non-price datasets must implement publication-time
metadata before they can be used in research experiments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

import polars as pl

from ..data.store import DuckDBStore
from .models import Frequency, Market


class PITDataset(str, Enum):
    KLINE_DAILY = "kline_daily"
    KLINE_MINUTE = "kline_minute"


@dataclass(frozen=True)
class PointInTimeQuery:
    dataset: PITDataset
    market: Market
    symbol: str
    as_of: datetime
    start: date | datetime | None = None
    end: date | datetime | None = None
    frequency: Frequency = Frequency.DAILY

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.end is not None and self.start is not None and self.end < self.start:
            raise ValueError("end cannot be earlier than start")


def ashare_market_code(symbol: str) -> int:
    """Return pytdx market code: 1 for SH-like symbols, 0 for SZ-like symbols."""
    raw = symbol.upper().split(".")[0]
    return 1 if raw.startswith(("6", "9", "11", "51", "58")) else 0


class PointInTimeStore:
    """PIT facade over DuckDBStore for datasets that have safe query semantics."""

    def __init__(self, store: DuckDBStore):
        self.store = store

    def query(self, request: PointInTimeQuery) -> pl.DataFrame:
        if request.market != Market.ASHARE:
            raise ValueError("PointInTimeStore currently supports ashare only")
        if request.dataset == PITDataset.KLINE_DAILY:
            return self._query_daily(request)
        if request.dataset == PITDataset.KLINE_MINUTE:
            return self._query_minute(request)
        raise ValueError(f"unsupported PIT dataset: {request.dataset}")

    def _query_daily(self, request: PointInTimeQuery) -> pl.DataFrame:
        end_date = min(
            request.end.date() if isinstance(request.end, datetime) else request.end,
            request.as_of.date(),
        ) if request.end else request.as_of.date()
        start_date = (
            request.start.date() if isinstance(request.start, datetime) else request.start
        )
        return self.store.get_kline_daily(
            request.symbol,
            market=ashare_market_code(request.symbol),
            start_date=start_date.isoformat() if start_date else "",
            end_date=end_date.isoformat(),
        )

    def _query_minute(self, request: PointInTimeQuery) -> pl.DataFrame:
        end = request.end if request.end else request.as_of
        if isinstance(end, date) and not isinstance(end, datetime):
            end = datetime.combine(end, datetime.max.time())
        end = min(end, request.as_of)
        start = request.start
        if isinstance(start, date) and not isinstance(start, datetime):
            start = datetime.combine(start, datetime.min.time())
        return self.store.get_kline_minute(
            request.symbol,
            market=ashare_market_code(request.symbol),
            start=start.isoformat() if start else "",
            end=end.isoformat(),
        )
