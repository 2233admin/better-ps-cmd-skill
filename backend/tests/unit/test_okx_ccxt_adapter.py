"""Unit tests for OKXCCXTAdapter and okx_pit_writer (XAR-423 spike).

Tests are network-free — they mock ccxt.okx and python-okx PublicAPI.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.markets.crypto.okx_ccxt_adapter import (
    OKXCCXTAdapter,
    _to_ccxt_symbol,
    _to_okx_bar,
)
from app.markets.crypto.okx_pit_writer import (
    backfill_funding_rate,
    backfill_mark_price,
    backfill_open_interest,
    validate_pit_invariants,
)


# ---------------------------------------------------------------------------
# inst_id / symbol translation
# ---------------------------------------------------------------------------

class TestSymbolTranslation:
    def test_swap_to_ccxt(self):
        assert _to_ccxt_symbol("BTC-USDT-SWAP") == "BTC/USDT:USDT"

    def test_spot_to_ccxt(self):
        assert _to_ccxt_symbol("BTC-USDT") == "BTC/USDT"

    def test_eth_swap(self):
        assert _to_ccxt_symbol("ETH-USDT-SWAP") == "ETH/USDT:USDT"


class TestBarMapping:
    def test_lowercase_minute(self):
        assert _to_okx_bar("1m") == "1m"

    def test_uppercase_hour(self):
        assert _to_okx_bar("1H") == "1h"

    def test_daily(self):
        assert _to_okx_bar("1D") == "1d"

    def test_unknown_falls_back(self):
        assert _to_okx_bar("99X") == "1h"


# ---------------------------------------------------------------------------
# Ticker normalization
# ---------------------------------------------------------------------------

MOCK_CCXT_TICKER = {
    "symbol": "BTC/USDT:USDT",
    "timestamp": 1_700_000_000_000,
    "last": 42000.0,
    "bid": 41999.9,
    "ask": 42000.1,
    "high": 43000.0,
    "low": 41000.0,
    "baseVolume": 5000.0,
    "quoteVolume": 210_000_000.0,
    "info": {
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "last": "42000",
        "lastSz": "0.01",
        "askPx": "42000.1",
        "askSz": "1.5",
        "bidPx": "41999.9",
        "bidSz": "2.0",
        "open24h": "40000",
        "high24h": "43000",
        "low24h": "41000",
        "volCcy24h": "210000000",
        "vol24h": "5000",
        "ts": "1700000000000",
        "sodUtc0": "40500",
        "sodUtc8": "40800",
    },
}


def _make_adapter_with_mock_ccxt(ticker=MOCK_CCXT_TICKER) -> tuple[OKXCCXTAdapter, MagicMock]:
    """Return adapter with ccxt exchange mocked out."""
    adapter = OKXCCXTAdapter.__new__(OKXCCXTAdapter)
    adapter.api_key = ""
    adapter.secret_key = ""
    adapter.passphrase = ""
    adapter.simulated = False

    mock_ex = MagicMock()
    mock_ex.fetch_ticker.return_value = ticker
    mock_ex.fetch_tickers.return_value = {"BTC/USDT:USDT": ticker}
    adapter._ex = mock_ex

    mock_pub = MagicMock()
    adapter._pub = mock_pub

    return adapter, mock_ex


class TestGetTicker:
    def test_returns_okx_shaped_dict(self):
        adapter, _ = _make_adapter_with_mock_ccxt()
        result = adapter.get_ticker("BTC-USDT-SWAP")
        assert result is not None
        assert result["instId"] == "BTC-USDT-SWAP"
        assert result["last"] == "42000.0"
        assert result["askPx"] == "42000.1"
        assert result["bidPx"] == "41999.9"
        assert result["high24h"] == "43000.0"
        assert result["low24h"] == "41000.0"

    def test_calls_ccxt_with_correct_symbol(self):
        adapter, mock_ex = _make_adapter_with_mock_ccxt()
        adapter.get_ticker("ETH-USDT-SWAP")
        mock_ex.fetch_ticker.assert_called_once_with("ETH/USDT:USDT")

    def test_returns_none_on_ccxt_error(self):
        adapter, mock_ex = _make_adapter_with_mock_ccxt()
        mock_ex.fetch_ticker.side_effect = Exception("network error")
        result = adapter.get_ticker("BTC-USDT-SWAP")
        assert result is None


# ---------------------------------------------------------------------------
# Orderbook
# ---------------------------------------------------------------------------

MOCK_ORDERBOOK = {
    "symbol": "BTC/USDT:USDT",
    "timestamp": 1_700_000_000_000,
    "nonce": 99,
    "bids": [[42000.0, 1.5, 0], [41999.0, 2.0, 0]],
    "asks": [[42001.0, 1.2, 0], [42002.0, 3.0, 0]],
}


class TestGetOrderbook:
    def test_shape(self):
        adapter, mock_ex = _make_adapter_with_mock_ccxt()
        mock_ex.fetch_order_book.return_value = MOCK_ORDERBOOK
        ob = adapter.get_orderbook("BTC-USDT-SWAP", 5)
        assert "bids" in ob
        assert "asks" in ob
        assert ob["bids"][0][0] == "42000.0"
        assert ob["asks"][0][0] == "42001.0"

    def test_empty_on_error(self):
        adapter, mock_ex = _make_adapter_with_mock_ccxt()
        mock_ex.fetch_order_book.side_effect = Exception("timeout")
        ob = adapter.get_orderbook("BTC-USDT-SWAP")
        assert ob == {}


# ---------------------------------------------------------------------------
# Klines
# ---------------------------------------------------------------------------

MOCK_OHLCV = [
    [1_700_000_000_000, 42000.0, 43000.0, 41000.0, 42500.0, 100.0],
    [1_700_003_600_000, 42500.0, 44000.0, 42000.0, 43000.0, 150.0],
]


class TestGetKline:
    def test_returns_list_of_str_lists(self):
        adapter, mock_ex = _make_adapter_with_mock_ccxt()
        mock_ex.fetch_ohlcv.return_value = MOCK_OHLCV
        klines = adapter.get_kline("BTC-USDT-SWAP", "1H", 2)
        assert len(klines) == 2
        assert klines[0][0] == "1700000000000"
        assert klines[0][1] == "42000.0"

    def test_bar_mapping_applied(self):
        adapter, mock_ex = _make_adapter_with_mock_ccxt()
        mock_ex.fetch_ohlcv.return_value = []
        adapter.get_kline("BTC-USDT-SWAP", "1H", 10)
        mock_ex.fetch_ohlcv.assert_called_once_with("BTC/USDT:USDT", "1h", limit=10)


# ---------------------------------------------------------------------------
# PIT parquet writer
# ---------------------------------------------------------------------------

MOCK_FUNDING_ROWS = [
    {
        "formulaType": "withRate",
        "fundingRate": "0.0001",
        "fundingTime": "1700000000000",
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "method": "current_period",
        "realizedRate": "0.0001",
    },
    {
        "formulaType": "withRate",
        "fundingRate": "-0.00005",
        "fundingTime": "1699971200000",
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "method": "current_period",
        "realizedRate": "-0.00005",
    },
]

MOCK_OI_ROW = {
    "instId": "BTC-USDT-SWAP",
    "instType": "SWAP",
    "oi": "3487749.42",
    "oiCcy": "34877.49",
    "oiUsd": "2685106670.47",
    "ts": "1700000000000",
}

MOCK_MARK_ROW = {
    "instId": "BTC-USDT-SWAP",
    "instType": "SWAP",
    "markPx": "42000.5",
    "ts": "1700000000000",
}


def _mock_adapter_for_pit() -> OKXCCXTAdapter:
    adapter = OKXCCXTAdapter.__new__(OKXCCXTAdapter)
    adapter.api_key = ""
    adapter.secret_key = ""
    adapter.passphrase = ""
    adapter.simulated = False
    adapter._ex = MagicMock()

    mock_pub = MagicMock()
    mock_pub.funding_rate_history.return_value = {"code": "0", "data": MOCK_FUNDING_ROWS}
    mock_pub.get_open_interest.return_value = {"code": "0", "data": [MOCK_OI_ROW]}
    mock_pub.get_mark_price.return_value = {"code": "0", "data": [MOCK_MARK_ROW]}
    adapter._pub = mock_pub

    return adapter


class TestPITInvariants:
    def test_valid_df_passes(self):
        now = datetime.now(tz=timezone.utc)
        df = pl.DataFrame({
            "inst_id": ["BTC-USDT-SWAP"],
            "event_time": [now],
            "available_at": [now],
            "source_updated_at": [now],
        }).with_columns([
            pl.col("event_time").cast(pl.Datetime("us", "UTC")),
            pl.col("available_at").cast(pl.Datetime("us", "UTC")),
            pl.col("source_updated_at").cast(pl.Datetime("us", "UTC")),
        ])
        violations = validate_pit_invariants(df, "test")
        assert violations == []

    def test_missing_column_caught(self):
        df = pl.DataFrame({"event_time": [datetime.now(tz=timezone.utc)]}).with_columns(
            pl.col("event_time").cast(pl.Datetime("us", "UTC"))
        )
        violations = validate_pit_invariants(df, "test")
        assert any("available_at" in v for v in violations)

    def test_available_at_before_event_time_caught(self):
        t1 = datetime(2024, 1, 2, tzinfo=timezone.utc)
        t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)  # earlier
        df = pl.DataFrame({
            "inst_id": ["BTC-USDT-SWAP"],
            "event_time": [t1],
            "available_at": [t0],   # before event_time -> violation
            "source_updated_at": [t1],
        }).with_columns([
            pl.col("event_time").cast(pl.Datetime("us", "UTC")),
            pl.col("available_at").cast(pl.Datetime("us", "UTC")),
            pl.col("source_updated_at").cast(pl.Datetime("us", "UTC")),
        ])
        violations = validate_pit_invariants(df, "test")
        assert any("available_at < event_time" in v for v in violations)


class TestBackfillFundingRate:
    def test_writes_parquet_and_returns_df(self, tmp_path: Path):
        adapter = _mock_adapter_for_pit()
        df = backfill_funding_rate("BTC-USDT-SWAP", tmp_path, limit=2, adapter=adapter)
        assert len(df) == 2
        assert set(df.columns) >= {"inst_id", "event_time", "available_at", "source_updated_at",
                                    "funding_rate", "realized_rate"}
        parquets = list(tmp_path.glob("funding_rate_*.parquet"))
        assert len(parquets) == 1

    def test_schema_correct_dtypes(self, tmp_path: Path):
        adapter = _mock_adapter_for_pit()
        df = backfill_funding_rate("BTC-USDT-SWAP", tmp_path, limit=2, adapter=adapter)
        assert df["event_time"].dtype == pl.Datetime("us", "UTC")
        assert df["available_at"].dtype == pl.Datetime("us", "UTC")
        assert df["source_updated_at"].dtype == pl.Datetime("us", "UTC")

    def test_venue_and_market_type(self, tmp_path: Path):
        adapter = _mock_adapter_for_pit()
        df = backfill_funding_rate("BTC-USDT-SWAP", tmp_path, limit=2, adapter=adapter)
        assert df["venue"].to_list() == ["okx", "okx"]
        assert df["market_type"].to_list() == ["swap", "swap"]

    def test_sorted_by_event_time(self, tmp_path: Path):
        adapter = _mock_adapter_for_pit()
        df = backfill_funding_rate("BTC-USDT-SWAP", tmp_path, limit=2, adapter=adapter)
        times = df["event_time"].to_list()
        assert times == sorted(times)

    def test_available_at_equals_event_time_for_funding(self, tmp_path: Path):
        """Funding rate: realizedRate known at settlement so available_at == event_time."""
        adapter = _mock_adapter_for_pit()
        df = backfill_funding_rate("BTC-USDT-SWAP", tmp_path, limit=2, adapter=adapter)
        mismatches = df.filter(pl.col("available_at") != pl.col("event_time"))
        assert len(mismatches) == 0


class TestBackfillOpenInterest:
    def test_writes_parquet(self, tmp_path: Path):
        adapter = _mock_adapter_for_pit()
        df = backfill_open_interest("BTC-USDT-SWAP", tmp_path, adapter=adapter)
        assert len(df) == 1
        assert "oi_usd" in df.columns
        parquets = list(tmp_path.glob("open_interest_*.parquet"))
        assert len(parquets) == 1

    def test_available_at_gte_event_time(self, tmp_path: Path):
        """Snapshot data: available_at is ingest time which >= exchange ts."""
        adapter = _mock_adapter_for_pit()
        df = backfill_open_interest("BTC-USDT-SWAP", tmp_path, adapter=adapter)
        bad = df.filter(pl.col("available_at") < pl.col("event_time"))
        assert len(bad) == 0


class TestBackfillMarkPrice:
    def test_writes_parquet(self, tmp_path: Path):
        adapter = _mock_adapter_for_pit()
        df = backfill_mark_price("BTC-USDT-SWAP", tmp_path, adapter=adapter)
        assert len(df) == 1
        assert "mark_price" in df.columns
        assert df["mark_price"][0] == pytest.approx(42000.5)
        parquets = list(tmp_path.glob("mark_price_*.parquet"))
        assert len(parquets) == 1


# ---------------------------------------------------------------------------
# Gate token contract preserved
# ---------------------------------------------------------------------------

class TestGateTokenContract:
    def test_place_order_rejects_wrong_token(self):
        adapter = OKXCCXTAdapter.__new__(OKXCCXTAdapter)
        adapter.api_key = "key"
        adapter.secret_key = "secret"
        adapter.passphrase = "pass"
        adapter.simulated = False
        adapter._ex = MagicMock()
        adapter._pub = MagicMock()
        result = adapter.place_order(
            "BTC-USDT-SWAP", "buy", "0.01", price="42000",
            katana_gate_token="wrong_token",
        )
        assert result["code"] == "katana_gate_required"
        adapter._ex.create_order.assert_not_called()

    def test_place_order_rejects_missing_credentials(self):
        adapter = OKXCCXTAdapter.__new__(OKXCCXTAdapter)
        adapter.api_key = ""
        adapter.secret_key = ""
        adapter.passphrase = ""
        adapter.simulated = False
        adapter._ex = MagicMock()
        adapter._pub = MagicMock()
        result = adapter.place_order(
            "BTC-USDT-SWAP", "buy", "0.01", price="42000",
            katana_gate_token="OKXBridge.live_test",
        )
        assert "error" in result
        adapter._ex.create_order.assert_not_called()
