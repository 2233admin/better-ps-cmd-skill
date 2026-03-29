"""
实盘交易模块

提供完整的实盘交易能力:
- executor: 交易执行器 (模拟/OKX/QMT/同花顺)
- live_engine: 实盘交易引擎
- risk: 实时风控系统

Example:
    >>> from quant_terminal.trade import LiveEngine, PaperTradingExecutor
    >>> executor = PaperTradingExecutor()
    >>> engine = LiveEngine(executor, strategy)
    >>> engine.run()  # 启动实盘
"""

from .executor import (
    TradeExecutor,
    PaperTradingExecutor,
    OKXExecutor,
    QMTExecutor,
    ThsTraderExecutor,
    Order,
    OrderType,
    OrderSide,
    OrderStatus,
    Position
)
from .sanli_executor import SanliPaperExecutor, SanliPosition
from .live_engine import LiveEngine, LiveConfig

__all__ = [
    # 执行器
    "TradeExecutor",
    "PaperTradingExecutor",
    "OKXExecutor",
    "QMTExecutor",
    "ThsTraderExecutor",
    # 三立期货
    "SanliPaperExecutor",
    "SanliPosition",
    # 引擎
    "LiveEngine",
    "LiveConfig",
    # 数据类
    "Order",
    "OrderType",
    "OrderSide",
    "OrderStatus",
    "Position",
]