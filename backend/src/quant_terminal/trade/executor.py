"""
实盘交易执行器

支持多种交易通道:
- Paper Trading: 模拟盘
- OKX: 加密货币交易所
- QMT: 迅投量化终端 (A股)
- ThsTrader: 同花顺客户端 (A股)

Example:
    >>> from quant_terminal.trade import LiveTrader, PaperTradingExecutor
    >>> executor = PaperTradingExecutor(initial_capital=1_000_000)
    >>> trader = LiveTrader(executor, strategy)
    >>> trader.start()
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Callable
from enum import Enum
import threading
import time
from loguru import logger


class OrderType(Enum):
    """订单类型"""
    MARKET = "market"       # 市价单
    LIMIT = "limit"         # 限价单
    STOP = "stop"           # 止损单


class OrderSide(Enum):
    """买卖方向"""
    BUY = 1
    SELL = -1


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"     # 待提交
    SUBMITTED = "submitted" # 已提交
    PARTIAL = "partial"     # 部分成交
    FILLED = "filled"       # 完全成交
    CANCELLED = "cancelled" # 已撤销
    REJECTED = "rejected"   # 已拒绝


@dataclass
class Order:
    """订单"""
    id: str
    code: str
    side: OrderSide
    type: OrderType
    volume: int
    price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_volume: int = 0
    avg_price: float = 0.0
    commission: float = 0.0
    create_time: datetime = field(default_factory=datetime.now)
    update_time: Optional[datetime] = None
    error_msg: Optional[str] = None


@dataclass
class Position:
    """持仓"""
    code: str
    volume: int
    avg_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class TradeExecutor(ABC):
    """
    交易执行器基类

    所有实盘/模拟盘执行器必须继承此类
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """执行器名称"""
        pass

    @abstractmethod
    def connect(self) -> bool:
        """连接交易通道"""
        pass

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    def submit_order(self, order: Order) -> bool:
        """提交订单"""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        pass

    @abstractmethod
    def get_position(self, code: str) -> Optional[Position]:
        """查询持仓"""
        pass

    @abstractmethod
    def get_all_positions(self) -> Dict[str, Position]:
        """查询所有持仓"""
        pass

    @abstractmethod
    def get_account(self) -> Dict:
        """查询账户信息"""
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """查询订单状态"""
        pass


