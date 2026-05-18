"""OKX ccxt + python-okx adapter — drop-in replacement candidate for okx_client.py.

Spike: XAR-423
Strategy:
  - ccxt.okx() for portable market data (ticker / orderbook / klines / balance / orders)
  - python-okx PublicData for OKX-specific PIT depth (funding-rate-history, open-interest, mark-price)

Public API mirrors OKXClient surface exactly so OKXBridge / crypto_market_data can be
switched with a one-line import change.

Auth-required calls (get_balance, place_order, cancel_order, get_orders, get_order_history)
require OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE env vars, identical to OKXClient.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import ccxt
from loguru import logger
from okx import PublicData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _build_ccxt_exchange(
    api_key: str,
    secret_key: str,
    passphrase: str,
    simulated: bool,
) -> ccxt.okx:
    config: dict = {
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    }
    if api_key and secret_key and passphrase:
        config["apiKey"] = api_key
        config["secret"] = secret_key
        config["password"] = passphrase
    if simulated:
        config["headers"] = {"x-simulated-trading": "1"}
    return ccxt.okx(config)


# ---------------------------------------------------------------------------
# ccxt inst_id translation
# OKXClient uses "BTC-USDT-SWAP"; ccxt uses "BTC/USDT:USDT"
# ---------------------------------------------------------------------------

def _to_ccxt_symbol(inst_id: str) -> str:
    """Convert OKX inst_id to ccxt unified symbol."""
    if inst_id.endswith("-SWAP"):
        base_quote = inst_id.removesuffix("-SWAP")          # "BTC-USDT"
        base, quote = base_quote.split("-", 1)               # "BTC", "USDT"
        return f"{base}/{quote}:{quote}"                     # "BTC/USDT:USDT"
    if "-" in inst_id:
        parts = inst_id.split("-")
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1]}"                  # spot "BTC/USDT"
    return inst_id


def _to_okx_bar(tf: str) -> str:
    """Map OKX bar string to ccxt timeframe string."""
    mapping = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1H": "1h", "2H": "2h", "4H": "4h", "6H": "6h", "12H": "12h",
        "1D": "1d", "1W": "1w", "1M": "1M",
    }
    return mapping.get(tf, "1h")


# ---------------------------------------------------------------------------
# Main adapter class
# ---------------------------------------------------------------------------

class OKXCCXTAdapter:
    """ccxt-backed OKX client with the same public surface as OKXClient.

    Drop-in replacement: bridge.py imports OKXClient from okx_client;
    swapping to `from .okx_ccxt_adapter import OKXCCXTAdapter as OKXClient`
    is all that is needed for the production rollout.
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        passphrase: str | None = None,
        simulated: bool = False,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("OKX_API_KEY", "")
        self.secret_key = secret_key if secret_key is not None else os.environ.get("OKX_SECRET_KEY", "")
        self.passphrase = passphrase if passphrase is not None else os.environ.get("OKX_PASSPHRASE", "")
        self.simulated = simulated
        self._ex = _build_ccxt_exchange(self.api_key, self.secret_key, self.passphrase, simulated)
        # python-okx for PIT depth endpoints not in ccxt
        self._pub = PublicData.PublicAPI(flag="1" if simulated else "0")

    def has_credentials(self) -> bool:
        return bool(self.api_key and self.secret_key and self.passphrase)

    def check_auth(self) -> dict:
        """Verify credentials by fetching balance. Returns OKX-shaped response dict.

        Replaces the legacy OKXClient._get("/api/v5/account/balance", auth=True)
        call that bridge.py used for connection health checks.
        Returns {"code": "0", "data": [...]} on success, or error dict on failure.
        """
        if not self.has_credentials():
            return self._missing_credentials_response()
        try:
            bal = self._ex.fetch_balance()
            details = bal.get("info", {}).get("data", [])
            return {"code": "0", "data": details if details else []}
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.check_auth: {e}")
            return {"code": "-1", "msg": str(e), "data": []}

    def _missing_credentials_response(self) -> dict:
        return {
            "code": "-1",
            "msg": "OKX credentials missing: set OKX_API_KEY, OKX_SECRET_KEY, and OKX_PASSPHRASE",
            "data": [],
        }

    # ------------------------------------------------------------------
    # Public market data (no auth required)
    # ------------------------------------------------------------------

    def get_tickers(self, inst_type: str = "SPOT") -> list[dict]:
        """Return all tickers for inst_type, mirroring OKXClient.get_tickers."""
        try:
            ccxt_type = inst_type.lower()
            raw = self._ex.fetch_tickers(params={"instType": inst_type})
            rows = []
            for sym, t in raw.items():
                info = t.get("info", {})
                if info.get("instType", "") != inst_type:
                    continue
                rows.append(self._normalize_ticker(t, info))
            return rows
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_tickers: {e}")
            return []

    def get_ticker(self, inst_id: str) -> dict | None:
        """Single ticker, returns OKX-shaped dict (same keys as okx_client)."""
        try:
            sym = _to_ccxt_symbol(inst_id)
            t = self._ex.fetch_ticker(sym)
            info = t.get("info", {})
            return self._normalize_ticker(t, info)
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_ticker {inst_id}: {e}")
            return None

    def _normalize_ticker(self, t: dict, info: dict) -> dict:
        """Map ccxt unified ticker to OKX-style dict for backwards compat."""
        return {
            "instId": info.get("instId", t.get("symbol", "")),
            "instType": info.get("instType", ""),
            "last": str(t.get("last") or info.get("last", "")),
            "lastSz": str(info.get("lastSz", "")),
            "askPx": str(t.get("ask") or info.get("askPx", "")),
            "askSz": str(info.get("askSz", "")),
            "bidPx": str(t.get("bid") or info.get("bidPx", "")),
            "bidSz": str(info.get("bidSz", "")),
            "open24h": str(info.get("open24h", "")),
            "high24h": str(t.get("high") or info.get("high24h", "")),
            "low24h": str(t.get("low") or info.get("low24h", "")),
            "volCcy24h": str(t.get("quoteVolume") or info.get("volCcy24h", "")),
            "vol24h": str(t.get("baseVolume") or info.get("vol24h", "")),
            "ts": str(t.get("timestamp") or info.get("ts", "")),
            "sodUtc0": str(info.get("sodUtc0", "")),
            "sodUtc8": str(info.get("sodUtc8", "")),
        }

    def get_orderbook(self, inst_id: str, depth: int = 20) -> dict:
        """Order book; returns OKX-shaped dict with 'asks'/'bids' as list-of-list."""
        try:
            sym = _to_ccxt_symbol(inst_id)
            ob = self._ex.fetch_order_book(sym, depth)
            # ccxt returns [[price, amount, ?count], ...]
            # OKX native returns [["price","qty","deprecated","orderCount"], ...]
            def fmt(level: list) -> list[str]:
                return [str(v) for v in level[:4]]

            return {
                "bids": [fmt(b) for b in ob.get("bids", [])],
                "asks": [fmt(a) for a in ob.get("asks", [])],
                "ts": str(ob.get("timestamp") or ""),
                "seqId": str(ob.get("nonce") or ""),
            }
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_orderbook {inst_id}: {e}")
            return {}

    def get_kline(self, inst_id: str, bar: str = "1m", limit: int = 100) -> list[list]:
        """K-line data; returns list-of-lists matching OKX candle format.

        OKX native: [ts_ms, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        ccxt:       [ts_ms, open, high, low, close, vol]
        We emit the ccxt 6-column form (same as OKXClient which returns raw OKX data).
        """
        try:
            sym = _to_ccxt_symbol(inst_id)
            tf = _to_okx_bar(bar)
            ohlcv = self._ex.fetch_ohlcv(sym, tf, limit=limit)
            # ccxt returns list of [timestamp, open, high, low, close, volume]
            return [[str(v) for v in row] for row in ohlcv]
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_kline {inst_id} {bar}: {e}")
            return []

    def get_trades(self, inst_id: str, limit: int = 100) -> list[dict]:
        """Recent trades."""
        try:
            sym = _to_ccxt_symbol(inst_id)
            trades = self._ex.fetch_trades(sym, limit=limit)
            return [
                {
                    "tradeId": str(t.get("id", "")),
                    "instId": inst_id,
                    "px": str(t.get("price", "")),
                    "sz": str(t.get("amount", "")),
                    "side": t.get("side", ""),
                    "ts": str(t.get("timestamp", "")),
                }
                for t in trades
            ]
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_trades {inst_id}: {e}")
            return []

    # ------------------------------------------------------------------
    # Authenticated endpoints
    # ------------------------------------------------------------------

    def get_balance(self) -> list[dict]:
        if not self.has_credentials():
            return []
        try:
            bal = self._ex.fetch_balance()
            # ccxt returns unified balance; reshape to OKX-style list
            details = bal.get("info", {}).get("data", [])
            return details if details else []
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_balance: {e}")
            return []

    def get_positions(self) -> list[dict]:
        if not self.has_credentials():
            return []
        try:
            positions = self._ex.fetch_positions()
            return [p.get("info", p) for p in positions]
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_positions: {e}")
            return []

    def place_order(
        self,
        inst_id: str,
        side: str,
        size: str,
        price: str | None = None,
        ord_type: str = "limit",
        td_mode: str = "cash",
        katana_gate_token: str = "",
    ) -> dict:
        """Place order — same gate token contract as OKXClient."""
        if not self.has_credentials():
            r = self._missing_credentials_response()
            return {"error": r["msg"], "code": r["code"]}
        if katana_gate_token != "OKXBridge.live_test":
            return {
                "error": "OKX order rejected: use OKXBridge live_test gate",
                "code": "katana_gate_required",
            }
        try:
            sym = _to_ccxt_symbol(inst_id)
            params = {"tdMode": td_mode}
            order = self._ex.create_order(
                symbol=sym,
                type=ord_type,
                side=side,
                amount=float(size),
                price=float(price) if price and ord_type == "limit" else None,
                params=params,
            )
            info = order.get("info", {})
            result = info.get("data", [{}])[0] if info.get("data") else {}
            order_id = result.get("ordId", order.get("id", ""))
            logger.info(f"OKXCCXTAdapter order placed: {side} {inst_id} size={size} -> {order_id}")
            return {"ordId": order_id, "clOrdId": result.get("clOrdId", ""), "sCode": "0"}
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.place_order {inst_id}: {e}")
            return {"error": str(e), "code": "-1"}

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        if not self.has_credentials():
            return {"error": "missing credentials"}
        try:
            sym = _to_ccxt_symbol(inst_id)
            result = self._ex.cancel_order(order_id, sym)
            info = result.get("info", {})
            data = info.get("data", [{}])[0] if info.get("data") else {}
            return data if data else {"ordId": order_id, "sCode": "0"}
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.cancel_order {inst_id} {order_id}: {e}")
            return {"error": str(e)}

    def get_orders(self, inst_type: str = "SPOT") -> list[dict]:
        if not self.has_credentials():
            return []
        try:
            orders = self._ex.fetch_open_orders(params={"instType": inst_type})
            return [o.get("info", o) for o in orders]
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_orders: {e}")
            return []

    def get_order_history(self, inst_type: str = "SPOT", limit: int = 20) -> list[dict]:
        if not self.has_credentials():
            return []
        try:
            orders = self._ex.fetch_closed_orders(params={"instType": inst_type, "limit": limit})
            return [o.get("info", o) for o in orders]
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_order_history: {e}")
            return []

    # ------------------------------------------------------------------
    # PIT depth endpoints (python-okx, not in ccxt unified API)
    # ------------------------------------------------------------------

    def get_funding_rate(self, inst_id: str) -> dict | None:
        """Current funding rate via python-okx."""
        try:
            r = self._pub.get_funding_rate(instId=inst_id)
            if r.get("code") == "0" and r.get("data"):
                return r["data"][0]
            return None
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_funding_rate {inst_id}: {e}")
            return None

    def get_funding_rate_history(
        self,
        inst_id: str,
        before: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Funding rate history via python-okx.

        Returns list of dicts with keys:
          instId, fundingTime (ms str), fundingRate, realizedRate
        """
        try:
            kwargs: dict = {"instId": inst_id, "limit": str(limit)}
            if before:
                kwargs["before"] = before
            if after:
                kwargs["after"] = after
            r = self._pub.funding_rate_history(**kwargs)
            if r.get("code") == "0":
                return r.get("data", [])
            logger.warning(f"funding_rate_history {inst_id}: code={r.get('code')} msg={r.get('msg')}")
            return []
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_funding_rate_history {inst_id}: {e}")
            return []

    def get_open_interest(self, inst_id: str) -> dict | None:
        """Point-in-time open interest via python-okx.

        Returns dict with keys: instId, oi (contracts), oiCcy (base), oiUsd, ts (ms str)
        """
        try:
            r = self._pub.get_open_interest(instType="SWAP", instId=inst_id)
            if r.get("code") == "0" and r.get("data"):
                return r["data"][0]
            return None
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_open_interest {inst_id}: {e}")
            return None

    def get_mark_price(self, inst_id: str) -> dict | None:
        """Mark price via python-okx.

        Returns dict with keys: instId, markPx, ts (ms str)
        """
        try:
            r = self._pub.get_mark_price(instType="SWAP", instId=inst_id)
            if r.get("code") == "0" and r.get("data"):
                return r["data"][0]
            return None
        except Exception as e:
            logger.error(f"OKXCCXTAdapter.get_mark_price {inst_id}: {e}")
            return None


# ---------------------------------------------------------------------------
# Module-level singleton (matches okx_client.py pattern)
# ---------------------------------------------------------------------------

_adapter: OKXCCXTAdapter | None = None


def get_okx_ccxt_adapter() -> OKXCCXTAdapter:
    global _adapter
    if _adapter is None:
        _adapter = OKXCCXTAdapter()
    return _adapter
