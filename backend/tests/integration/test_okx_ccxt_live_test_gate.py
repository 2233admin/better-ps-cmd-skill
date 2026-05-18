"""Integration tests: OKXBridge live_test gate works with ccxt adapter.

Network-free — mocks place_order on the adapter level.
Verifies that:
  1. _live_test_rejection() still gates correctly when the ccxt adapter is active
  2. wheel_admission policy still trips for non-admitted symbols
  3. OKXBridge.connect() calls check_auth() (not _get) on the ccxt adapter

XAR-426: rollout verification.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.markets.crypto.okx_ccxt_adapter import OKXCCXTAdapter
from app.trading.adapters.okx.bridge import OKXBridge
from app.trading.adapters.crypto.wheel_admission import load_wheel_admission_policy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bridge_with_ccxt_adapter(monkeypatch) -> tuple[OKXBridge, OKXCCXTAdapter]:
    """Return OKXBridge wired to a mock OKXCCXTAdapter (ccxt path)."""
    # Ensure ccxt adapter mode
    monkeypatch.setenv("KATANA_OKX_ADAPTER", "ccxt")

    adapter = OKXCCXTAdapter.__new__(OKXCCXTAdapter)
    adapter.api_key = "key"
    adapter.secret_key = "secret"
    adapter.passphrase = "pass"
    adapter.simulated = False
    adapter._ex = MagicMock()
    adapter._pub = MagicMock()

    bridge = OKXBridge.__new__(OKXBridge)
    bridge.client = adapter
    bridge.trade_mode = "swap"
    bridge.connected = False
    return bridge, adapter


# ---------------------------------------------------------------------------
# Test 1: _live_test_rejection gate still fires with ccxt adapter
# ---------------------------------------------------------------------------

class TestLiveTestRejectionWithCCXTAdapter:

    def test_rejects_without_live_test_mode(self, monkeypatch):
        bridge, _ = _make_bridge_with_ccxt_adapter(monkeypatch)
        monkeypatch.setenv("KATANA_TRADING_MODE", "paper")
        monkeypatch.setenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", "1")
        monkeypatch.setenv("KATANA_OKX_ALLOWED_PAIRS", "BTC-USDT")
        monkeypatch.setenv("KATANA_OKX_MAX_ORDER_USDT", "500")

        result = bridge.buy("BTC-USDT-SWAP", 50000.0, 1)
        assert "error" in result
        assert "live_test" in result["error"]
        # adapter's ccxt create_order must NOT have been called
        bridge.client._ex.create_order.assert_not_called()

    def test_rejects_without_enable_flag(self, monkeypatch):
        bridge, _ = _make_bridge_with_ccxt_adapter(monkeypatch)
        monkeypatch.setenv("KATANA_TRADING_MODE", "live_test")
        monkeypatch.delenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", raising=False)
        monkeypatch.setenv("KATANA_OKX_ALLOWED_PAIRS", "BTC-USDT")
        monkeypatch.setenv("KATANA_OKX_MAX_ORDER_USDT", "500")

        result = bridge.buy("BTC-USDT-SWAP", 50000.0, 1)
        assert "error" in result
        assert "KATANA_ENABLE_CRYPTO_LIVE_TEST" in result["error"]
        bridge.client._ex.create_order.assert_not_called()

    def test_rejects_pair_not_in_allowed(self, monkeypatch):
        bridge, _ = _make_bridge_with_ccxt_adapter(monkeypatch)
        monkeypatch.setenv("KATANA_TRADING_MODE", "live_test")
        monkeypatch.setenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", "1")
        monkeypatch.setenv("KATANA_OKX_ALLOWED_PAIRS", "ETH-USDT")
        monkeypatch.setenv("KATANA_OKX_MAX_ORDER_USDT", "500")

        result = bridge.buy("BTC-USDT-SWAP", 50000.0, 1)
        assert "error" in result
        assert "BTC-USDT" in result["error"]
        bridge.client._ex.create_order.assert_not_called()

    def test_rejects_notional_over_max(self, monkeypatch):
        bridge, _ = _make_bridge_with_ccxt_adapter(monkeypatch)
        monkeypatch.setenv("KATANA_TRADING_MODE", "live_test")
        monkeypatch.setenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", "1")
        monkeypatch.setenv("KATANA_OKX_ALLOWED_PAIRS", "BTC-USDT")
        monkeypatch.setenv("KATANA_OKX_MAX_ORDER_USDT", "100")

        # 50000 * 1 = 50000 USDT > 100
        result = bridge.buy("BTC-USDT-SWAP", 50000.0, 1)
        assert "error" in result
        assert "notional" in result["error"] or "exceeds" in result["error"]
        bridge.client._ex.create_order.assert_not_called()

    def test_passes_gate_and_calls_place_order(self, monkeypatch):
        bridge, adapter = _make_bridge_with_ccxt_adapter(monkeypatch)
        monkeypatch.setenv("KATANA_TRADING_MODE", "live_test")
        monkeypatch.setenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", "1")
        monkeypatch.setenv("KATANA_OKX_ALLOWED_PAIRS", "BTC-USDT")
        monkeypatch.setenv("KATANA_OKX_MAX_ORDER_USDT", "10000")

        # Mock successful order response
        adapter._ex.create_order.return_value = {
            "id": "mock-order-123",
            "info": {"data": [{"ordId": "mock-order-123", "clOrdId": "", "sCode": "0"}]},
        }

        result = bridge.buy("BTC-USDT-SWAP", 50.0, 1)
        assert "error" not in result
        assert result["order_id"] == "mock-order-123"
        adapter._ex.create_order.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: wheel_admission policy still enforces for non-admitted symbols
# ---------------------------------------------------------------------------

class TestWheelAdmission:

    def test_admitted_packages_have_allow_decision(self):
        policy = load_wheel_admission_policy()
        # ccxt and python-okx promoted to allow in XAR-426 rollout
        for pkg in ("ccxt", "python-okx"):
            decision = policy.decision_for(pkg)
            assert decision == "allow", (
                f"{pkg} should be admitted with decision=allow (got {decision!r}). "
                "Update CRYPTO_WHEEL_ADMISSION.json if the spike has been validated."
            )

    def test_non_admitted_package_raises(self):
        policy = load_wheel_admission_policy()
        with pytest.raises(ValueError, match="runtime imports require allow"):
            policy.assert_allowed_for_runtime("not-a-real-wheel-xyz")

    def test_default_decision_is_reject(self):
        policy = load_wheel_admission_policy()
        assert policy.default_decision == "reject"


# ---------------------------------------------------------------------------
# Test 3: OKXBridge.connect() uses check_auth() on ccxt adapter
# ---------------------------------------------------------------------------

class TestBridgeConnectUsesCheckAuth:

    def test_connect_calls_check_auth_on_ccxt_adapter(self, monkeypatch):
        bridge, adapter = _make_bridge_with_ccxt_adapter(monkeypatch)

        # Mock check_auth to return success
        adapter.check_auth = MagicMock(return_value={"code": "0", "data": []})

        result = bridge.connect()
        assert result is True
        assert bridge.connected is True
        adapter.check_auth.assert_called_once()
        # _get should NOT be called (ccxt adapter doesn't have it)
        assert not hasattr(adapter, "_get") or not adapter._get.called

    def test_connect_returns_false_on_auth_failure(self, monkeypatch):
        bridge, adapter = _make_bridge_with_ccxt_adapter(monkeypatch)
        adapter.check_auth = MagicMock(return_value={"code": "-1", "msg": "auth failed", "data": []})

        result = bridge.connect()
        assert result is False
        assert bridge.connected is False

    def test_connect_legacy_adapter_uses_get(self, monkeypatch):
        """Verify legacy fallback path: bridge._get still works for OKXClient."""
        monkeypatch.setenv("KATANA_OKX_ADAPTER", "legacy")

        from app.markets.crypto.okx_client import OKXClient
        legacy_client = OKXClient.__new__(OKXClient)
        # Credentials required so _get doesn't short-circuit with missing-creds response
        legacy_client.api_key = "key"
        legacy_client.secret_key = "secret"
        legacy_client.passphrase = "pass"
        legacy_client.simulated = False
        legacy_client.session = MagicMock()
        legacy_client.session.timeout = 10
        legacy_client.session.get.return_value = MagicMock(
            json=lambda: {"code": "0", "data": []}
        )

        bridge = OKXBridge.__new__(OKXBridge)
        bridge.client = legacy_client
        bridge.trade_mode = "spot"
        bridge.connected = False

        result = bridge.connect()
        assert result is True
        assert bridge.connected is True
        # Confirm _get was used (legacy path, no check_auth on OKXClient)
        legacy_client.session.get.assert_called_once()
