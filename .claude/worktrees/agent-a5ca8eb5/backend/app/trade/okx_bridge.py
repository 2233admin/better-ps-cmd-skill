"""OKX 交易桥接 - 对接 OrderExecutor 的统一接口

将 OKX REST API 封装为 executor 可调用的 buy/sell 接口。
支持现货和永续合约两种模式。
"""

from loguru import logger

from ..data.okx_client import get_okx_client, OKXClient


class OKXBridge:
    """OKX 交易桥接"""

    def __init__(self, trade_mode: str = "spot"):
        """
        Args:
            trade_mode: "spot" (现货) 或 "swap" (永续合约)
        """
        self.client: OKXClient = get_okx_client()
        self.trade_mode = trade_mode
        self.connected = False

    def connect(self) -> bool:
        """验证连接"""
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

    def _inst_id(self, code: str) -> str:
        """将 code 转为 OKX instId 格式"""
        # 已经是 OKX 格式 (如 BTC-USDT)
        if "-" in code:
            if self.trade_mode == "swap" and not code.endswith("-SWAP"):
                return code + "-SWAP"
            return code
        # 纯符号 (如 BTC) → 补全
        base = code.upper()
        suffix = "-USDT-SWAP" if self.trade_mode == "swap" else "-USDT"
        return base + suffix

    def _td_mode(self) -> str:
        """交易模式"""
        if self.trade_mode == "swap":
            return "cross"  # 永续用全仓
        return "cash"  # 现货

    def buy(self, code: str, price: float, volume: int) -> dict:
        """买入"""
        inst_id = self._inst_id(code)
        sz = str(volume)

        result = self.client.place_order(
            inst_id=inst_id,
            side="buy",
            size=sz,
            price=f"{price:.8g}",
            ord_type="limit",
            td_mode=self._td_mode(),
        )

        if "error" in result:
            return {"error": result["error"]}
        return {
            "order_id": result.get("ordId", ""),
            "inst_id": inst_id,
            "side": "buy",
        }

    def sell(self, code: str, price: float, volume: int) -> dict:
        """卖出"""
        inst_id = self._inst_id(code)
        sz = str(volume)

        result = self.client.place_order(
            inst_id=inst_id,
            side="sell",
            size=sz,
            price=f"{price:.8g}",
            ord_type="limit",
            td_mode=self._td_mode(),
        )

        if "error" in result:
            return {"error": result["error"]}
        return {
            "order_id": result.get("ordId", ""),
            "inst_id": inst_id,
            "side": "sell",
        }

    def market_buy(self, code: str, size: str) -> dict:
        """市价买入"""
        inst_id = self._inst_id(code)
        return self.client.place_order(
            inst_id=inst_id, side="buy", size=size,
            ord_type="market", td_mode=self._td_mode(),
        )

    def market_sell(self, code: str, size: str) -> dict:
        """市价卖出"""
        inst_id = self._inst_id(code)
        return self.client.place_order(
            inst_id=inst_id, side="sell", size=size,
            ord_type="market", td_mode=self._td_mode(),
        )

    def get_positions(self) -> list[dict]:
        """获取当前持仓"""
        return self.client.get_positions()

    def get_balance(self) -> list[dict]:
        """获取账户余额"""
        return self.client.get_balance()

    def cancel(self, code: str, order_id: str) -> dict:
        """撤单"""
        inst_id = self._inst_id(code)
        return self.client.cancel_order(inst_id, order_id)
