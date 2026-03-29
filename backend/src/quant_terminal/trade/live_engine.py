"""
实盘交易引擎

整合行情订阅、策略执行、订单管理的完整实盘系统

Example:
    >>> from quant_terminal.trade import LiveEngine, PaperTradingExecutor
    >>> from quant_terminal.strategies import DualThrustStrategy
    >>>
    >>> executor = PaperTradingExecutor(initial_capital=1_000_000)
    >>> strategy = DualThrustStrategy()
    >>>
    >>> engine = LiveEngine(executor, strategy)
    >>> engine.run()  # 开始实盘
"""

import threading
import time
from typing import Dict, Optional, Callable, List
from dataclasses import dataclass
from datetime import datetime
from loguru import logger

from .executor import TradeExecutor, Order, OrderType, OrderSide, PaperTradingExecutor
from ..strategies.base import Strategy, Signal, SignalType
from ..core.portfolio import Portfolio, PortfolioConfig


@dataclass
class LiveConfig:
    """实盘配置"""
    mode: str = "paper"           # paper, okx, qmt, ths
    interval: float = 1.0         # 轮询间隔(秒)
    auto_trading: bool = True     # 自动交易开关
    max_positions: int = 10       # 最大持仓数
    risk_per_trade: float = 0.02  # 单笔风险比例
    daily_loss_limit: float = 0.05  # 日亏损限制


