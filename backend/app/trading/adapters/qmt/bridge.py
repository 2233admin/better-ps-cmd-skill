"""A-share execution boundary for EasyXT/QMT.

This module intentionally avoids broker SDK imports. k-atana emits execution
intents; the external EasyXT bridge owns broker SDK integration.
"""

from __future__ import annotations

import json
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from loguru import logger


class QMTBridge:
    """Boundary adapter that posts broker-neutral intents to EasyXT."""

    def __init__(self):
        self.connected = False
        self.account_id = ""
        self.bridge_url = ""

    def connect(self, path: str = "", account: str = "") -> bool:
        self.account_id = account or os.environ.get("KATANA_EASYXT_ACCOUNT", "sim")
        mode = os.environ.get("KATANA_ASHARE_EXECUTION", "manual").strip().lower()
        if mode != "easyxt_bridge":
            logger.warning("A-share execution is manual; EasyXT bridge disabled")
            return False
        self.bridge_url = os.environ.get("KATANA_EASYXT_BRIDGE_URL", "").rstrip("/")
        if not self.bridge_url:
            logger.warning("KATANA_EASYXT_BRIDGE_URL is required for easyxt_bridge")
            return False
        self.connected = True
        return True

    def buy(self, code: str, price: float, volume: int) -> dict:
        return self._submit_intent(code, "buy", price, volume)

    def sell(self, code: str, price: float, volume: int) -> dict:
        return self._submit_intent(code, "sell", price, volume)

    def _submit_intent(self, code: str, side: str, price: float, volume: int) -> dict:
        if not self.connected:
            return {"error": "EasyXT bridge not connected"}

        payload = {
            "request_id": f"katana-{uuid.uuid4().hex}",
            "strategy": "katana_boundary",
            "account": self.account_id,
            "symbol": code,
            "side": side,
            "qty": volume,
            "dry_run": True,
            "note": f"limit_price={price}",
        }
        request = Request(
            f"{self.bridge_url}/v1/execution-requests",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return json.loads(exc.read().decode("utf-8"))
        except (URLError, TimeoutError) as exc:
            logger.error(f"EasyXT bridge request failed: {exc}")
            return {"error": str(exc)}

    def cancel(self, order_id: int) -> bool:
        return False

    def query_positions(self) -> list[dict]:
        return []

    def query_orders(self) -> list[dict]:
        return []

    def disconnect(self):
        self.connected = False
