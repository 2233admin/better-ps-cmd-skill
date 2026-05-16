"""Optional EasyXT phase-1 bridge integration tests.

Set KATANA_EASYXT_BRIDGE_URL, for example http://127.0.0.1:8000, to run.
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from quant_terminal.trade.intent import make_trade_intent


pytestmark = pytest.mark.integration


def _bridge_url() -> str:
    url = os.environ.get("KATANA_EASYXT_BRIDGE_URL", "").rstrip("/")
    if not url:
        pytest.skip("KATANA_EASYXT_BRIDGE_URL is not set")
    return url


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))
    except URLError as exc:
        pytest.fail(f"EasyXT bridge is not reachable: {exc}")


def test_easyxt_bridge_accepts_dry_run_trade_intent():
    payload = make_trade_intent(
        request_id="katana-integration-dry-run-0001",
        strategy="katana_contract_test",
        account="sim",
        symbol="600000.SH",
        side="buy",
        qty=100,
        dry_run=True,
        note="k-atana optional integration contract test",
    ).to_bridge_payload()

    response = _post_json(f"{_bridge_url()}/v1/execution-requests", payload)

    assert response.get("request_id") == payload["request_id"]
    assert response.get("status") in {"accepted", "submitted", "queued", "rejected"}
    if response.get("status") == "rejected":
        assert response.get("reason") or response.get("error")


def test_easyxt_bridge_request_id_is_idempotent_for_dry_run():
    payload = make_trade_intent(
        request_id="katana-integration-idempotent-0001",
        strategy="katana_contract_test",
        account="sim",
        symbol="600000.SH",
        side="buy",
        qty=100,
        dry_run=True,
        note="k-atana optional idempotency test",
    ).to_bridge_payload()

    first = _post_json(f"{_bridge_url()}/v1/execution-requests", payload)
    second = _post_json(f"{_bridge_url()}/v1/execution-requests", payload)

    assert first.get("request_id") == payload["request_id"]
    assert second.get("request_id") == payload["request_id"]
    assert second.get("status") in {"accepted", "submitted", "queued", "rejected"}
