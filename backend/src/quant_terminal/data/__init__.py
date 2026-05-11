"""
数据模块

统一数据访问接口 - 支持A股、期货、加密货币

目录结构:
    data/
    ├── stocks/          # A股数据
    ├── futures/         # 期货数据
    └── crypto/          # 加密货币数据
"""

from .base import DataProvider, DataConfig
from .stocks import StockDataProvider, load_stock_for_backtest
from .futures import FuturesDataProvider, load_futures_for_backtest
from .crypto import CryptoDataProvider, load_crypto_for_backtest
from .sanli_futures import SanliFuturesProvider, load_sanli_for_backtest
from .wenhua_futures import WenhuaFuturesProvider, load_wenhua_for_backtest
from .unified import UnifiedAssetLoader, load_asset

__all__ = [
    "DataProvider",
    "DataConfig",
    # A股
    "StockDataProvider",
    "load_stock_for_backtest",
    # 期货
    "FuturesDataProvider",
    "load_futures_for_backtest",
    # 三立期货
    "SanliFuturesProvider",
    "load_sanli_for_backtest",
    # 文华期货
    "WenhuaFuturesProvider",
    "load_wenhua_for_backtest",
    # 加密货币
    "CryptoDataProvider",
    "load_crypto_for_backtest",
    # 统一加载
    "UnifiedAssetLoader",
    "load_asset",
]
