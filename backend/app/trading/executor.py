"""Order executor for paper execution and explicit broker boundary modes."""

import time
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from ..data.store import get_store
from .intent import TradingMode, require_trading_mode
from .risk import get_risk_manager


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    id: int
    code: str
    market: int
    direction: str
    price: float
    volume: int
    strategy: str
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float = 0.0
    filled_volume: int = 0
    created_at: float = field(default_factory=time.time)
    error: str = ""


@dataclass
class Position:
    code: str
    market: int
    volume: int
    avg_price: float
    current_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0


class OrderExecutor:
    """Order executor.

    Supported modes:
    - paper: simulated fills, no broker adapter
    - okx: gated crypto live_test adapter
    - qmt: EasyXT/QMT boundary adapter

    Legacy ths/tdx/ctp modes remain in app.trade for parked compatibility.
    """

    def __init__(self, mode: str = "paper"):
        self.mode = mode
        self.orders: dict[int, Order] = {}
        self.positions: dict[str, Position] = {}
        self._next_id = 1
        self.risk_manager = get_risk_manager()
        self._bridge = None
        self._init_bridge()

    def submit_order(
        self,
        code: str,
        direction: str,
        price: float,
        volume: int,
        strategy: str = "manual",
    ) -> dict:
        if self.mode == "paper":
            require_trading_mode(TradingMode.PAPER)
        elif self.mode in {"okx", "qmt"}:
            require_trading_mode(TradingMode.LIVE_TEST)

        market = 99 if "-" in code else 1 if code.startswith(("6", "11")) else 0
        amount = price * volume

        risk_check = self.risk_manager.check_order(
            code=code,
            direction=direction,
            price=price,
            volume=volume,
            positions=self.positions,
        )
        if not risk_check["passed"]:
            logger.warning(f"Order rejected by risk: {risk_check['reason']}")
            return {"error": risk_check["reason"]}

        order_id = self._next_id
        self._next_id += 1
        order = Order(
            id=order_id,
            code=code,
            market=market,
            direction=direction,
            price=price,
            volume=volume,
            strategy=strategy,
        )

        if self.mode == "paper":
            order.status = OrderStatus.FILLED
            order.filled_price = price
            order.filled_volume = volume
            self._update_position(order)
            self._record_paper_trade(code, market, direction, price, volume, amount, strategy)
            logger.info(f"Paper trade: {direction} {code} @ {price} x {volume}")
        else:
            order.status = OrderStatus.SUBMITTED
            self._submit_live(order)

        self.orders[order_id] = order
        self.risk_manager.record_trade(direction, amount, price)

        result = {
            "order_id": order_id,
            "status": order.status.value,
            "code": code,
            "direction": direction,
            "price": price,
            "volume": volume,
        }
        if order.error:
            result["error"] = order.error
        return result

    def _record_paper_trade(
        self,
        code: str,
        market: int,
        direction: str,
        price: float,
        volume: int,
        amount: float,
        strategy: str,
    ) -> None:
        try:
            store = get_store()
            store.record_trade(
                code=code,
                market=market,
                direction=direction,
                price=price,
                volume=volume,
                amount=amount,
                strategy=strategy,
            )
        except Exception as e:
            logger.debug(f"Record trade to store failed: {e}")

    def _init_bridge(self):
        if self.mode == "paper":
            return
        if self.mode == "okx":
            from .adapters.okx import OKXBridge

            self._bridge = OKXBridge(trade_mode="swap")
        elif self.mode == "qmt":
            from .adapters.qmt import QMTBridge

            self._bridge = QMTBridge()
            self._bridge.connect()
        else:
            logger.warning(f"Unsupported active trading mode: {self.mode}")

    def _submit_live(self, order: Order):
        if self._bridge is None:
            logger.error(f"No trade bridge for mode: {self.mode}")
            order.status = OrderStatus.REJECTED
            order.error = "Trade bridge not initialized"
            return

        if order.direction == "buy":
            result = self._bridge.buy(order.code, order.price, order.volume)
        else:
            result = self._bridge.sell(order.code, order.price, order.volume)

        if result.get("error"):
            order.status = OrderStatus.REJECTED
            order.error = result["error"]
        else:
            order.status = OrderStatus.SUBMITTED
            logger.info(f"Live order submitted: {order.direction} {order.code} @ {order.price}")

    def _update_position(self, order: Order):
        code = order.code
        if code not in self.positions:
            self.positions[code] = Position(
                code=code,
                market=order.market,
                volume=0,
                avg_price=0,
            )

        pos = self.positions[code]
        if order.direction == "buy":
            total_cost = pos.avg_price * pos.volume + order.filled_price * order.filled_volume
            pos.volume += order.filled_volume
            pos.avg_price = total_cost / pos.volume if pos.volume > 0 else 0
        elif order.direction == "sell":
            pos.volume -= order.filled_volume
            if pos.volume <= 0:
                del self.positions[code]

    def cancel_order(self, order_id: int) -> bool:
        if order_id not in self.orders:
            return False
        order = self.orders[order_id]
        if order.status not in (OrderStatus.PENDING, OrderStatus.SUBMITTED):
            return False
        order.status = OrderStatus.CANCELLED
        logger.info(f"Order #{order_id} cancelled")
        return True

    def get_positions(self) -> list[dict]:
        return [
            {
                "code": p.code,
                "market": p.market,
                "volume": p.volume,
                "avg_price": round(p.avg_price, 3),
                "current_price": p.current_price,
                "pnl": round(p.pnl, 2),
                "pnl_pct": round(p.pnl_pct, 4),
            }
            for p in self.positions.values()
        ]

    def get_pending_orders(self) -> list[dict]:
        return [
            {
                "id": o.id,
                "code": o.code,
                "direction": o.direction,
                "price": o.price,
                "volume": o.volume,
                "status": o.status.value,
                "strategy": o.strategy,
            }
            for o in self.orders.values()
            if o.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED)
        ]

    def get_pnl_summary(self) -> dict:
        total_pnl = sum(p.pnl for p in self.positions.values())
        return {
            "total_pnl": round(total_pnl, 2),
            "position_count": len(self.positions),
            "today_trades": len(
                [o for o in self.orders.values() if o.status == OrderStatus.FILLED]
            ),
        }

    def sync_real_positions(self, real_positions: list[dict]):
        real_map = {}
        for p in real_positions:
            code = str(p.get("symbol", p.get("证券代码", p.get("code", ""))))
            vol = int(p.get("quantity", p.get("股票余额", p.get("可用余额", p.get("volume", 0)))))
            if code and vol > 0:
                real_map[code] = vol

        for code, real_vol in real_map.items():
            mem_vol = self.positions[code].volume if code in self.positions else 0
            if real_vol != mem_vol:
                logger.warning(f"Position mismatch: {code} real={real_vol} mem={mem_vol}")

        for code in self.positions:
            if code not in real_map:
                logger.warning(f"Position {code} in memory but not in broker")

    def update_prices(self, quotes: list[dict]):
        quote_map = {}
        for q in quotes:
            code = q.get("code", "")
            price = q.get("price", 0)
            if code and price:
                quote_map[code] = price

        for pos in self.positions.values():
            if pos.code in quote_map:
                pos.current_price = quote_map[pos.code]
                pos.pnl = (pos.current_price - pos.avg_price) * pos.volume
                pos.pnl_pct = (
                    (pos.current_price - pos.avg_price) / pos.avg_price
                    if pos.avg_price > 0
                    else 0
                )


_executor: OrderExecutor | None = None


def get_executor() -> OrderExecutor:
    global _executor
    if _executor is None:
        _executor = OrderExecutor(mode="paper")
    return _executor
