"""
策略模块

包含策略基类和具体策略实现
"""

from .base import Strategy, StrategyConfig, Signal, SignalType
from .dual_thrust import DualThrustStrategy, DualThrustConfig
from .rbreaker import RBreakerStrategy, RBreakerConfig

__all__ = [
    "Strategy",
    "StrategyConfig",
    "Signal",
    "SignalType",
    "DualThrustStrategy",
    "DualThrustConfig",
    "RBreakerStrategy",
    "RBreakerConfig",
]
