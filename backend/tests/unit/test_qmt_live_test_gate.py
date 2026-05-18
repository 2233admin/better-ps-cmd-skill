"""Unit tests for QMT/EasyXT live_test rejection gate (XAR-413).

Mirrors okx/bridge.py::_live_test_rejection contract.
"""

from __future__ import annotations

import pytest

from app.trading.adapters.qmt.bridge import QMTBridge


@pytest.fixture(autouse=True)
def _reset_daily_counter():
    """Ensure each test starts with a fresh in-process daily counter."""
    QMTBridge._daily_order_counts.clear()
    yield
    QMTBridge._daily_order_counts.clear()


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Strip any inherited live_test env vars so each test sets its own."""
    for var in (
        "KATANA_ENABLE_ASHARE_LIVE_TEST",
        "KATANA_ASHARE_LIVE_TEST_SYMBOLS",
        "KATANA_ASHARE_LIVE_TEST_MAX_NOTIONAL",
        "KATANA_ASHARE_LIVE_TEST_MAX_ORDERS_PER_DAY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_env_var_off_rejects_with_live_test_disabled(monkeypatch):
    # KATANA_ENABLE_ASHARE_LIVE_TEST unset -> reject
    assert QMTBridge._live_test_rejection("600519", 100.0, 100) == "live_test_disabled"

    monkeypatch.setenv("KATANA_ENABLE_ASHARE_LIVE_TEST", "0")
    assert QMTBridge._live_test_rejection("600519", 100.0, 100) == "live_test_disabled"


def test_symbol_not_in_allowlist_rejects(monkeypatch):
    monkeypatch.setenv("KATANA_ENABLE_ASHARE_LIVE_TEST", "1")
    # No allowlist set -> reject all
    assert (
        QMTBridge._live_test_rejection("600519", 100.0, 100)
        == "symbol_not_in_allowlist"
    )

    # Allowlist set but symbol not in it
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_SYMBOLS", "000001,000002")
    assert (
        QMTBridge._live_test_rejection("600519", 100.0, 100)
        == "symbol_not_in_allowlist"
    )


def test_passes_when_enabled_allowed_and_under_cap(monkeypatch):
    monkeypatch.setenv("KATANA_ENABLE_ASHARE_LIVE_TEST", "1")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_SYMBOLS", "600519,000001")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_MAX_NOTIONAL", "50000")
    # 100 * 100 = 10000 notional, well under 50000 cap
    assert QMTBridge._live_test_rejection("600519", 100.0, 100) is None


def test_notional_over_cap_rejects(monkeypatch):
    monkeypatch.setenv("KATANA_ENABLE_ASHARE_LIVE_TEST", "1")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_SYMBOLS", "600519")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_MAX_NOTIONAL", "50000")
    # 600 * 100 = 60000 > 50000
    assert (
        QMTBridge._live_test_rejection("600519", 600.0, 100)
        == "notional_exceeds_cap"
    )


def test_daily_order_cap_reached_on_21st_order(monkeypatch):
    monkeypatch.setenv("KATANA_ENABLE_ASHARE_LIVE_TEST", "1")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_SYMBOLS", "600519")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_MAX_NOTIONAL", "50000")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_MAX_ORDERS_PER_DAY", "20")

    # Simulate 20 accepted orders.
    for _ in range(20):
        assert QMTBridge._live_test_rejection("600519", 100.0, 100) is None
        QMTBridge._record_live_test_order()

    # 21st should be rejected
    assert (
        QMTBridge._live_test_rejection("600519", 100.0, 100)
        == "daily_order_cap_reached"
    )


def test_counter_does_not_reset_within_same_process(monkeypatch):
    """Documented limit: counter is in-process only. No reset between tests
    of the same process besides the autouse fixture (which simulates a fresh
    process). Verify the counter actually accumulates across calls."""
    monkeypatch.setenv("KATANA_ENABLE_ASHARE_LIVE_TEST", "1")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_SYMBOLS", "600519")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_MAX_NOTIONAL", "50000")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_MAX_ORDERS_PER_DAY", "3")

    for _ in range(3):
        assert QMTBridge._live_test_rejection("600519", 100.0, 100) is None
        QMTBridge._record_live_test_order()

    # 4th rejected -- counter persisted across separate calls in this process
    assert (
        QMTBridge._live_test_rejection("600519", 100.0, 100)
        == "daily_order_cap_reached"
    )

    # A new QMTBridge instance shares the class-level counter (same process)
    other = QMTBridge()
    assert (
        other._live_test_rejection("600519", 100.0, 100)
        == "daily_order_cap_reached"
    )


def test_submit_intent_returns_rejection_dict(monkeypatch):
    monkeypatch.setenv("KATANA_ENABLE_ASHARE_LIVE_TEST", "0")
    bridge = QMTBridge()
    # Bypass connect() -- rejection must fire before connection check.
    result = bridge.buy("600519", 100.0, 100)
    assert result.get("rejected") is True
    assert result.get("reason") == "live_test_disabled"
    assert result.get("error") == "live_test_disabled"


def test_symbol_allowlist_case_insensitive(monkeypatch):
    monkeypatch.setenv("KATANA_ENABLE_ASHARE_LIVE_TEST", "1")
    monkeypatch.setenv("KATANA_ASHARE_LIVE_TEST_SYMBOLS", "sh600519")
    # Allowlist normalized to upper -- symbol passed in any case should match
    assert QMTBridge._live_test_rejection("SH600519", 1.0, 1) is None
    assert QMTBridge._live_test_rejection("sh600519", 1.0, 1) is None
