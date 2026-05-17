"""Contract tests for product and execution boundaries."""

from pathlib import Path

import pytest


def test_product_boundary_doc_declares_trading_modes():
    doc = Path(__file__).resolve().parents[3] / "docs" / "PRODUCT_BOUNDARY.md"
    text = doc.read_text(encoding="utf-8")

    assert "KATANA_TRADING_MODE" in text
    assert "research" in text
    assert "paper" in text
    assert "live_test" in text
    assert "KATANA_ENABLE_CRYPTO_LIVE_TEST" in text
    assert "KATANA_ASHARE_EXECUTION" in text


def test_old_import_wrappers_still_work():
    import app.api.strategy_api as strategy_api
    import app.data.okx_client as okx_client
    import app.trade.executor as old_executor

    from app.trading.executor import OrderExecutor

    assert strategy_api.router is not None
    assert hasattr(okx_client, "get_okx_client")
    assert old_executor.OrderExecutor is OrderExecutor


def test_crypto_live_test_rejects_without_explicit_env(monkeypatch):
    from app.trading.adapters.okx import OKXBridge

    calls = []

    class FakeClient:
        def place_order(self, **kwargs):
            calls.append(kwargs)
            return {"ordId": "should-not-happen"}

        def get_positions(self):
            return []

        def get_balance(self):
            return []

        def cancel_order(self, inst_id, order_id):
            return {"instId": inst_id, "ordId": order_id}

    monkeypatch.delenv("KATANA_TRADING_MODE", raising=False)
    monkeypatch.delenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", raising=False)
    monkeypatch.delenv("KATANA_OKX_MAX_ORDER_USDT", raising=False)
    monkeypatch.delenv("KATANA_OKX_ALLOWED_PAIRS", raising=False)

    bridge = OKXBridge()
    bridge.client = FakeClient()

    result = bridge.buy("BTC-USDT", price=100.0, volume=1)

    assert "error" in result
    assert calls == []


def test_crypto_live_test_allows_only_configured_small_orders(monkeypatch):
    from app.trading.adapters.okx import OKXBridge

    calls = []

    class FakeClient:
        def place_order(self, **kwargs):
            calls.append(kwargs)
            return {"ordId": "ok-1"}

    monkeypatch.setenv("KATANA_TRADING_MODE", "live_test")
    monkeypatch.setenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", "1")
    monkeypatch.setenv("KATANA_OKX_MAX_ORDER_USDT", "150")
    monkeypatch.setenv("KATANA_OKX_ALLOWED_PAIRS", "BTC-USDT")

    bridge = OKXBridge()
    bridge.client = FakeClient()

    result = bridge.buy("BTC-USDT", price=100.0, volume=1)

    assert result["order_id"] == "ok-1"
    assert calls and calls[0]["inst_id"] == "BTC-USDT"


def test_okx_client_without_env_keys_cannot_post(monkeypatch):
    from app.markets.crypto.okx_client import OKXClient

    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_SECRET_KEY", raising=False)
    monkeypatch.delenv("OKX_PASSPHRASE", raising=False)

    client = OKXClient()
    result = client.place_order(
        inst_id="BTC-USDT",
        side="buy",
        size="1",
        price="100",
    )

    assert result["code"] == "-1"
    assert "credentials missing" in result["error"]


def test_okx_client_direct_order_requires_bridge_gate_even_with_keys(monkeypatch):
    from app.markets.crypto.okx_client import OKXClient

    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_SECRET_KEY", "secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "pass")

    result = OKXClient().place_order(
        inst_id="BTC-USDT",
        side="buy",
        size="1",
        price="100",
    )

    assert result["code"] == "katana_gate_required"
    assert "OKXBridge live_test gate" in result["error"]

    raw_result = OKXClient()._post("/api/v5/trade/order", {"instId": "BTC-USDT"})
    assert raw_result["code"] == "katana_gate_required"


@pytest.mark.anyio
async def test_okx_direct_order_api_is_disabled_without_gate():
    from fastapi import HTTPException

    from app.api.crypto.okx_api import TradeRequest, execute_trade

    with pytest.raises(HTTPException) as exc_info:
        await execute_trade(
            TradeRequest(
                pair="BTC-USDT",
                side="buy",
                posSide="long",
                size="1",
                price="100",
            )
        )

    assert exc_info.value.status_code == 403
    assert "Direct OKX order routes are disabled" in str(exc_info.value.detail)


def test_qmt_boundary_adapter_does_not_import_xtquant():
    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "trading"
        / "adapters"
        / "qmt"
        / "bridge.py"
    ).read_text(encoding="utf-8")

    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert all("xtquant" not in line for line in import_lines)


def test_paper_order_requires_paper_mode(monkeypatch):
    from app.trading.executor import OrderExecutor

    monkeypatch.delenv("KATANA_TRADING_MODE", raising=False)
    executor = OrderExecutor(mode="paper")
    with pytest.raises(RuntimeError, match="KATANA_TRADING_MODE=research"):
        executor.submit_order(
            code="BTC-USDT",
            direction="buy",
            price=100.0,
            volume=1,
            strategy="paper-test",
        )


def test_paper_order_does_not_initialize_real_adapter(monkeypatch):
    from app.trading.executor import OrderExecutor

    monkeypatch.setenv("KATANA_TRADING_MODE", "paper")
    executor = OrderExecutor(mode="paper")
    executor.risk_manager.force_close_time = "23:59"
    result = executor.submit_order(
        code="BTC-USDT",
        direction="buy",
        price=100.0,
        volume=1,
        strategy="paper-test",
    )

    assert result["status"] == "filled"
    assert executor._bridge is None
