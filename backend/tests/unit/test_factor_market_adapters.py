"""Unit tests for market adapters into the shared factor-core contract."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from app.research.factors.contracts import validate_core_panel_columns
from app.research.markets.ashare.adapter import to_factor_panel as ashare_to_factor_panel
from app.research.markets.crypto.adapter import to_factor_panel as crypto_to_factor_panel


def test_ashare_adapter_maps_symbol_and_amount_to_canonical_panel() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["600000.SH"],
            "event_time": [datetime(2026, 5, 19, tzinfo=UTC)],
            "available_at": [datetime(2026, 5, 20, tzinfo=UTC)],
            "close": [10.0],
            "amount": [12345.0],
        }
    )

    out = ashare_to_factor_panel(frame)

    assert "asset_id" in out.columns
    assert "notional" in out.columns
    assert out["asset_id"].to_list() == ["600000.SH"]
    assert out["market"].to_list() == ["ashare"]
    assert validate_core_panel_columns(out.columns).passed


def test_crypto_adapter_maps_inst_id_and_quote_volume_to_canonical_panel() -> None:
    frame = pl.DataFrame(
        {
            "inst_id": ["BTC-USDT-SWAP"],
            "event_time": [datetime(2026, 5, 19, tzinfo=UTC)],
            "available_at": [datetime(2026, 5, 19, tzinfo=UTC)],
            "close": [105000.0],
            "quote_volume": [98765.0],
        }
    )

    out = crypto_to_factor_panel(frame)

    assert "asset_id" in out.columns
    assert "notional" in out.columns
    assert out["asset_id"].to_list() == ["BTC-USDT-SWAP"]
    assert out["market"].to_list() == ["crypto"]
    assert validate_core_panel_columns(out.columns).passed
