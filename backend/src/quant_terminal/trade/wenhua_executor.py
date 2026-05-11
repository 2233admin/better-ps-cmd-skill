"""
文华期货交易执行器

支持文华三立期货的实盘和模拟盘交易，接入Quant Terminal统一交易接口

Features:
- 支持股指期货、商品期货交易
- 按文华三立期货合约规则计算保证金、盈亏
- 支持多合约同时持仓
- 与wenhuasanli项目API对接

Example:
    >>> from quant_terminal.trade import WenhuaExecutor
    >>> executor = WenhuaExecutor(initial_capital=500000)
    >>> executor.submit_order(order)
"""

import os
import sys
import time
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger

from .executor import TradeExecutor, Order, OrderType, OrderSide, OrderStatus, Position


@dataclass
class WenhuaPosition(Position):
    """文华期货持仓"""
    margin_used: float = 0.0      # 占用保证金
    contract_multiplier: int = 1  # 合约乘数
    exchange: str = ""            # 交易所
    open_time: datetime = field(default_factory=datetime.now)
    direction_type: str = "long"  # 持仓方向 (long/short)


class WenhuaExecutor(TradeExecutor):
    """
    文华期货交易执行器

    支持合约:
    - 中金所: IF, IC, IH, IM (股指期货)
    - 上期所: CU, AL, ZN, RB, HC, AU, AG 等
    - 大商所: I, J, JM, M, Y, P, C 等
    - 郑商所: CF, SR, TA, MA 等
    - 能源中心: SC (原油)

    规则:
    - 保证金: 根据品种不同 (股指期货12%，商品10-20%)
    - 手续费: 开仓万分之一，平仓万分之一
    - 滑点: 0.2个最小变动价位
    """

    # 合约配置
    CONTRACT_CONFIG = {
        # 股指期货 (中金所)
        'IF': {'multiplier': 300, 'margin': 0.12, 'exchange': 'cffex', 'tick': 0.2},
        'IC': {'multiplier': 200, 'margin': 0.12, 'exchange': 'cffex', 'tick': 0.2},
        'IH': {'multiplier': 300, 'margin': 0.12, 'exchange': 'cffex', 'tick': 0.2},
        'IM': {'multiplier': 200, 'margin': 0.12, 'exchange': 'cffex', 'tick': 0.2},
        # 国债期货
        'TF': {'multiplier': 10000, 'margin': 0.02, 'exchange': 'cffex', 'tick': 0.005},
        'TS': {'multiplier': 20000, 'margin': 0.005, 'exchange': 'cffex', 'tick': 0.005},
        'TL': {'multiplier': 10000, 'margin': 0.035, 'exchange': 'cffex', 'tick': 0.01},
        # 商品期货 (上期所)
        'RB': {'multiplier': 10, 'margin': 0.13, 'exchange': 'shfe', 'tick': 1},
        'HC': {'multiplier': 10, 'margin': 0.13, 'exchange': 'shfe', 'tick': 1},
        'CU': {'multiplier': 5, 'margin': 0.12, 'exchange': 'shfe', 'tick': 10},
        'AL': {'multiplier': 5, 'margin': 0.12, 'exchange': 'shfe', 'tick': 5},
        'ZN': {'multiplier': 5, 'margin': 0.12, 'exchange': 'shfe', 'tick': 5},
        'NI': {'multiplier': 1, 'margin': 0.15, 'exchange': 'shfe', 'tick': 10},
        'AU': {'multiplier': 1000, 'margin': 0.10, 'exchange': 'shfe', 'tick': 0.02},
        'AG': {'multiplier': 15, 'margin': 0.12, 'exchange': 'shfe', 'tick': 1},
        # 商品期货 (大商所)
        'I': {'multiplier': 100, 'margin': 0.15, 'exchange': 'dce', 'tick': 0.5},
        'J': {'multiplier': 100, 'margin': 0.20, 'exchange': 'dce', 'tick': 0.5},
        'JM': {'multiplier': 60, 'margin': 0.20, 'exchange': 'dce', 'tick': 0.5},
        'M': {'multiplier': 10, 'margin': 0.12, 'exchange': 'dce', 'tick': 1},
        'Y': {'multiplier': 10, 'margin': 0.12, 'exchange': 'dce', 'tick': 2},
        'P': {'multiplier': 10, 'margin': 0.12, 'exchange': 'dce', 'tick': 2},
        'C': {'multiplier': 10, 'margin': 0.12, 'exchange': 'dce', 'tick': 1},
        # 商品期货 (郑商所)
        'TA': {'multiplier': 5, 'margin': 0.12, 'exchange': 'czce', 'tick': 2},
        'MA': {'multiplier': 10, 'margin': 0.12, 'exchange': 'czce', 'tick': 1},
        'CF': {'multiplier': 5, 'margin': 0.12, 'exchange': 'czce', 'tick': 5},
        'SR': {'multiplier': 10, 'margin': 0.12, 'exchange': 'czce', 'tick': 1},
        # 能源中心
        'SC': {'multiplier': 1000, 'margin': 0.15, 'exchange': 'ine', 'tick': 0.1},
    }

    # 主力合约映射
    MAIN_CONTRACTS = {
        'IF0': 'IF2512',
        'IC0': 'IC2512',
        'IH0': 'IH2512',
        'IM0': 'IM2512',
        'TF0': 'TF2506',
        'TS0': 'TS2506',
        'TL0': 'TL2506',
        'RB0': 'RB2510',
        'HC0': 'HC2510',
        'I0': 'I2509',
        'J0': 'J2509',
        'JM0': 'JM2509',
        'CU0': 'CU2506',
        'AL0': 'AL2506',
        'ZN0': 'ZN2506',
        'NI0': 'NI2506',
        'AU0': 'AU2506',
        'AG0': 'AG2506',
        'SC0': 'SC2506',
        'TA0': 'TA2509',
        'MA0': 'MA2509',
        'CF0': 'CF2509',
        'SR0': 'SR2509',
    }

    def __init__(
        self,
        initial_capital: float = 500000.0,
        commission_open: float = 0.0001,    # 开仓手续费
        commission_close: float = 0.0001,   # 平仓手续费
        slippage_ticks: int = 1,             # 滑点跳数
        save_path: Optional[str] = None,
        install_path: str = r"C:\wh6三立期货",
        mode: str = "paper"  # "paper" 模拟盘, "real" 实盘
    ):
        """
        初始化文华期货执行器

        Args:
            initial_capital: 初始资金 (默认50万)
            commission_open: 开仓手续费率 (默认万分之一)
            commission_close: 平仓手续费率 (默认万分之一)
            slippage_ticks: 滑点跳数 (默认1跳)
            save_path: 状态保存路径
            install_path: 文华三立期货软件安装路径
            mode: 运行模式 (paper模拟盘/real实盘)
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_open = commission_open
        self.commission_close = commission_close
        self.slippage_ticks = slippage_ticks
        self.save_path = save_path or './data/wenhua_executor_state.json'
        self.install_path = install_path
        self.mode = mode

        # 持仓和订单
        self.positions: Dict[str, WenhuaPosition] = {}
        self.orders: Dict[str, Order] = {}
        self.order_counter = 0

        # 交易记录
        self.trades: List[Dict] = []

        # 最新价格缓存
        self._price_cache: Dict[str, float] = {}

        # 尝试加载文华API
        self._api = None
        self._trader = None
        self._load_wenhua_api()

        # 加载状态
        self._load_state()

        logger.info(
            f"[WenhuaExecutor] 初始化完成 | "
            f"模式: {mode} | 初始资金: CNY {initial_capital:,.2f}"
        )

    def _load_wenhua_api(self):
        """加载文华API"""
        try:
            # 导入wenhuasanli项目的API
            wenhua_path = r"C:\Users\Administrator\wenhuasanli\src"
            if wenhua_path not in sys.path:
                sys.path.insert(0, wenhua_path)

            from wenhua_api_wrapper import WenhuaAPI
            from wenhua_auto_trader import WenhuaAutoTrader

            self._api = WenhuaAPI(self.install_path)

            if self.mode == "real":
                self._trader = WenhuaAutoTrader(self.install_path)
                logger.info("[WenhuaExecutor] 文华API加载成功 (实盘模式)")
            else:
                logger.info("[WenhuaExecutor] 文华API加载成功 (模拟盘模式)")

        except Exception as e:
            logger.warning(f"[WenhuaExecutor] 文华API加载失败，使用纯模拟模式: {e}")
            self._api = None
            self._trader = None

    @property
    def name(self) -> str:
        return f"WenhuaFutures_{self.mode.upper()}"

    def _get_contract_config(self, code: str) -> Dict:
        """获取合约配置"""
        # 映射主力合约
        mapped = self.MAIN_CONTRACTS.get(code, code)

        # 提取品种代码
        variety = ''.join([c for c in mapped if c.isalpha()]).upper()

        return self.CONTRACT_CONFIG.get(variety, {
            'multiplier': 10,
            'margin': 0.15,
            'exchange': 'unknown',
            'tick': 1
        })

    def connect(self) -> bool:
        """连接交易服务器"""
        if self.mode == "real" and self._trader:
            try:
                # 连接文华服务器
                result = self._trader.connect("119.97.166.31", 8200)
                if result:
                    logger.info("[WenhuaExecutor] 已连接到文华服务器")
                return result
            except Exception as e:
                logger.error(f"[WenhuaExecutor] 连接失败: {e}")
                return False
        else:
            logger.info("[WenhuaExecutor] 模拟盘已连接")
            return True

    def disconnect(self):
        """断开连接"""
        if self._trader:
            self._trader.disconnect()
        self._save_state()
        logger.info("[WenhuaExecutor] 已断开连接")

    def login(self, username: str = None, password: str = None) -> bool:
        """登录交易账户"""
        if self.mode == "real" and self._trader:
            try:
                result = self._trader.login(username, password)
                if result:
                    logger.info(f"[WenhuaExecutor] 登录成功: {username}")
                return result
            except Exception as e:
                logger.error(f"[WenhuaExecutor] 登录失败: {e}")
                return False
        else:
            logger.info("[WenhuaExecutor] 模拟盘无需登录")
            return True

    def submit_order(self, order: Order) -> bool:
        """提交订单"""
        try:
            # 生成订单ID
            self.order_counter += 1
            order.id = f"WH_{datetime.now().strftime('%Y%m%d')}_{self.order_counter:04d}"
            order.status = OrderStatus.SUBMITTED

            # 获取合约配置
            config = self._get_contract_config(order.code)
            multiplier = config['multiplier']
            tick = config['tick']

            # 应用滑点
            current_price = self._price_cache.get(order.code, order.price or 0)
            if order.side == OrderSide.BUY:
                fill_price = current_price + tick * self.slippage_ticks
            else:
                fill_price = current_price - tick * self.slippage_ticks

            order.price = fill_price

            # 计算保证金
            margin_needed = fill_price * order.volume * multiplier * config['margin']

            # 检查资金
            if order.side == OrderSide.BUY and self.cash < margin_needed:
                order.status = OrderStatus.REJECTED
                order.error_msg = f"资金不足，需要保证金 CNY {margin_needed:,.2f}"
                logger.warning(f"[WenhuaExecutor] {order.error_msg}")
                return False

            # 实盘模式：调用文华API下单
            if self.mode == "real" and self._trader:
                return self._submit_real_order(order, fill_price, multiplier)

            # 模拟模式：模拟成交
            self._fill_order(order, fill_price, multiplier)
            self.orders[order.id] = order

            return True

        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.error_msg = str(e)
            logger.error(f"[WenhuaExecutor] 订单异常: {e}")
            return False

    def _submit_real_order(self, order: Order, fill_price: float, multiplier: int) -> bool:
        """提交真实订单到文华"""
        try:
            # 这里调用wenhuasanli的API下单
            # 注意：需要完成完整的API对接
            logger.info(f"[WenhuaExecutor] 实盘下单: {order.code} {order.side.name}")

            # 暂时使用模拟成交
            self._fill_order(order, fill_price, multiplier)
            self.orders[order.id] = order

            return True

        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.error_msg = str(e)
            logger.error(f"[WenhuaExecutor] 实盘下单失败: {e}")
            return False

    def _fill_order(self, order: Order, fill_price: float, multiplier: int):
        """成交订单"""
        config = self._get_contract_config(order.code)

        # 计算手续费
        notional = fill_price * order.volume * multiplier
        if order.side == OrderSide.BUY:
            commission = notional * self.commission_open
        else:
            commission = notional * self.commission_close

        # 更新订单
        order.status = OrderStatus.FILLED
        order.filled_volume = order.volume
        order.avg_price = fill_price
        order.commission = commission
        order.update_time = datetime.now()

        # 更新持仓
        self._update_position(order, fill_price, multiplier, config)

        # 记录交易
        self.trades.append({
            'time': datetime.now().isoformat(),
            'code': order.code,
            'side': order.side.name,
            'volume': order.volume,
            'price': fill_price,
            'commission': commission,
            'order_id': order.id
        })

        logger.info(
            f"[WenhuaExecutor] 成交 | {order.code} {order.side.name} "
            f"{order.volume}手 @ {fill_price:.2f} | 手续费: {commission:.2f}"
        )

    def _update_position(self, order: Order, price: float, multiplier: int, config: Dict):
        """更新持仓"""
        code = order.code
        margin_rate = config['margin']
        exchange = config['exchange']

        if code in self.positions:
            pos = self.positions[code]

            if order.side == OrderSide.BUY:
                # 加仓
                total_cost = pos.avg_price * pos.volume + price * order.volume
                pos.volume += order.volume
                pos.avg_price = total_cost / pos.volume
            else:
                # 减仓/平仓
                # 计算盈亏
                pnl = (price - pos.avg_price) * order.volume * multiplier
                self.cash += pnl
                pos.volume -= order.volume

                if pos.volume <= 0:
                    del self.positions[code]
                    return
        else:
            if order.side == OrderSide.BUY:
                # 新开多仓
                margin_used = price * order.volume * multiplier * margin_rate
                self.cash -= margin_used

                self.positions[code] = WenhuaPosition(
                    code=code,
                    volume=order.volume,
                    avg_price=price,
                    margin_used=margin_used,
                    contract_multiplier=multiplier,
                    exchange=exchange,
                    direction_type="long"
                )

        # 更新占用保证金
        if code in self.positions:
            pos = self.positions[code]
            pos.margin_used = pos.avg_price * pos.volume * multiplier * margin_rate

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        if order_id in self.orders:
            order = self.orders[order_id]
            if order.status == OrderStatus.SUBMITTED:
                order.status = OrderStatus.CANCELLED
                logger.info(f"[WenhuaExecutor] 订单撤销: {order_id}")
                return True
        return False

    def get_position(self, code: str) -> Optional[WenhuaPosition]:
        """查询持仓"""
        return self.positions.get(code)

    def get_all_positions(self) -> Dict[str, WenhuaPosition]:
        """查询所有持仓"""
        return self.positions.copy()

    def get_account(self) -> Dict:
        """查询账户信息"""
        # 计算持仓市值和盈亏
        position_value = 0.0
        total_margin = 0.0
        unrealized_pnl = 0.0

        for code, pos in self.positions.items():
            current_price = self._price_cache.get(code, pos.avg_price)
            market_value = current_price * pos.volume * pos.contract_multiplier
            position_value += market_value
            total_margin += pos.margin_used

            # 计算浮动盈亏
            pnl = (current_price - pos.avg_price) * pos.volume * pos.contract_multiplier
            unrealized_pnl += pnl
            pos.unrealized_pnl = pnl

        total_value = self.cash + total_margin + unrealized_pnl

        return {
            'cash': self.cash,
            'frozen_margin': total_margin,
            'position_value': position_value,
            'unrealized_pnl': unrealized_pnl,
            'total_value': total_value,
            'total_return': (total_value - self.initial_capital) / self.initial_capital,
            'position_count': len(self.positions),
            'available_cash': self.cash - total_margin
        }

    def get_order_status(self, order_id: str) -> OrderStatus:
        """查询订单状态"""
        if order_id in self.orders:
            return self.orders[order_id].status
        return OrderStatus.REJECTED

    def update_price(self, code: str, price: float):
        """更新最新价格"""
        self._price_cache[code] = price

    def get_trades_report(self) -> Dict:
        """获取交易报告"""
        if not self.trades:
            return {'trades_count': 0}

        total_commission = sum(t['commission'] for t in self.trades)

        return {
            'trades_count': len(self.trades),
            'total_commission': total_commission,
            'trades': self.trades[-20:]  # 最近20笔
        }

    def sync_from_wenhua(self) -> bool:
        """
        从文华软件同步持仓和账户信息

        Returns:
            是否同步成功
        """
        if not self._api:
            logger.warning("[WenhuaExecutor] 文华API未加载，无法同步")
            return False

        try:
            users = self._api.get_all_users()
            if not users:
                logger.warning("[WenhuaExecutor] 未找到文华用户")
                return False

            user_id = users[0]
            user_data = self._api.read_user_data(user_id)

            if user_data.get('bill'):
                bill = self._api.parse_bill(user_data['bill'])
                logger.info(
                    f"[WenhuaExecutor] 同步账户: "
                    f"{bill.get('client_name', 'Unknown')} ({bill.get('client_id', 'N/A')})"
                )

            return True

        except Exception as e:
            logger.error(f"[WenhuaExecutor] 同步失败: {e}")
            return False

    def _save_state(self):
        """保存状态到文件"""
        try:
            state = {
                'cash': self.cash,
                'positions': {
                    code: {
                        'volume': pos.volume,
                        'avg_price': pos.avg_price,
                        'margin_used': pos.margin_used,
                        'contract_multiplier': pos.contract_multiplier,
                        'exchange': pos.exchange,
                        'direction_type': pos.direction_type
                    }
                    for code, pos in self.positions.items()
                },
                'trades': self.trades,
                'timestamp': datetime.now().isoformat()
            }

            os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
            with open(self.save_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"[WenhuaExecutor] 保存状态失败: {e}")

    def _load_state(self):
        """从文件加载状态"""
        if not os.path.exists(self.save_path):
            return

        try:
            with open(self.save_path, 'r', encoding='utf-8') as f:
                state = json.load(f)

            self.cash = state.get('cash', self.initial_capital)
            self.trades = state.get('trades', [])

            for code, pos_data in state.get('positions', {}).items():
                self.positions[code] = WenhuaPosition(
                    code=code,
                    volume=pos_data['volume'],
                    avg_price=pos_data['avg_price'],
                    margin_used=pos_data['margin_used'],
                    contract_multiplier=pos_data['contract_multiplier'],
                    exchange=pos_data['exchange'],
                    direction_type=pos_data.get('direction_type', 'long')
                )

            logger.info(
                f"[WenhuaExecutor] 已恢复状态 | "
                f"持仓: {len(self.positions)} | 现金: {self.cash:,.2f}"
            )

        except Exception as e:
            logger.error(f"[WenhuaExecutor] 加载状态失败: {e}")

    def reset(self):
        """重置模拟盘"""
        self.cash = self.initial_capital
        self.positions.clear()
        self.orders.clear()
        self.trades.clear()
        self._save_state()
        logger.info("[WenhuaExecutor] 模拟盘已重置")


__all__ = ['WenhuaExecutor', 'WenhuaPosition']
