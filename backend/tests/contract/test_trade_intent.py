"""Contract tests for k-atana -> EasyXT phase-1 bridge payloads."""

import pytest

from quant_terminal.trade.intent import TradeIntent, make_trade_intent


def test_trade_intent_bridge_payload_shape():
    intent = make_trade_intent(
        request_id="ma-rotation-0001",
        strategy="ma_rotation",
        account="sim",
        symbol="600000.SH",
        side="buy",
        qty=100,
        dry_run=True,
        note="contract test",
    )

    assert intent.to_bridge_payload() == {
        "request_id": "ma-rotation-0001",
        "strategy": "ma_rotation",
        "account": "sim",
        "symbol": "600000.SH",
        "side": "buy",
        "qty": 100,
        "dry_run": True,
        "note": "contract test",
    }


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("request_id", "", "request_id is required"),
        ("strategy", "", "strategy is required"),
        ("account", "", "account is required"),
        ("symbol", "", "symbol is required"),
        ("side", "hold", "side must be one of"),
        ("qty", 0, "qty must be positive"),
    ],
)
def test_trade_intent_rejects_invalid_payloads(field, value, error):
    payload = {
        "request_id": "req-1",
        "strategy": "ma_rotation",
        "account": "sim",
        "symbol": "600000.SH",
        "side": "buy",
        "qty": 100,
        "dry_run": True,
        "note": None,
    }
    payload[field] = value

    with pytest.raises(ValueError, match=error):
        TradeIntent(**payload).to_bridge_payload()


def test_trade_intent_has_no_broker_order_fields():
    payload = make_trade_intent(
        strategy="ma_rotation",
        account="sim",
        symbol="600000.SH",
        side="sell",
        qty=100,
    ).to_bridge_payload()

    assert "order_type" not in payload
    assert "price_type" not in payload
    assert "xtconstant" not in payload
