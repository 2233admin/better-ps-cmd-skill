
"""
Quant Terminal - 生产级量化交易系统

A股日内 + 加密货币量化交易系统。
基于FastAPI构建，支持多策略并行运行、实时回测和AI预测。
"""

__version__ = "0.1.0"
__author__ = "Quant Terminal Team"

from .core.backtest import BacktestEngine, BacktestResult
from .core.portfolio import Portfolio, Position
from .core.metrics import calculate_sharpe, calculate_drawdown, calculate_returns

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "Portfolio",
    "Position",
    "calculate_sharpe",
    "calculate_drawdown",
    "calculate_returns",
]
