"""
数据模块

统一数据访问接口
"""

from .base import DataProvider, DataConfig
from .futures import FuturesDataProvider

__all__ = [
    "DataProvider",
    "DataConfig",
    "FuturesDataProvider",
]
