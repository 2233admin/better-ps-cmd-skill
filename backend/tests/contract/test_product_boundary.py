"""Contract tests for product and execution boundaries."""

from pathlib import Path
import tomllib

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
    assert "CRYPTO_WHEEL_ADMISSION.json" in text


def test_crypto_wheel_admission_doc_declares_default_reject():
    doc = Path(__file__).resolve().parents[3] / "docs" / "CRYPTO_WHEEL_ADMISSION.md"
    text = doc.read_text(encoding="utf-8")

    assert "Unknown crypto wheels are rejected" in text
    assert "external wheel -> adapter/normalizer -> crypto PIT schema" in text
    assert "approved intent -> paper/sim reconciliation -> OKXBridge.live_test" in text


def test_crypto_runtime_dependencies_have_wheel_admission_records():
    from app.trading.adapters.crypto import load_wheel_admission_policy

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dependencies = payload["project"]["dependencies"]
    names = {_dependency_name(item) for item in dependencies}
    crypto_runtime = {
        name
        for name in names
        if name in {"requests", "httpx", "websockets", "ccxt", "okx", "python-binance", "binance-connector"}
        or any(token in name for token in ("crypto", "exchange", "binance", "freqtrade", "hummingbot"))
    }
    policy = load_wheel_admission_policy()

    assert crypto_runtime
    assert crypto_runtime.issubset(set(policy.admissions))


def test_old_import_wrappers_still_work():
    import app.api.strategy_api as strategy_api
    import app.data.okx_client as okx_client
    import app.trade.executor as old_executor

    from app.trading.executor import OrderExecutor

    assert strategy_api.router is not None
    assert hasattr(okx_client, "get_okx_client")
    assert old_executor.OrderExecutor is OrderExecutor


def _dependency_name(raw: str) -> str:
    for separator in ("[", "<", ">", "=", "~", "!", ";"):
        raw = raw.split(separator, 1)[0]
    return raw.strip().lower()


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


def test_okx_submit_intent_dry_run_does_not_call_exchange(monkeypatch):
    from app.trading.adapters.crypto import CryptoOrderIntent
    from app.trading.adapters.okx import OKXBridge

    calls = []

    class FakeClient:
        def place_order(self, **kwargs):
            calls.append(kwargs)
            return {"ordId": "should-not-happen"}

    monkeypatch.delenv("KATANA_TRADING_MODE", raising=False)
    bridge = OKXBridge()
    bridge.client = FakeClient()

    result = bridge.submit_intent(
        CryptoOrderIntent(
            request_id="intent-1",
            inst_id="BTC-USDT",
            side="buy",
            qty=1,
            limit_price=100.0,
            dry_run=True,
        )
    )

    assert result.status == "accepted"
    assert result.reason == "dry_run"
    assert calls == []


def test_okx_submit_intent_live_test_uses_existing_gate(monkeypatch):
    from app.trading.adapters.crypto import CryptoOrderIntent
    from app.trading.adapters.okx import OKXBridge

    calls = []

    class FakeClient:
        def place_order(self, **kwargs):
            calls.append(kwargs)
            return {"ordId": "ok-2"}

    monkeypatch.setenv("KATANA_TRADING_MODE", "live_test")
    monkeypatch.setenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", "1")
    monkeypatch.setenv("KATANA_OKX_MAX_ORDER_USDT", "150")
    monkeypatch.setenv("KATANA_OKX_ALLOWED_PAIRS", "BTC-USDT")
    bridge = OKXBridge()
    bridge.client = FakeClient()

    result = bridge.submit_intent(
        CryptoOrderIntent(
            request_id="intent-2",
            inst_id="BTC-USDT",
            side="buy",
            qty=1,
            limit_price=100.0,
            dry_run=False,
        )
    )

    assert result.status == "submitted"
    assert result.order_id == "ok-2"
    assert calls and calls[0]["katana_gate_token"] == "OKXBridge.live_test"


def test_okx_submit_intent_live_test_checks_fractional_crypto_notional(monkeypatch):
    from app.trading.adapters.crypto import CryptoOrderIntent
    from app.trading.adapters.okx import OKXBridge

    calls = []

    class FakeClient:
        def place_order(self, **kwargs):
            calls.append(kwargs)
            return {"ordId": "should-not-happen"}

    monkeypatch.setenv("KATANA_TRADING_MODE", "live_test")
    monkeypatch.setenv("KATANA_ENABLE_CRYPTO_LIVE_TEST", "1")
    monkeypatch.setenv("KATANA_OKX_MAX_ORDER_USDT", "150")
    monkeypatch.setenv("KATANA_OKX_ALLOWED_PAIRS", "BTC-USDT")
    bridge = OKXBridge()
    bridge.client = FakeClient()

    result = bridge.submit_intent(
        CryptoOrderIntent(
            request_id="intent-3",
            inst_id="BTC-USDT",
            side="buy",
            qty=0.01,
            limit_price=20_000.0,
            dry_run=False,
        )
    )

    assert result.status == "rejected"
    assert "exceeds" in result.reason
    assert calls == []


def test_crypto_sim_reconciliation_explains_fills_and_rejections():
    from datetime import UTC, datetime

    import polars as pl

    from app.trading.adapters.crypto import CryptoOrderIntent, replay_crypto_intents

    pit = pl.DataFrame(
        {
            "inst_id": ["BTC-USDT", "ETH-USDT"],
            "event_time": [
                datetime(2026, 5, 17, 12, tzinfo=UTC),
                datetime(2026, 5, 17, 12, tzinfo=UTC),
            ],
            "close": [100.0, 50.0],
        }
    )

    report = replay_crypto_intents(
        (
            CryptoOrderIntent("ok-1", "BTC-USDT", "buy", 1.0, 100.0, dry_run=True),
            CryptoOrderIntent("bad-1", "ETH-USDT", "buy", 100.0, 50.0, dry_run=True),
            CryptoOrderIntent("bad-2", "SOL-USDT", "buy", 1.0, 10.0, dry_run=True),
        ),
        pit,
        allowed_pairs={"BTC-USDT", "ETH-USDT", "SOL-USDT"},
        max_notional=150.0,
    )

    assert report.accepted == 1
    assert report.fill_count == 1
    assert report.rejected == 2
    assert report.cash_diff < 0
    assert report.position_diff["BTC-USDT"] == 1.0
    assert "bad-1:notional_exceeds_limit" in report.reject_reasons
    assert "bad-2:missing_price" in report.reject_reasons


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


def test_crypto_research_pipeline_has_no_exchange_or_trading_imports():
    package_dir = Path(__file__).resolve().parents[2] / "app" / "research" / "crypto_pipeline"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.py"))

    assert "app.trading" not in source
    assert "app.trade" not in source
    assert "app.markets.crypto" not in source
    assert "ccxt" not in source
    assert "freqtrade" not in source
    assert "hummingbot" not in source


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
