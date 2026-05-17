"""Unit tests for the typed AccountReader contract + MultiSourceAccountReader fallback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.trade.account_reader import (
    AccountReader,
    BalanceRecord,
    EntrustRecord,
    MultiSourceAccountReader,
    PositionRecord,
    TradeRecord,
)


class _StubReader:
    """Mock AccountReader for fallback-logic tests."""

    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        positions: list[PositionRecord] | None = None,
        trades: list[TradeRecord] | None = None,
        entrusts: list[EntrustRecord] | None = None,
        balance: BalanceRecord | None = None,
        raise_on: set[str] | None = None,
    ):
        self.name = name
        self._available = available
        self._positions = positions or []
        self._trades = trades or []
        self._entrusts = entrusts or []
        self._balance = balance
        self._raise_on = raise_on or set()

    def is_available(self) -> bool:
        if "is_available" in self._raise_on:
            raise RuntimeError("boom")
        return self._available

    def get_balance(self) -> BalanceRecord | None:
        if "get_balance" in self._raise_on:
            raise RuntimeError("boom")
        return self._balance

    def get_positions(self) -> list[PositionRecord]:
        if "get_positions" in self._raise_on:
            raise RuntimeError("boom")
        return self._positions

    def get_today_trades(self) -> list[TradeRecord]:
        if "get_today_trades" in self._raise_on:
            raise RuntimeError("boom")
        return self._trades

    def get_today_entrusts(self) -> list[EntrustRecord]:
        if "get_today_entrusts" in self._raise_on:
            raise RuntimeError("boom")
        return self._entrusts


def _pos(symbol: str, qty: int, source: str) -> PositionRecord:
    return PositionRecord(
        symbol=symbol,
        quantity=qty,
        available_quantity=qty,
        avg_cost=10.0,
        market_value=qty * 10.0,
        source=source,
    )


def _trade(req: str, source: str) -> TradeRecord:
    return TradeRecord(
        request_id=req,
        symbol="600000.SH",
        side="buy",
        fill_qty=100,
        fill_price=10.5,
        timestamp=datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
        source=source,
    )


def _entrust(req: str, source: str) -> EntrustRecord:
    return EntrustRecord(
        request_id=req,
        symbol="600000.SH",
        side="buy",
        qty=100,
        price=10.5,
        status="submitted",
        timestamp=datetime(2026, 5, 18, 13, 50, tzinfo=UTC),
        source=source,
    )


def _balance(source: str, cash: float = 100_000.0) -> BalanceRecord:
    return BalanceRecord(
        available_cash=cash,
        frozen_cash=0.0,
        total_assets=cash,
        source=source,
    )


def test_stub_reader_satisfies_account_reader_protocol():
    stub = _StubReader("stub")
    assert isinstance(stub, AccountReader)


def test_multi_source_requires_at_least_one_reader():
    with pytest.raises(ValueError):
        MultiSourceAccountReader([])


def test_multi_source_returns_primary_when_available():
    primary = _StubReader("tdx", positions=[_pos("600000.SH", 100, "tdx")])
    secondary = _StubReader("qmt", positions=[_pos("600000.SH", 999, "qmt")])
    reader = MultiSourceAccountReader([primary, secondary])

    positions = reader.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 100
    assert positions[0].source == "tdx"


def test_multi_source_falls_back_when_primary_unavailable():
    primary = _StubReader("tdx", available=False, positions=[_pos("600000.SH", 100, "tdx")])
    secondary = _StubReader("qmt", positions=[_pos("600000.SH", 200, "qmt")])
    reader = MultiSourceAccountReader([primary, secondary])

    positions = reader.get_positions()
    assert positions[0].source == "qmt"
    assert positions[0].quantity == 200


def test_multi_source_falls_back_when_primary_returns_empty():
    primary = _StubReader("tdx", positions=[])
    secondary = _StubReader("qmt", positions=[_pos("600000.SH", 50, "qmt")])
    reader = MultiSourceAccountReader([primary, secondary])

    positions = reader.get_positions()
    assert positions[0].source == "qmt"


def test_multi_source_swallows_exception_and_falls_back():
    primary = _StubReader("tdx", raise_on={"get_positions"})
    secondary = _StubReader("qmt", positions=[_pos("600000.SH", 33, "qmt")])
    reader = MultiSourceAccountReader([primary, secondary])

    positions = reader.get_positions()
    assert positions[0].quantity == 33


def test_multi_source_returns_empty_when_all_sources_silent():
    reader = MultiSourceAccountReader(
        [_StubReader("tdx", positions=[]), _StubReader("qmt", positions=[])]
    )
    assert reader.get_positions() == []
    assert reader.get_today_trades() == []
    assert reader.get_today_entrusts() == []


def test_multi_source_balance_prefers_primary():
    primary = _StubReader("tdx", balance=_balance("tdx", 50_000.0))
    secondary = _StubReader("qmt", balance=_balance("qmt", 999_999.0))
    reader = MultiSourceAccountReader([primary, secondary])

    balance = reader.get_balance()
    assert balance is not None
    assert balance.source == "tdx"
    assert balance.available_cash == 50_000.0


def test_multi_source_balance_falls_back_to_secondary():
    primary = _StubReader("tdx", balance=None)
    secondary = _StubReader("qmt", balance=_balance("qmt", 80_000.0))
    reader = MultiSourceAccountReader([primary, secondary])

    balance = reader.get_balance()
    assert balance is not None
    assert balance.source == "qmt"
    assert balance.available_cash == 80_000.0


def test_multi_source_trade_and_entrust_fallback():
    primary = _StubReader("tdx", trades=[], entrusts=[])
    secondary = _StubReader(
        "qmt",
        trades=[_trade("req-1", "qmt")],
        entrusts=[_entrust("req-1", "qmt")],
    )
    reader = MultiSourceAccountReader([primary, secondary])

    assert reader.get_today_trades()[0].source == "qmt"
    assert reader.get_today_entrusts()[0].source == "qmt"


def test_multi_source_is_available_reflects_any():
    reader = MultiSourceAccountReader(
        [_StubReader("tdx", available=False), _StubReader("qmt", available=True)]
    )
    assert reader.is_available() is True

    reader = MultiSourceAccountReader(
        [_StubReader("tdx", available=False), _StubReader("qmt", available=False)]
    )
    assert reader.is_available() is False


def test_multi_source_skips_reader_whose_availability_raises():
    primary = _StubReader("tdx", raise_on={"is_available"})
    secondary = _StubReader("qmt", positions=[_pos("600000.SH", 7, "qmt")])
    reader = MultiSourceAccountReader([primary, secondary])

    assert reader.is_available() is True
    assert reader.get_positions()[0].source == "qmt"
