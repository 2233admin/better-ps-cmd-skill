"""OKX adapter gated for explicit crypto live_test execution."""

from __future__ import annotations

import os

from loguru import logger

from ....data.okx_client import OKXClient, get_okx_client
from ...intent import TradingMode, get_trading_mode


class OKXBridge:
    """OKX trading bridge with live-test safety checks."""

    def __init__(self, trade_mode: str = "spot"):
        self.client: OKXClient = get_okx_client()
        self.trade_mode = trade_mode
        self.connected = False

    def connect(self) -> bool:
        try:
            r = self.client._get("/api/v5/account/balance", auth=True)
            if r.get("code") == "0":
                self.connected = True
                logger.info(f"OKX bridge connected (mode={self.trade_mode})")
                return True
            logger.error(f"OKX bridge auth failed: {r.get('msg')}")
            return False
        except Exception as e:
            logger.error(f"OKX bridge connect error: {e}")
            return False

    def _base_pair(self, code: str) -> str:
        if code.endswith("-SWAP"):
            return code.removesuffix("-SWAP")
        parts = code.split("-")
        if len(parts) >= 2:
            return f"{parts[0].upper()}-{parts[1].upper()}"
        return f"{code.upper()}-USDT"

    def _inst_id(self, code: str) -> str:
        if "-" in code:
            if self.trade_mode == "swap" and not code.endswith("-SWAP"):
                return code + "-SWAP"
            return code
        suffix = "-USDT-SWAP" if self.trade_mode == "swap" else "-USDT"
        return code.upper() + suffix

    def _td_mode(self) -> str:
        return "cross" if self.trade_mode == "swap" else "cash"

    def _live_test_rejection(self, code: str, price: float, volume: int) -> str | None:
        if get_trading_mode() != TradingMode.LIVE_TEST:
            return "OKX live_test rejected: KATANA_TRADING_MODE must be live_test"
        if os.environ.get("KATANA_ENABLE_CRYPTO_LIVE_TEST") != "1":
            return "OKX live_test rejected: KATANA_ENABLE_CRYPTO_LIVE_TEST must be 1"

        allowed = {
            item.strip().upper()
            for item in os.environ.get("KATANA_OKX_ALLOWED_PAIRS", "").split(",")
            if item.strip()
        }
        pair = self._base_pair(code)
        if not allowed or pair.upper() not in allowed:
            return f"OKX live_test rejected: {pair} is not in KATANA_OKX_ALLOWED_PAIRS"

        try:
            max_order_usdt = float(os.environ["KATANA_OKX_MAX_ORDER_USDT"])
        except (KeyError, ValueError):
            return "OKX live_test rejected: KATANA_OKX_MAX_ORDER_USDT must be numeric"

        notional = price * volume
        if notional > max_order_usdt:
            return (
                "OKX live_test rejected: order notional "
                f"{notional:.8g} exceeds {max_order_usdt:.8g} USDT"
            )
        return None

    def buy(self, code: str, price: float, volume: int) -> dict:
        rejection = self._live_test_rejection(code, price, volume)
        if rejection:
            return {"error": rejection}
        return self._place_limit(code, "buy", price, volume)

    def sell(self, code: str, price: float, volume: int) -> dict:
        rejection = self._live_test_rejection(code, price, volume)
        if rejection:
            return {"error": rejection}
        return self._place_limit(code, "sell", price, volume)

    def _place_limit(self, code: str, side: str, price: float, volume: int) -> dict:
        inst_id = self._inst_id(code)
        result = self.client.place_order(
            inst_id=inst_id,
            side=side,
            size=str(volume),
            price=f"{price:.8g}",
            ord_type="limit",
            td_mode=self._td_mode(),
        )
        if "error" in result:
            return {"error": result["error"]}
        return {"order_id": result.get("ordId", ""), "inst_id": inst_id, "side": side}

    def market_buy(self, code: str, size: str) -> dict:
        return {"error": "OKX live_test rejected: market orders are disabled"}

    def market_sell(self, code: str, size: str) -> dict:
        return {"error": "OKX live_test rejected: market orders are disabled"}

    def get_positions(self) -> list[dict]:
        return self.client.get_positions()

    def get_balance(self) -> list[dict]:
        return self.client.get_balance()

    def cancel(self, code: str, order_id: str) -> dict:
        return self.client.cancel_order(self._inst_id(code), order_id)
