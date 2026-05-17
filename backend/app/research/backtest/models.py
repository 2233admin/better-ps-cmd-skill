"""Auditable A-share backtest ledger models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    date: date | datetime
    symbol: str
    side: str
    qty: float
    intended_price: float
    status: str
    reject_reason: str = ""


@dataclass(frozen=True)
class FillRecord:
    fill_id: str
    order_id: str
    date: date | datetime
    symbol: str
    side: str
    qty: float
    price: float
    commission: float
    slippage: float


@dataclass(frozen=True)
class DailyLedgerRecord:
    date: date | datetime
    cash: float
    position_qty: float
    position_value: float
    total_equity: float
    daily_pnl: float
    drawdown: float
    funding_cashflow: float = 0.0
    margin_used: float = 0.0


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    entry_date: date | datetime
    exit_date: date | datetime
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    return_pct: float


@dataclass(frozen=True)
class LedgerBacktestResult:
    symbol: str
    window: str
    initial_capital: float
    final_equity: float
    total_return: float
    max_drawdown: float
    orders: tuple[OrderRecord, ...] = ()
    fills: tuple[FillRecord, ...] = ()
    daily_ledger: tuple[DailyLedgerRecord, ...] = ()
    trades: tuple[TradeRecord, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)

    def metrics(self) -> dict[str, float]:
        wins = [trade for trade in self.trades if trade.pnl > 0]
        turnover = sum(fill.qty * fill.price for fill in self.fills) / self.initial_capital
        funding_capture = sum(row.funding_cashflow for row in self.daily_ledger)
        max_margin_used = max((row.margin_used for row in self.daily_ledger), default=0.0)
        return {
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "win_rate": len(wins) / len(self.trades) if self.trades else 0.0,
            "turnover": turnover,
            "funding_capture": funding_capture,
            "max_margin_used": max_margin_used,
            "trade_count": float(len(self.trades)),
            "final_equity": self.final_equity,
        }

    def to_audit_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("orders", "fills", "daily_ledger", "trades"):
            payload[key] = [
                {
                    field: value.isoformat() if isinstance(value, date) else value
                    for field, value in item.items()
                }
                for item in payload[key]
            ]
        return payload
