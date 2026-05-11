"""QMT (迅投) 接口桥接 - 招商证券量化交易接口

注意: 需要先开通 QMT 权限并安装 miniQMT + XtQuant SDK
"""

from loguru import logger


class QMTBridge:
    """QMT 交易接口桥接"""

    def __init__(self):
        self.connected = False
        self.xt_trader = None
        self.account_id = ""

    def connect(self, path: str = "", account: str = "") -> bool:
        """连接 miniQMT

        Args:
            path: miniQMT 安装路径
            account: 资金账号
        """
        try:
            # 尝试导入 xtquant（需要安装 miniQMT 后才有）
            from xtquant import xttrader, xtdata

            self.xt_trader = xttrader.XtQuantTrader(path, session_id=1)
            self.xt_trader.start()
            self.account_id = account
            self.connected = True
            logger.info(f"QMT connected: {account}")
            return True
        except ImportError:
            logger.warning(
                "xtquant not installed. Please install miniQMT first. "
                "Contact your broker to enable QMT access."
            )
            return False
        except Exception as e:
            logger.error(f"QMT connection failed: {e}")
            return False

    def buy(self, code: str, price: float, volume: int) -> dict:
        """买入"""
        if not self.connected:
            return {"error": "QMT not connected"}
        try:
            from xtquant import xtconstant

            order_id = self.xt_trader.order_stock(
                self.account_id,
                code,
                xtconstant.STOCK_BUY,
                volume,
                xtconstant.FIX_PRICE,
                price,
            )
            logger.info(f"QMT buy: {code} @ {price} x {volume}, order_id={order_id}")
            return {"order_id": order_id, "status": "submitted"}
        except Exception as e:
            logger.error(f"QMT buy error: {e}")
            return {"error": str(e)}

    def sell(self, code: str, price: float, volume: int) -> dict:
        """卖出"""
        if not self.connected:
            return {"error": "QMT not connected"}
        try:
            from xtquant import xtconstant

            order_id = self.xt_trader.order_stock(
                self.account_id,
                code,
                xtconstant.STOCK_SELL,
                volume,
                xtconstant.FIX_PRICE,
                price,
            )
            logger.info(f"QMT sell: {code} @ {price} x {volume}, order_id={order_id}")
            return {"order_id": order_id, "status": "submitted"}
        except Exception as e:
            logger.error(f"QMT sell error: {e}")
            return {"error": str(e)}

    def cancel(self, order_id: int) -> bool:
        """撤单"""
        if not self.connected:
            return False
        try:
            self.xt_trader.cancel_order_stock(self.account_id, order_id)
            return True
        except Exception as e:
            logger.error(f"QMT cancel error: {e}")
            return False

    def query_positions(self) -> list[dict]:
        """查询持仓"""
        if not self.connected:
            return []
        try:
            positions = self.xt_trader.query_stock_positions(self.account_id)
            return [
                {
                    "code": p.stock_code,
                    "volume": p.volume,
                    "available": p.can_use_volume,
                    "avg_price": p.avg_price,
                    "market_value": p.market_value,
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"QMT query positions error: {e}")
            return []

    def query_orders(self) -> list[dict]:
        """查询委托"""
        if not self.connected:
            return []
        try:
            orders = self.xt_trader.query_stock_orders(self.account_id)
            return [
                {
                    "order_id": o.order_id,
                    "code": o.stock_code,
                    "direction": "buy" if o.order_type == 23 else "sell",
                    "price": o.price,
                    "volume": o.order_volume,
                    "filled_volume": o.traded_volume,
                    "status": o.order_status,
                }
                for o in orders
            ]
        except Exception as e:
            logger.error(f"QMT query orders error: {e}")
            return []

    def disconnect(self):
        if self.xt_trader:
            self.xt_trader.stop()
            self.connected = False