class PaperTradingExecutor(TradeExecutor):
    """
    模拟盘执行器

    用于策略验证，不产生真实成交
    """

    def __init__(self, initial_capital: float = 1_000_000.0,
                 commission: float = 0.0001,
                 slippage: float = 0.0002):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.order_counter = 0
        self._lock = threading.Lock()
        self._price_cache: Dict[str, float] = {}

    @property
    def name(self) -> str:
        return "PaperTrading"

    def connect(self) -> bool:
        logger.info("[PaperTrading] 模拟盘已连接")
        return True

    def disconnect(self):
        logger.info("[PaperTrading] 模拟盘已断开")

    def submit_order(self, order: Order) -> bool:
        """模拟提交订单"""
        with self._lock:
            self.order_counter += 1
            order.id = f"PAPER_{self.order_counter}"
            order.status = OrderStatus.SUBMITTED
            self.orders[order.id] = order

            # 模拟成交 (假设有最新价格)
            if order.code in self._price_cache:
                self._simulate_fill(order, self._price_cache[order.code])
            else:
                logger.warning(f"[PaperTrading] 无价格数据，订单 {order.id} 待成交")

            return True

    def _simulate_fill(self, order: Order, current_price: float):
        """模拟成交"""
        # 应用滑点
        if order.side == OrderSide.BUY:
            fill_price = current_price * (1 + self.slippage)
        else:
            fill_price = current_price * (1 - self.slippage)

        # 计算手续费
        notional = fill_price * order.volume
        commission = notional * self.commission

        # 更新订单
        order.status = OrderStatus.FILLED
        order.filled_volume = order.volume
        order.avg_price = fill_price
        order.commission = commission
        order.update_time = datetime.now()

        # 更新持仓和资金
        self._update_position(order, fill_price, commission)

        logger.info(f"[PaperTrading] 订单成交: {order.code} {order.side.name} "
                   f"{order.volume}@{fill_price:.2f} 手续费:{commission:.2f}")

    def _update_position(self, order: Order, price: float, commission: float):
        """更新持仓"""
        cost = price * order.volume + commission

        if order.side == OrderSide.BUY:
            if self.cash < cost:
                order.status = OrderStatus.REJECTED
                order.error_msg = "资金不足"
                return

            self.cash -= cost

            if order.code in self.positions:
                pos = self.positions[order.code]
                total_cost = pos.avg_price * pos.volume + price * order.volume
                pos.volume += order.volume
                pos.avg_price = total_cost / pos.volume
            else:
                self.positions[order.code] = Position(
                    code=order.code,
                    volume=order.volume,
                    avg_price=price
                )
        else:
            if order.code not in self.positions or self.positions[order.code].volume < order.volume:
                order.status = OrderStatus.REJECTED
                order.error_msg = "持仓不足"
                return

            pos = self.positions[order.code]
            self.cash += price * order.volume - commission
            pos.volume -= order.volume

            if pos.volume == 0:
                del self.positions[order.code]

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False

    def get_position(self, code: str) -> Optional[Position]:
        return self.positions.get(code)

    def get_all_positions(self) -> Dict[str, Position]:
        return self.positions.copy()

    def get_account(self) -> Dict:
        position_value = sum(
            pos.volume * pos.avg_price for pos in self.positions.values()
        )
        return {
            'cash': self.cash,
            'position_value': position_value,
            'total_value': self.cash + position_value,
            'positions': len(self.positions)
        }

    def get_order_status(self, order_id: str) -> OrderStatus:
        if order_id in self.orders:
            return self.orders[order_id].status
        return OrderStatus.REJECTED

    def update_price(self, code: str, price: float):
        """更新最新价格 (用于模拟成交)"""
        self._price_cache[code] = price

        # 检查待成交订单
        for order in self.orders.values():
            if order.code == code and order.status == OrderStatus.SUBMITTED:
                self._simulate_fill(order, price)


