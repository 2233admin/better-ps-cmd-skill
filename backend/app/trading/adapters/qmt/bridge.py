"""A-share execution boundary for EasyXT/QMT.

This module intentionally avoids broker SDK imports. k-atana emits execution
intents; the external EasyXT bridge owns broker SDK integration.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from loguru import logger


class QMTBridge:
    """Boundary adapter that posts broker-neutral intents to EasyXT."""

    # In-process daily order counter shared across instances. Keyed by UTC
    # date string. NOTE: this is intentionally NOT persisted -- if the
    # process restarts the counter resets. Cross-process enforcement would
    # require redis/sqlite (see XAR-413 follow-up).
    _daily_order_counts: dict[str, int] = {}

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

    @classmethod
    def _utc_today_key(cls) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    @classmethod
    def _live_test_rejection(
        cls, code: str, price: float, volume: float
    ) -> str | None:
        """Mirror of OKX `_live_test_rejection` for the A-share/EasyXT path.

        Returns a string reason if the intent must be rejected, else None.
        Contract intentionally identical to okx.bridge.OKXBridge for parity.
        """
        if os.environ.get("KATANA_ENABLE_ASHARE_LIVE_TEST") != "1":
            return "live_test_disabled"

        allowed = {
            item.strip().upper()
            for item in os.environ.get(
                "KATANA_ASHARE_LIVE_TEST_SYMBOLS", ""
            ).split(",")
            if item.strip()
        }
        if not allowed or code.upper() not in allowed:
            return "symbol_not_in_allowlist"

        try:
            max_notional = float(
                os.environ.get("KATANA_ASHARE_LIVE_TEST_MAX_NOTIONAL", "50000")
            )
        except ValueError:
            return "notional_exceeds_cap"
        notional = float(price) * float(volume)
        if notional > max_notional:
            return "notional_exceeds_cap"

        try:
            max_orders = int(
                os.environ.get(
                    "KATANA_ASHARE_LIVE_TEST_MAX_ORDERS_PER_DAY", "20"
                )
            )
        except ValueError:
            return "daily_order_cap_reached"
        today = cls._utc_today_key()
        if cls._daily_order_counts.get(today, 0) >= max_orders:
            return "daily_order_cap_reached"

        return None

    @classmethod
    def _record_live_test_order(cls) -> None:
        today = cls._utc_today_key()
        cls._daily_order_counts[today] = cls._daily_order_counts.get(today, 0) + 1

    def _submit_intent(self, code: str, side: str, price: float, volume: int) -> dict:
        rejection = self._live_test_rejection(code, price, volume)
        if rejection:
            return {"error": rejection, "rejected": True, "reason": rejection}

        if not self.connected:
            return {"error": "EasyXT bridge not connected"}

        self._record_live_test_order()
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
