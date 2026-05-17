"""Small auditable daily-bar A-share backtester."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import polars as pl

from .models import DailyLedgerRecord, FillRecord, LedgerBacktestResult, OrderRecord, TradeRecord


@dataclass(frozen=True)
class AShareBacktestConfig:
    initial_capital: float = 1_000_000.0
    position_cap: float = 0.05
    commission_rate: float = 0.0005
    slippage_rate: float = 0.001
    lot_size: int = 100
    enforce_t_plus_one: bool = True


class AShareLedgerBacktester:
    """Backtest with explicit orders, fills, daily ledger, and A-share constraints."""

    def __init__(self, config: AShareBacktestConfig | None = None) -> None:
        self.config = config or AShareBacktestConfig()

    def run(self, bars: pl.DataFrame, signals: pl.DataFrame, *, symbol: str) -> LedgerBacktestResult:
        if bars.is_empty():
            return LedgerBacktestResult(
                symbol=symbol,
                window="",
                initial_capital=self.config.initial_capital,
                final_equity=self.config.initial_capital,
                total_return=0.0,
                max_drawdown=0.0,
                parameters=self._parameters(),
            )

        time_col = _time_column(bars)
        signal_time_col = _time_column(signals)
        frame = (
            bars.sort(time_col)
            .join(signals.rename({signal_time_col: time_col}), on=time_col, how="left")
            .with_columns(pl.col("signal").fill_null(0))
        )
        dates = [_as_date(value) for value in frame[time_col].to_list()]
        closes = [float(value) for value in frame["close"].to_list()]
        signal_values = [int(value) for value in frame["signal"].to_list()]

        cash = self.config.initial_capital
        position_qty = 0
        entry_price = 0.0
        entry_date: date | None = None
        peak_equity = self.config.initial_capital
        previous_equity = self.config.initial_capital
        orders: list[OrderRecord] = []
        fills: list[FillRecord] = []
        ledger: list[DailyLedgerRecord] = []
        trades: list[TradeRecord] = []

        for idx, current_date in enumerate(dates):
            price = closes[idx]
            signal = signal_values[idx]

            if signal == 1 and position_qty == 0:
                qty = self._target_qty(cash, price)
                order, fill = self._order(
                    idx=idx,
                    current_date=current_date,
                    symbol=symbol,
                    side="buy",
                    qty=qty,
                    price=price,
                    row=frame.row(idx, named=True),
                )
                orders.append(order)
                if fill:
                    fills.append(fill)
                    cash -= fill.qty * fill.price + fill.commission
                    position_qty = fill.qty
                    entry_price = fill.price
                    entry_date = current_date

            elif signal == -1 and position_qty > 0:
                reject_reason = ""
                if (
                    self.config.enforce_t_plus_one
                    and entry_date is not None
                    and current_date <= entry_date
                ):
                    reject_reason = "t_plus_one"
                if reject_reason:
                    orders.append(
                        OrderRecord(
                            order_id=f"ord-{idx:04d}",
                            date=current_date,
                            symbol=symbol,
                            side="sell",
                            qty=position_qty,
                            intended_price=price,
                            status="rejected",
                            reject_reason=reject_reason,
                        )
                    )
                else:
                    order, fill = self._order(
                        idx=idx,
                        current_date=current_date,
                        symbol=symbol,
                        side="sell",
                        qty=position_qty,
                        price=price,
                        row=frame.row(idx, named=True),
                    )
                    orders.append(order)
                    if fill:
                        fills.append(fill)
                        proceeds = fill.qty * fill.price - fill.commission
                        cash += proceeds
                        trades.append(
                            TradeRecord(
                                symbol=symbol,
                                entry_date=entry_date or current_date,
                                exit_date=current_date,
                                qty=fill.qty,
                                entry_price=entry_price,
                                exit_price=fill.price,
                                pnl=(fill.price - entry_price) * fill.qty - fill.commission,
                                return_pct=(fill.price - entry_price) / entry_price
                                if entry_price
                                else 0.0,
                            )
                        )
                        position_qty = 0
                        entry_price = 0.0
                        entry_date = None

            position_value = position_qty * price
            total_equity = cash + position_value
            peak_equity = max(peak_equity, total_equity)
            drawdown = (peak_equity - total_equity) / peak_equity if peak_equity else 0.0
            ledger.append(
                DailyLedgerRecord(
                    date=current_date,
                    cash=cash,
                    position_qty=position_qty,
                    position_value=position_value,
                    total_equity=total_equity,
                    daily_pnl=total_equity - previous_equity,
                    drawdown=drawdown,
                    margin_used=position_value,
                )
            )
            previous_equity = total_equity

        final_equity = ledger[-1].total_equity if ledger else self.config.initial_capital
        window = f"{dates[0].isoformat()}/{dates[-1].isoformat()}" if dates else ""
        return LedgerBacktestResult(
            symbol=symbol,
            window=window,
            initial_capital=self.config.initial_capital,
            final_equity=final_equity,
            total_return=(final_equity / self.config.initial_capital) - 1,
            max_drawdown=max((row.drawdown for row in ledger), default=0.0),
            orders=tuple(orders),
            fills=tuple(fills),
            daily_ledger=tuple(ledger),
            trades=tuple(trades),
            parameters=self._parameters(),
        )

    def _target_qty(self, cash: float, price: float) -> int:
        budget = min(cash, self.config.initial_capital * self.config.position_cap)
        raw_qty = int(budget // price)
        return (raw_qty // self.config.lot_size) * self.config.lot_size

    def _order(
        self,
        *,
        idx: int,
        current_date: date,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        row: dict,
    ) -> tuple[OrderRecord, FillRecord | None]:
        reject_reason = self._reject_reason(side, qty, row)
        order_id = f"ord-{idx:04d}"
        if reject_reason:
            return (
                OrderRecord(
                    order_id=order_id,
                    date=current_date,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    intended_price=price,
                    status="rejected",
                    reject_reason=reject_reason,
                ),
                None,
            )
        fill_price = price * (
            1 + self.config.slippage_rate if side == "buy" else 1 - self.config.slippage_rate
        )
        notional = qty * fill_price
        commission = notional * self.config.commission_rate
        return (
            OrderRecord(
                order_id=order_id,
                date=current_date,
                symbol=symbol,
                side=side,
                qty=qty,
                intended_price=price,
                status="filled",
            ),
            FillRecord(
                fill_id=f"fill-{idx:04d}",
                order_id=order_id,
                date=current_date,
                symbol=symbol,
                side=side,
                qty=qty,
                price=fill_price,
                commission=commission,
                slippage=abs(fill_price - price),
            ),
        )

    def _reject_reason(self, side: str, qty: int, row: dict) -> str:
        if qty <= 0:
            return "qty_below_lot_size"
        if bool(row.get("is_suspended", False)):
            return "suspended"
        if bool(row.get("is_st", False)):
            return "st_stock"
        if "is_tradable" in row and not bool(row.get("is_tradable")):
            return str(row.get("reason") or row.get("tradable_reason") or "not_tradable")
        if side == "buy" and bool(row.get("limit_up", False)):
            return "limit_up"
        if side == "sell" and bool(row.get("limit_down", False)):
            return "limit_down"
        if float(row.get("volume", 1) or 0) <= 0:
            return "no_volume"
        return ""

    def _parameters(self) -> dict:
        return {
            "initial_capital": self.config.initial_capital,
            "position_cap": self.config.position_cap,
            "commission_rate": self.config.commission_rate,
            "slippage_rate": self.config.slippage_rate,
            "lot_size": self.config.lot_size,
            "enforce_t_plus_one": self.config.enforce_t_plus_one,
        }


@dataclass(frozen=True)
class CryptoBacktestConfig:
    initial_capital: float = 100_000.0
    position_cap: float = 0.10
    taker_fee_rate: float = 0.0005
    slippage_rate: float = 0.0005
    min_order_qty: float = 0.0001
    market_type: Literal["spot", "swap"] = "spot"
    leverage: float = 1.0
    maintenance_margin_rate: float = 0.005
    allow_short: bool = False


class CryptoLedgerBacktester:
    """Backtest crypto spot/swap bars with explicit costs and optional funding."""

    def __init__(self, config: CryptoBacktestConfig | None = None) -> None:
        self.config = config or CryptoBacktestConfig()
        if self.config.market_type == "spot" and self.config.allow_short:
            raise ValueError("spot crypto backtest cannot allow short")
        if self.config.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.config.position_cap <= 0:
            raise ValueError("position_cap must be positive")
        if self.config.leverage <= 0:
            raise ValueError("leverage must be positive")

    def run(self, bars: pl.DataFrame, signals: pl.DataFrame, *, symbol: str) -> LedgerBacktestResult:
        if bars.is_empty():
            return LedgerBacktestResult(
                symbol=symbol,
                window="",
                initial_capital=self.config.initial_capital,
                final_equity=self.config.initial_capital,
                total_return=0.0,
                max_drawdown=0.0,
                parameters=self._parameters(),
            )

        time_col = _time_column(bars)
        signal_time_col = _time_column(signals)
        frame = (
            bars.sort(time_col)
            .join(signals.rename({signal_time_col: time_col}), on=time_col, how="left")
            .with_columns(pl.col("signal").fill_null(0))
        )
        times = [_as_timestamp(value) for value in frame[time_col].to_list()]
        closes = [float(value) for value in frame["close"].to_list()]
        signal_values = [int(value) for value in frame["signal"].to_list()]
        funding_rates = (
            [float(value or 0.0) for value in frame["funding_rate"].to_list()]
            if "funding_rate" in frame.columns
            else [0.0] * len(closes)
        )

        cash = self.config.initial_capital
        position_qty = 0.0
        position_side = 0
        entry_price = 0.0
        entry_time: date | datetime | None = None
        peak_equity = self.config.initial_capital
        previous_equity = self.config.initial_capital
        orders: list[OrderRecord] = []
        fills: list[FillRecord] = []
        ledger: list[DailyLedgerRecord] = []
        trades: list[TradeRecord] = []

        for idx, current_time in enumerate(times):
            price = closes[idx]
            target_side = self._target_side(signal_values[idx])

            funding_cashflow = 0.0
            if position_side and self.config.market_type == "swap":
                funding_cashflow = -abs(position_qty) * price * funding_rates[idx] * position_side
                cash += funding_cashflow

            if target_side != position_side:
                if position_side:
                    order, fill = self._order(
                        idx=idx,
                        current_time=current_time,
                        symbol=symbol,
                        side="sell" if position_side > 0 else "cover",
                        qty=abs(position_qty),
                        price=price,
                    )
                    orders.append(order)
                    if fill:
                        fills.append(fill)
                        cash += self._closing_cash_delta(position_side, fill, entry_price)
                        trades.append(
                            TradeRecord(
                                symbol=symbol,
                                entry_date=entry_time or current_time,
                                exit_date=current_time,
                                qty=fill.qty,
                                entry_price=entry_price,
                                exit_price=fill.price,
                                pnl=self._trade_pnl(position_side, fill.qty, entry_price, fill.price)
                                - fill.commission,
                                return_pct=self._return_pct(position_side, entry_price, fill.price),
                            )
                        )
                        position_qty = 0.0
                        position_side = 0
                        entry_price = 0.0
                        entry_time = None

                if target_side:
                    qty = self._target_qty(cash, price)
                    order, fill = self._order(
                        idx=idx,
                        current_time=current_time,
                        symbol=symbol,
                        side="buy" if target_side > 0 else "short",
                        qty=qty,
                        price=price,
                    )
                    orders.append(order)
                    if fill:
                        fills.append(fill)
                        cash -= fill.commission
                        if target_side > 0:
                            cash -= fill.qty * fill.price / self.config.leverage
                        else:
                            cash += fill.qty * fill.price / self.config.leverage
                        position_qty = fill.qty * target_side
                        position_side = target_side
                        entry_price = fill.price
                        entry_time = current_time

            position_value = self._position_value(position_qty, price, entry_price)
            margin_used = abs(position_qty) * entry_price / self.config.leverage if position_qty else 0.0
            total_equity = cash + position_value
            peak_equity = max(peak_equity, total_equity)
            drawdown = (peak_equity - total_equity) / peak_equity if peak_equity else 0.0
            ledger.append(
                DailyLedgerRecord(
                    date=current_time,
                    cash=cash,
                    position_qty=position_qty,
                    position_value=position_value,
                    total_equity=total_equity,
                    daily_pnl=total_equity - previous_equity,
                    drawdown=drawdown,
                    funding_cashflow=funding_cashflow,
                    margin_used=margin_used,
                )
            )
            previous_equity = total_equity

        final_equity = ledger[-1].total_equity if ledger else self.config.initial_capital
        window = f"{times[0].isoformat()}/{times[-1].isoformat()}" if times else ""
        return LedgerBacktestResult(
            symbol=symbol,
            window=window,
            initial_capital=self.config.initial_capital,
            final_equity=final_equity,
            total_return=(final_equity / self.config.initial_capital) - 1,
            max_drawdown=max((row.drawdown for row in ledger), default=0.0),
            orders=tuple(orders),
            fills=tuple(fills),
            daily_ledger=tuple(ledger),
            trades=tuple(trades),
            parameters=self._parameters(),
        )

    def _target_side(self, signal: int) -> int:
        if signal > 0:
            return 1
        if signal < 0 and self.config.market_type == "swap" and self.config.allow_short:
            return -1
        return 0

    def _target_qty(self, cash: float, price: float) -> float:
        notional = self.config.initial_capital * self.config.position_cap * self.config.leverage
        if self.config.market_type == "spot":
            notional = min(notional, max(cash, 0.0))
        qty = notional / price if price > 0 else 0.0
        return round(qty, 8)

    def _order(
        self,
        *,
        idx: int,
        current_time: date | datetime,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> tuple[OrderRecord, FillRecord | None]:
        order_id = f"cord-{idx:04d}"
        reject_reason = self._reject_reason(qty, price)
        if reject_reason:
            return (
                OrderRecord(
                    order_id=order_id,
                    date=current_time,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    intended_price=price,
                    status="rejected",
                    reject_reason=reject_reason,
                ),
                None,
            )
        fill_price = price * (
            1 + self.config.slippage_rate
            if side in {"buy", "cover"}
            else 1 - self.config.slippage_rate
        )
        commission = qty * fill_price * self.config.taker_fee_rate
        return (
            OrderRecord(
                order_id=order_id,
                date=current_time,
                symbol=symbol,
                side=side,
                qty=qty,
                intended_price=price,
                status="filled",
            ),
            FillRecord(
                fill_id=f"cfill-{idx:04d}",
                order_id=order_id,
                date=current_time,
                symbol=symbol,
                side=side,
                qty=qty,
                price=fill_price,
                commission=commission,
                slippage=abs(fill_price - price),
            ),
        )

    def _reject_reason(self, qty: float, price: float) -> str:
        if price <= 0:
            return "invalid_price"
        if qty < self.config.min_order_qty:
            return "qty_below_min_order"
        return ""

    def _position_value(self, position_qty: float, price: float, entry_price: float) -> float:
        if not position_qty:
            return 0.0
        if self.config.market_type == "spot":
            return position_qty * price
        return abs(position_qty) * entry_price / self.config.leverage + (
            (price - entry_price) * position_qty
        )

    def _closing_cash_delta(self, position_side: int, fill: FillRecord, entry_price: float) -> float:
        if self.config.market_type == "spot":
            return fill.qty * fill.price - fill.commission
        margin_release = fill.qty * entry_price / self.config.leverage
        pnl = self._trade_pnl(position_side, fill.qty, entry_price, fill.price)
        return margin_release + pnl - fill.commission

    def _trade_pnl(self, position_side: int, qty: float, entry_price: float, exit_price: float) -> float:
        return (exit_price - entry_price) * qty * position_side

    def _return_pct(self, position_side: int, entry_price: float, exit_price: float) -> float:
        if entry_price <= 0:
            return 0.0
        return ((exit_price - entry_price) / entry_price) * position_side

    def _parameters(self) -> dict:
        return {
            "initial_capital": self.config.initial_capital,
            "position_cap": self.config.position_cap,
            "taker_fee_rate": self.config.taker_fee_rate,
            "slippage_rate": self.config.slippage_rate,
            "min_order_qty": self.config.min_order_qty,
            "market_type": self.config.market_type,
            "leverage": self.config.leverage,
            "maintenance_margin_rate": self.config.maintenance_margin_rate,
            "allow_short": self.config.allow_short,
        }


def _time_column(frame: pl.DataFrame) -> str:
    for column in ("event_time", "datetime", "date"):
        if column in frame.columns:
            return column
    raise ValueError("frame must include event_time, datetime, or date")


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f"unsupported date value: {value!r}")


def _as_timestamp(value: object) -> date | datetime:
    if isinstance(value, datetime | date):
        return value
    raise ValueError(f"unsupported timestamp value: {value!r}")
