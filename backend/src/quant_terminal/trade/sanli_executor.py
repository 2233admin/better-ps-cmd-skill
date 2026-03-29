"""
三立期货模拟盘执行器

支持三立期货合约规则的模拟盘交易

Features:
- 支持股指期货、商品期货
- 按三立期货合约规则计算保证金、盈亏
- 支持多合约同时持仓
- 支持从三立期货客户端抓取持仓同步

Example:
    >>> from quant_terminal.trade import SanliPaperExecutor
    >>> executor = SanliPaperExecutor(initial_capital=500000)
    >>> executor.submit_order('IF0', 'buy', 1, 4000)
"""

import os
import time
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger

from .executor import TradeExecutor, Order, OrderType, OrderSide, OrderStatus, Position


@dataclass
class SanliPosition(Position):
    """三立期货持仓"""
    margin_used: float = 0.0      # 占用保证金
    contract_multiplier: int = 1  # 合约乘数
    exchange: str = ""            # 交易所
    open_time: datetime = field(default_factory=datetime.now)


class SanliPaperExecutor(TradeExecutor):
    """
    三立期货模拟盘执行器

    支持合约:
    - 中金所: IF, IC, IH, IM (股指期货)
    - 上期所: CU, AL, ZN, RB, HC, AU, AG 等
    - 大商所: I, J, JM, M, Y, P, C 等
    - 郑商所: CF, SR, TA, MA 等
    - 能源中心: SC (原油)

    规则:
    - 保证金: 默认15%
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
        # 商品期货 (示例配置)
        'RB': {'multiplier': 10, 'margin': 0.13, 'exchange': 'shfe', 'tick': 1},
        'HC': {'multiplier': 10, 'margin': 0.13, 'exchange': 'shfe', 'tick': 1},
        'I': {'multiplier': 100, 'margin': 0.15, 'exchange': 'dce', 'tick': 0.5},
        'J': {'multiplier': 100, 'margin': 0.20, 'exchange': 'dce', 'tick': 0.5},
        'JM': {'multiplier': 60, 'margin': 0.20, 'exchange': 'dce', 'tick': 0.5},
        'CU': {'multiplier': 5, 'margin': 0.12, 'exchange': 'shfe', 'tick': 10},
        'AU': {'multiplier': 1000, 'margin': 0.10, 'exchange': 'shfe', 'tick': 0.02},
        'AG': {'multiplier': 15, 'margin': 0.12, 'exchange': 'shfe', 'tick': 1},
        'SC': {'multiplier': 1000, 'margin': 0.15, 'exchange': 'ine', 'tick': 0.1},
        'TA': {'multiplier': 5, 'margin': 0.12, 'exchange': 'czce', 'tick': 2},
        'MA': {'multiplier': 10, 'margin': 0.12, 'exchange': 'czce', 'tick': 1},
    }

    # 主力合约映射
    MAIN_CONTRACTS = {
        'IF0': 'IF2512',
        'IC0': 'IC2512',
        'IH0': 'IH2512',
        'IM0': 'IM2512',
        'RB0': 'RB2510',
        'HC0': 'HC2510',
        'I0': 'I2509',
        'J0': 'J2509',
        'JM0': 'JM2509',
        'CU0': 'CU2506',
        'AU0': 'AU2506',
        'AG0': 'AG2506',
        'SC0': 'SC2506',
        'TA0': 'TA2509',
        'MA0': 'MA2509',
    }

    def __init__(
        self,
        initial_capital: float = 500000.0,
        commission_open: float = 0.0001,    # 开仓手续费
        commission_close: float = 0.0001,   # 平仓手续费
        slippage_ticks: int = 1,             # 滑点跳数
        save_path: Optional[str] = None
    ):
        """
        初始化三立期货模拟盘

        Args:
            initial_capital: 初始资金 (默认50万)
            commission_open: 开仓手续费率 (默认万分之一)
            commission_close: 平仓手续费率 (默认万分之一)
            slippage_ticks: 滑点跳数 (默认1跳)
            save_path: 状态保存路径
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_open = commission_open
        self.commission_close = commission_close
        self.slippage_ticks = slippage_ticks
        self.save_path = save_path or './data/sanli_paper_state.json'

        # 持仓和订单
        self.positions: Dict[str, SanliPosition] = {}
        self.orders: Dict[str, Order] = {}
        self.order_counter = 0

        # 交易记录
        self.trades: List[Dict] = []

        # 最新价格缓存
        self._price_cache: Dict[str, float] = {}

        # 加载状态
        self._load_state()

        logger.info(f"[SanliPaper] 模拟盘初始化完成 | 初始资金: CNY {initial_capital:,.2f}")

    @property
    def name(self) -> str:
        return "SanliFutures_Paper"

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
        """连接模拟盘"""
        logger.info("[SanliPaper] 三立期货模拟盘已连接")
        return True

    def disconnect(self):
        """断开连接"""
        self._save_state()
        logger.info("[SanliPaper] 三立期货模拟盘已断开")

    def submit_order(self, order: Order) -> bool:
        """提交订单"""
        try:
            # 生成订单ID
            self.order_counter += 1
            order.id = f"SL_{datetime.now().strftime('%Y%m%d')}_{self.order_counter:04d}"
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
                logger.warning(f"[SanliPaper] {order.error_msg}")
                return False

            # 模拟成交
            self._fill_order(order, fill_price, multiplier)
            self.orders[order.id] = order

            return True

        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.error_msg = str(e)
            logger.error(f"[SanliPaper] 订单异常: {e}")
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
            f"[SanliPaper] 成交 | {order.code} {order.side.name} "
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

                self.positions[code] = SanliPosition(
                    code=code,
                    volume=order.volume,
                    avg_price=price,
                    margin_used=margin_used,
                    contract_multiplier=multiplier,
                    exchange=exchange
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
                logger.info(f"[SanliPaper] 订单撤销: {order_id}")
                return True
        return False

    def get_position(self, code: str) -> Optional[SanliPosition]:
        """查询持仓"""
        return self.positions.get(code)

    def get_all_positions(self) -> Dict[str, SanliPosition]:
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
                        'exchange': pos.exchange
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
            logger.error(f"[SanliPaper] 保存状态失败: {e}")

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
                self.positions[code] = SanliPosition(
                    code=code,
                    volume=pos_data['volume'],
                    avg_price=pos_data['avg_price'],
                    margin_used=pos_data['margin_used'],
                    contract_multiplier=pos_data['contract_multiplier'],
                    exchange=pos_data['exchange']
                )

            logger.info(f"[SanliPaper] 已恢复状态 | 持仓: {len(self.positions)} | 现金: {self.cash:,.2f}")

        except Exception as e:
            logger.error(f"[SanliPaper] 加载状态失败: {e}")

    def reset(self):
        """重置模拟盘"""
        self.cash = self.initial_capital
        self.positions.clear()
        self.orders.clear()
        self.trades.clear()
        self._save_state()
        logger.info("[SanliPaper] 模拟盘已重置")


__all__ = ['SanliPaperExecutor', 'SanliPosition']