class OKXExecutor(TradeExecutor):
    """
    OKX交易所执行器 (加密货币)
    """

    def __init__(self, api_key: str, api_secret: str, passphrase: str,
                 testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet
        self.client = None

    @property
    def name(self) -> str:
        return "OKX" + ("_TEST" if self.testnet else "_LIVE")

    def connect(self) -> bool:
        try:
            import okx.Trade as Trade
            import okx.Account as Account

            domain = "https://www.okx.com"
            if self.testnet:
                domain = "https://www.okx.com"  # OKX测试网

            self.trade_api = Trade.TradeAPI(
                self.api_key, self.api_secret, self.passphrase,
                False, domain
            )
            self.account_api = Account.AccountAPI(
                self.api_key, self.api_secret, self.passphrase,
                False, domain
            )

            # 测试连接
            result = self.account_api.get_account_balance()
            if result.get('code') == '0':
                logger.info("[OKX] 连接成功")
                return True
            else:
                logger.error(f"[OKX] 连接失败: {result}")
                return False

        except Exception as e:
            logger.error(f"[OKX] 连接异常: {e}")
            return False

    def disconnect(self):
        logger.info("[OKX] 已断开")

    def submit_order(self, order: Order) -> bool:
        try:
            result = self.trade_api.place_order(
                instId=order.code,
                tdMode="cross" if self.testnet else "cash",
                side="buy" if order.side == OrderSide.BUY else "sell",
                ordType=order.type.value,
                sz=str(order.volume),
                px=str(order.price) if order.price else None
            )

            if result.get('code') == '0':
                order.id = result['data'][0]['ordId']
                order.status = OrderStatus.SUBMITTED
                logger.info(f"[OKX] 订单提交成功: {order.id}")
                return True
            else:
                order.status = OrderStatus.REJECTED
                order.error_msg = result.get('msg', 'Unknown error')
                logger.error(f"[OKX] 订单提交失败: {order.error_msg}")
                return False

        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.error_msg = str(e)
            logger.error(f"[OKX] 订单异常: {e}")
            return False

    def cancel_order(self, order_id: str) -> bool:
        # 实现撤单逻辑
        pass

    def get_position(self, code: str) -> Optional[Position]:
        # 实现查询持仓
        pass

    def get_all_positions(self) -> Dict[str, Position]:
        # 实现查询所有持仓
        pass

    def get_account(self) -> Dict:
        try:
            result = self.account_api.get_account_balance()
            if result.get('code') == '0':
                data = result['data'][0]
                return {
                    'cash': float(data['details'][0]['cashBal']),
                    'total_value': float(data['totalEq']),
                    'currency': data['details'][0]['ccy']
                }
        except Exception as e:
            logger.error(f"[OKX] 查询账户失败: {e}")
        return {}

    def get_order_status(self, order_id: str) -> OrderStatus:
        # 实现查询订单状态
        pass


class QMTExecutor(TradeExecutor):
    """
    QMT (迅投) 执行器 - A股量化交易
    """

    def __init__(self, mini_qmt_path: str, account_id: str):
        self.mini_qmt_path = mini_qmt_path
        self.account_id = account_id
        self.xt_trader = None

    @property
    def name(self) -> str:
        return f"QMT_{self.account_id}"

    def connect(self) -> bool:
        try:
            from xtquant.xttrader import XtQuantTrader
            from xtquant.xttype import StockAccount

            self.xt_trader = XtQuantTrader(self.mini_qmt_path, 'quant_terminal')
            self.stock_account = StockAccount(self.account_id)

            # 启动交易线程
            self.xt_trader.start()
            connect_result = self.xt_trader.connect()

            if connect_result == 0:
                logger.info(f"[QMT] 连接成功: {self.account_id}")
                return True
            else:
                logger.error(f"[QMT] 连接失败: {connect_result}")
                return False

        except Exception as e:
            logger.error(f"[QMT] 连接异常: {e}")
            return False

    def disconnect(self):
        if self.xt_trader:
            self.xt_trader.stop()
            logger.info("[QMT] 已断开")

    def submit_order(self, order: Order) -> bool:
        try:
            from xtquant.xttype import StockOrder

            xt_order = StockOrder(
                stock_code=order.code,
                order_type=self._map_order_type(order.type),
                order_volume=order.volume,
                price_type=self._map_price_type(order.type),
                price=order.price or 0.0
            )

            order_id = self.xt_trader.order(self.stock_account, xt_order)

            if order_id > 0:
                order.id = str(order_id)
                order.status = OrderStatus.SUBMITTED
                logger.info(f"[QMT] 订单提交成功: {order_id}")
                return True
            else:
                order.status = OrderStatus.REJECTED
                logger.error(f"[QMT] 订单提交失败: {order_id}")
                return False

        except Exception as e:
            logger.error(f"[QMT] 订单异常: {e}")
            return False

    def _map_order_type(self, order_type: OrderType) -> int:
        """映射订单类型"""
        if order_type == OrderType.MARKET:
            return 23  # QMT市价单
        return 24  # QMT限价单

    def _map_price_type(self, order_type: OrderType) -> int:
        """映射价格类型"""
        if order_type == OrderType.MARKET:
            return 4  # 市价
        return 0  # 限价

    def cancel_order(self, order_id: str) -> bool:
        try:
            result = self.xt_trader.cancel_order(self.stock_account, int(order_id))
            return result == 0
        except Exception as e:
            logger.error(f"[QMT] 撤单失败: {e}")
            return False

    def get_position(self, code: str) -> Optional[Position]:
        positions = self.get_all_positions()
        return positions.get(code)

    def get_all_positions(self) -> Dict[str, Position]:
        try:
            xt_positions = self.xt_trader.query_stock_positions(self.stock_account)
            positions = {}
            for pos in xt_positions:
                positions[pos.stock_code] = Position(
                    code=pos.stock_code,
                    volume=pos.volume,
                    avg_price=pos.avg_price,
                    unrealized_pnl=pos.unrealized_pnl
                )
            return positions
        except Exception as e:
            logger.error(f"[QMT] 查询持仓失败: {e}")
            return {}

    def get_account(self) -> Dict:
        try:
            asset = self.xt_trader.query_stock_asset(self.stock_account)
            return {
                'cash': asset.cash,
                'frozen_cash': asset.frozen_cash,
                'market_value': asset.market_value,
                'total_value': asset.total_asset
            }
        except Exception as e:
            logger.error(f"[QMT] 查询账户失败: {e}")
            return {}

    def get_order_status(self, order_id: str) -> OrderStatus:
        try:
            orders = self.xt_trader.query_stock_orders(self.stock_account, int(order_id))
            if orders:
                # 映射QMT状态到内部状态
                return self._map_status(orders[0].order_status)
        except Exception as e:
            logger.error(f"[QMT] 查询订单失败: {e}")
        return OrderStatus.REJECTED

    def _map_status(self, xt_status: int) -> OrderStatus:
        """映射QMT订单状态"""
        status_map = {
            48: OrderStatus.SUBMITTED,   # 已报
            49: OrderStatus.PARTIAL,      # 部成
            50: OrderStatus.FILLED,       # 已成
            51: OrderStatus.CANCELLED,    # 已撤
            52: OrderStatus.REJECTED,     # 已拒绝
        }
        return status_map.get(xt_status, OrderStatus.PENDING)


class ThsTraderExecutor(TradeExecutor):
    """
    同花顺客户端执行器

    需要同花顺交易客户端保持登录状态
    """

    def __init__(self, account_id: str = None):
        self.account_id = account_id
        self.client = None

    @property
    def name(self) -> str:
        return "ThsTrader"

    def connect(self) -> bool:
        try:
            import easytrader
            self.client = easytrader.use('ths')
            self.client.connect()
            logger.info("[ThsTrader] 同花顺客户端已连接")
            return True
        except Exception as e:
            logger.error(f"[ThsTrader] 连接失败: {e}")
            return False

    def disconnect(self):
        logger.info("[ThsTrader] 已断开")

    def submit_order(self, order: Order) -> bool:
        try:
            if order.side == OrderSide.BUY:
                result = self.client.buy(
                    order.code, price=order.price or 0.0, amount=order.volume
                )
            else:
                result = self.client.sell(
                    order.code, price=order.price or 0.0, amount=order.volume
                )

            if result:
                order.id = result.get('entrust_no', str(time.time()))
                order.status = OrderStatus.SUBMITTED
                return True
            return False

        except Exception as e:
            logger.error(f"[ThsTrader] 订单失败: {e}")
            return False

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.client.cancel_entrust(order_id)
            return True
        except Exception as e:
            logger.error(f"[ThsTrader] 撤单失败: {e}")
            return False

    def get_position(self, code: str) -> Optional[Position]:
        positions = self.get_all_positions()
        return positions.get(code)

    def get_all_positions(self) -> Dict[str, Position]:
        try:
            positions = {}
            for pos in self.client.position:
                positions[pos['stock_code']] = Position(
                    code=pos['stock_code'],
                    volume=pos['current_amount'],
                    avg_price=pos['cost_price'],
                    unrealized_pnl=pos['market_value'] - pos['cost_price'] * pos['current_amount']
                )
            return positions
        except Exception as e:
            logger.error(f"[ThsTrader] 查询持仓失败: {e}")
            return {}

    def get_account(self) -> Dict:
        try:
            balance = self.client.balance
            return {
                'cash': balance['available_funds'],
                'total_value': balance['total_assets'],
                'market_value': balance['stock_value']
            }
        except Exception as e:
            logger.error(f"[ThsTrader] 查询账户失败: {e}")
            return {}

    def get_order_status(self, order_id: str) -> OrderStatus:
        # 同花顺easytrader暂不支持精确查询订单状态
        return OrderStatus.SUBMITTED


__all__ = [
    'TradeExecutor',
    'PaperTradingExecutor',
    'OKXExecutor',
    'QMTExecutor',
    'ThsTraderExecutor',
    'Order', 'OrderType', 'OrderSide', 'OrderStatus', 'Position'
]