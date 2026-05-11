"""OKX 认证客户端 - 支持行情查询和交易下单

API v5 文档: https://www.okx.com/docs-v5/
认证头: OK-ACCESS-KEY, OK-ACCESS-SIGN, OK-ACCESS-TIMESTAMP, OK-ACCESS-PASSPHRASE
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import requests
from loguru import logger


class OKXClient:
    """OKX REST API 客户端"""

    BASE_URL = "https://www.okx.com"

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        passphrase: str = "",
        simulated: bool = False,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.simulated = simulated  # True = 模拟盘
        self.session = requests.Session()
        self.session.timeout = 10

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """生成 HMAC SHA256 签名"""
        message = timestamp + method.upper() + path + body
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        """生成认证请求头"""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        sign = self._sign(timestamp, method, path, body)
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.simulated:
            headers["x-simulated-trading"] = "1"
        return headers

    def _get(self, path: str, params: dict | None = None, auth: bool = False) -> dict:
        """GET 请求"""
        url = self.BASE_URL + path
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if qs:
                path = path + "?" + qs
                url = self.BASE_URL + path

        headers = self._headers("GET", path) if auth else {}
        try:
            r = self.session.get(url, headers=headers)
            return r.json()
        except Exception as e:
            logger.error(f"OKX GET {path}: {e}")
            return {"code": "-1", "msg": str(e), "data": []}

    def _post(self, path: str, data: dict) -> dict:
        """POST 请求 (需认证)"""
        body = json.dumps(data)
        headers = self._headers("POST", path, body)
        try:
            r = self.session.post(self.BASE_URL + path, headers=headers, data=body)
            return r.json()
        except Exception as e:
            logger.error(f"OKX POST {path}: {e}")
            return {"code": "-1", "msg": str(e), "data": []}

    # ========== 公开行情 (无需认证) ==========

    def get_tickers(self, inst_type: str = "SPOT") -> list[dict]:
        """全量行情"""
        r = self._get("/api/v5/market/tickers", {"instType": inst_type})
        return r.get("data", []) if r.get("code") == "0" else []

    def get_ticker(self, inst_id: str) -> dict | None:
        """单币对行情"""
        r = self._get("/api/v5/market/ticker", {"instId": inst_id})
        data = r.get("data", [])
        return data[0] if data else None

    def get_orderbook(self, inst_id: str, depth: int = 20) -> dict:
        """深度数据"""
        r = self._get("/api/v5/market/books", {"instId": inst_id, "sz": str(depth)})
        data = r.get("data", [])
        return data[0] if data else {}

    def get_kline(
        self, inst_id: str, bar: str = "1m", limit: int = 100
    ) -> list[list]:
        """K线数据
        bar: 1m/3m/5m/15m/30m/1H/2H/4H/1D/1W/1M
        """
        r = self._get("/api/v5/market/candles", {
            "instId": inst_id, "bar": bar, "limit": str(limit),
        })
        return r.get("data", [])

    def get_trades(self, inst_id: str, limit: int = 100) -> list[dict]:
        """最近成交"""
        r = self._get("/api/v5/market/trades", {
            "instId": inst_id, "limit": str(limit),
        })
        return r.get("data", [])

    # ========== 认证接口 ==========

    def get_balance(self) -> list[dict]:
        """查询账户余额"""
        r = self._get("/api/v5/account/balance", auth=True)
        return r.get("data", []) if r.get("code") == "0" else []

    def get_positions(self) -> list[dict]:
        """查询持仓"""
        r = self._get("/api/v5/account/positions", auth=True)
        return r.get("data", []) if r.get("code") == "0" else []

    def place_order(
        self,
        inst_id: str,
        side: str,  # "buy" or "sell"
        size: str,
        price: str | None = None,
        ord_type: str = "limit",  # "market" or "limit"
        td_mode: str = "cash",  # "cash"=现货, "cross"=全仓, "isolated"=逐仓
    ) -> dict:
        """下单"""
        params = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": size,
        }
        if price and ord_type == "limit":
            params["px"] = price

        r = self._post("/api/v5/trade/order", params)
        if r.get("code") == "0":
            order_data = r["data"][0]
            logger.info(f"OKX order placed: {side} {inst_id} size={size} -> {order_data}")
            return order_data
        else:
            logger.error(f"OKX order failed: {r}")
            return {"error": r.get("msg", "unknown"), "code": r.get("code")}

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        """撤单"""
        r = self._post("/api/v5/trade/cancel-order", {
            "instId": inst_id,
            "ordId": order_id,
        })
        return r.get("data", [{}])[0] if r.get("code") == "0" else {"error": r.get("msg")}

    def get_orders(self, inst_type: str = "SPOT") -> list[dict]:
        """查询当前挂单"""
        r = self._get("/api/v5/trade/orders-pending", {"instType": inst_type}, auth=True)
        return r.get("data", []) if r.get("code") == "0" else []

    def get_order_history(self, inst_type: str = "SPOT", limit: int = 20) -> list[dict]:
        """查询历史订单"""
        r = self._get("/api/v5/trade/orders-history-archive", {
            "instType": inst_type, "limit": str(limit),
        }, auth=True)
        return r.get("data", []) if r.get("code") == "0" else []


# 全局客户端
_client: OKXClient | None = None


def get_okx_client() -> OKXClient:
    global _client
    if _client is None:
        _client = OKXClient(
            api_key="d6875510-5965-4ad2-8820-49ebf5ab2085",
            secret_key="E7172B6326FD0DBCBBA1D5E2B0E0A446",
            passphrase="Xyt456321..",
        )
    return _client