class LiveEngine:
    """
    实盘交易引擎

    主要功能:
    1. 订阅实时行情
    2. 策略信号生成
    3. 订单执行管理
    4. 风险控制监控
    5. 状态实时汇报
    """

    def __init__(
        self,
        executor: TradeExecutor,
        strategy: Strategy,
        config: Optional[LiveConfig] = None,
        data_provider = None
    ):
        """
        初始化实盘引擎

        Args:
            executor: 交易执行器 (模拟/OKX/QMT/同花顺)
            strategy: 交易策略
            config: 实盘配置
            data_provider: 数据提供者 (用于获取实时行情)
        """
        self.executor = executor
        self.strategy = strategy
        self.config = config or LiveConfig()
        self.data_provider = data_provider

        # 内部状态
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 持仓和订单管理
        self.positions: Dict[str, dict] = {}
        self.pending_orders: Dict[str, Order] = {}
        self.signal_history: List[dict] = []

        # 日统计
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.last_date = datetime.now().date()

        # 回调函数
        self.on_signal: Optional[Callable] = None
        self.on_order: Optional[Callable] = None
        self.on_fill: Optional[Callable] = None

    def run(self):
        """启动实盘交易"""
        if self.is_running:
            logger.warning("[LiveEngine] 引擎已在运行中")
            return

        # 连接交易通道
        if not self.executor.connect():
            logger.error("[LiveEngine] 交易通道连接失败")
            return

        logger.info(f"[LiveEngine] 实盘引擎启动，模式: {self.executor.name}")
        logger.info(f"[LiveEngine] 策略: {self.strategy.name}")

        self.is_running = True
        self._stop_event.clear()

        # 启动交易线程
        self._thread = threading.Thread(target=self._trading_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止实盘交易"""
        if not self.is_running:
            return

        logger.info("[LiveEngine] 正在停止实盘引擎...")
        self._stop_event.set()
        self._thread.join(timeout=5.0)
        self.executor.disconnect()
        self.is_running = False
        logger.info("[LiveEngine] 实盘引擎已停止")

    def _trading_loop(self):
        """交易主循环"""
        while not self._stop_event.is_set():
            try:
                # 检查日切
                self._check_day_change()

                # 更新账户和持仓信息
                self._update_account_info()

                # 获取实时行情并生成信号
                if self.data_provider:
                    self._process_signals()

                # 更新订单状态
                self._update_orders()

                # 检查风控
                if self._check_risk():
                    logger.warning("[LiveEngine] 触发风控，暂停交易")
                    time.sleep(60)  # 暂停1分钟
                    continue

                # 汇报状态
                self._report_status()

            except Exception as e:
                logger.error(f"[LiveEngine] 交易循环异常: {e}")

            # 等待下一轮
            self._stop_event.wait(self.config.interval)

    def _check_day_change(self):
        """检查日切，重置日统计"""
        current_date = datetime.now().date()
        if current_date != self.last_date:
            logger.info(f"[LiveEngine] 日切: {self.last_date} -> {current_date}")
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.last_date = current_date

    def _update_account_info(self):
        """更新账户信息"""
        try:
            account = self.executor.get_account()
            positions = self.executor.get_all_positions()

            self.positions = {
                code: {
                    'volume': pos.volume,
                    'avg_price': pos.avg_price,
                    'unrealized_pnl': pos.unrealized_pnl
                }
                for code, pos in positions.items()
            }

        except Exception as e:
            logger.error(f"[LiveEngine] 更新账户信息失败: {e}")

    def _process_signals(self):
        """处理策略信号"""
        try:
            # 获取实时数据
            # 这里简化处理，实际应从data_provider获取实时tick/bar
            # data = self.data_provider.fetch(...)
            # signals = self.strategy.generate_signals(data)

            # 模拟信号处理流程
            pass

        except Exception as e:
            logger.error(f"[LiveEngine] 信号处理失败: {e}")

    def on_bar(self, code: str, bar: dict):
        """
        收到新K线时的回调

        Args:
            code: 品种代码
            bar: K线数据 {'open': ..., 'high': ..., 'low': ..., 'close': ..., 'volume': ...}
        """
        if not self.is_running or not self.config.auto_trading:
            return

        try:
            # 更新模拟盘价格
            if isinstance(self.executor, PaperTradingExecutor):
                self.executor.update_price(code, bar['close'])

            # 策略生成信号
            signal = self.strategy.on_bar(bar)

            if signal:
                self._handle_signal(code, signal, bar['close'])

        except Exception as e:
            logger.error(f"[LiveEngine] 处理K线失败: {e}")

    def on_tick(self, code: str, tick: dict):
        """
        收到新Tick时的回调

        Args:
            code: 品种代码
            tick: Tick数据 {'price': ..., 'volume': ..., 'bid': ..., 'ask': ...}
        """
        if not self.is_running or not self.config.auto_trading:
            return

        try:
            # 更新价格
            if isinstance(self.executor, PaperTradingExecutor):
                self.executor.update_price(code, tick['price'])

            # 高频策略处理
            signal = self.strategy.on_tick(tick)

            if signal:
                self._handle_signal(code, signal, tick['price'])

        except Exception as e:
            logger.error(f"[LiveEngine] 处理Tick失败: {e}")

    def _handle_signal(self, code: str, signal: Signal, current_price: float):
        """处理交易信号"""
        logger.info(f"[LiveEngine] 收到信号: {code} {signal.signal_type.name} @ {signal.price}")

        # 记录信号
        self.signal_history.append({
            'time': datetime.now(),
            'code': code,
            'signal': signal.signal_type.name,
            'price': signal.price
        })

        # 执行回调
        if self.on_signal:
            self.on_signal(signal)

        # 检查是否可以交易
        if not self._can_trade(code, signal):
            return

        # 计算仓位
        position_size = self._calculate_position_size(code, signal, current_price)

        if position_size <= 0:
            return

        # 发送订单
        self._send_order(code, signal.signal_type, position_size, signal.price)

    def _can_trade(self, code: str, signal: Signal) -> bool:
        """检查是否可以交易"""
        # 检查自动交易开关
        if not self.config.auto_trading:
            return False

        # 检查持仓数量限制
        if len(self.positions) >= self.config.max_positions:
            if code not in self.positions:
                logger.warning(f"[LiveEngine] 持仓数量已达上限: {self.config.max_positions}")
                return False

        return True

    def _calculate_position_size(self, code: str, signal: Signal, price: float) -> int:
        """计算仓位大小"""
        try:
            account = self.executor.get_account()
            total_value = account.get('total_value', 0)

            # 简单固定仓位计算 (可根据策略调整)
            risk_amount = total_value * self.config.risk_per_trade
            position_value = risk_amount * 5  # 5倍杠杆因子

            volume = int(position_value / price)

            # 确保至少1手
            return max(volume, 1)

        except Exception as e:
            logger.error(f"[LiveEngine] 仓位计算失败: {e}")
            return 0

    def _send_order(self, code: str, signal_type: SignalType, volume: int, price: float):
        """发送订单"""
        try:
            # 确定买卖方向
            if signal_type == SignalType.BUY:
                side = OrderSide.BUY
            elif signal_type == SignalType.SELL:
                side = OrderSide.SELL
            else:
                return

            # 确定订单类型 (默认限价单)
            order_type = OrderType.LIMIT

            # 创建订单
            order = Order(
                id="",  # 由执行器分配
                code=code,
                side=side,
                type=order_type,
                volume=volume,
                price=price
            )

            # 提交订单
            success = self.executor.submit_order(order)

            if success:
                self.pending_orders[order.id] = order
                self.daily_trades += 1

                logger.info(f"[LiveEngine] 订单已提交: {order.id} {code} {side.name} {volume}@{price}")

                if self.on_order:
                    self.on_order(order)
            else:
                logger.error(f"[LiveEngine] 订单提交失败: {order.error_msg}")

        except Exception as e:
            logger.error(f"[LiveEngine] 发送订单失败: {e}")

    def _update_orders(self):
        """更新订单状态"""
        for order_id, order in list(self.pending_orders.items()):
            try:
                status = self.executor.get_order_status(order_id)

                if status != order.status:
                    order.status = status
                    logger.info(f"[LiveEngine] 订单状态更新: {order_id} -> {status.value}")

                    if status == OrderStatus.FILLED:
                        if self.on_fill:
                            self.on_fill(order)
                        del self.pending_orders[order_id]

                    elif status in [OrderStatus.CANCELLED, OrderStatus.REJECTED]:
                        del self.pending_orders[order_id]

            except Exception as e:
                logger.error(f"[LiveEngine] 更新订单状态失败: {e}")

    def _check_risk(self) -> bool:
        """检查风控"""
        # 日亏损限制
        if self.daily_pnl < -self.config.daily_loss_limit * self.executor.get_account().get('total_value', 1):
            logger.warning(f"[LiveEngine] 日亏损超限: {self.daily_pnl}")
            return True

        return False

    def _report_status(self):
        """汇报状态"""
        try:
            account = self.executor.get_account()
            positions = self.executor.get_all_positions()

            # 每60秒汇报一次
            if int(time.time()) % 60 == 0:
                logger.info(
                    f"[LiveEngine] 状态 | "
                    f"总资产: {account.get('total_value', 0):,.2f} | "
                    f"持仓: {len(positions)} | "
                    f"当日成交: {self.daily_trades}"
                )

        except Exception as e:
            logger.error(f"[LiveEngine] 状态汇报失败: {e}")

    def get_status(self) -> dict:
        """获取引擎状态"""
        return {
            'is_running': self.is_running,
            'mode': self.executor.name,
            'strategy': self.strategy.name,
            'positions': len(self.positions),
            'pending_orders': len(self.pending_orders),
            'daily_pnl': self.daily_pnl,
            'daily_trades': self.daily_trades,
            'signals_today': len([s for s in self.signal_history
                                  if s['time'].date() == datetime.now().date()])
        }

    def manual_order(self, code: str, side: str, volume: int, price: Optional[float] = None):
        """
        手动下单

        Args:
            code: 品种代码
            side: 'buy' 或 'sell'
            volume: 数量
            price: 价格 (None为市价单)
        """
        from .executor import OrderType

        order = Order(
            id="",
            code=code,
            side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
            type=OrderType.LIMIT if price else OrderType.MARKET,
            volume=volume,
            price=price
        )

        success = self.executor.submit_order(order)
        if success:
            self.pending_orders[order.id] = order
            logger.info(f"[LiveEngine] 手动订单已提交: {order.id}")
        return success


__all__ = ['LiveEngine', 'LiveConfig']
