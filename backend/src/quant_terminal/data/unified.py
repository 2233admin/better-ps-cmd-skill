"""
统一资产数据加载器

根据代码自动识别资产类型并加载数据

支持的资产类型:
- A股: 6位数字代码 (如 '000001', '600000')
- 期货: 字母+数字 (如 'IF0', 'IC0', 'IH0')
- 加密货币: 交易对格式 (如 'BTC-USDT', 'ETH-USDT')

Example:
    >>> from quant_terminal.data import UnifiedAssetLoader
    >>> loader = UnifiedAssetLoader()
    >>> df = loader.load('000001')  # A股
    >>> df = loader.load('IF0')     # 期货
    >>> df = loader.load('BTC')     # 加密货币
"""

import os
from typing import Optional, Union
import polars as pl

from .base import DataConfig
from .stocks import StockDataProvider
from .futures import FuturesDataProvider
from .crypto import CryptoDataProvider


class UnifiedAssetLoader:
    """
    统一资产加载器

    自动识别资产类型并加载对应数据
    """

    # 资产类型定义
    ASSET_STOCK = 'stock'      # A股
    ASSET_FUTURES = 'futures'  # 期货
    ASSET_CRYPTO = 'crypto'    # 加密货币

    def __init__(self, data_dir: str = './data'):
        """
        初始化加载器

        Args:
            data_dir: 数据根目录
        """
        self.data_dir = data_dir

        # 确保各资产目录存在
        for subdir in ['stocks', 'futures', 'crypto']:
            os.makedirs(os.path.join(data_dir, subdir), exist_ok=True)

        # 初始化各提供者
        self._stock_provider = StockDataProvider(
            DataConfig(cache_dir=data_dir, use_cache=True)
        )
        self._futures_provider = FuturesDataProvider(
            DataConfig(cache_dir=data_dir, use_cache=True)
        )
        self._crypto_provider = CryptoDataProvider(
            DataConfig(cache_dir=data_dir, use_cache=True)
        )

    def detect_asset_type(self, code: str) -> str:
        """
        检测资产类型

        Args:
            code: 资产代码

        Returns:
            资产类型: 'stock', 'futures', 'crypto'
        """
        code = code.strip().upper()

        # A股: 6位纯数字
        if code.isdigit() and len(code) == 6:
            return self.ASSET_STOCK

        # 加密货币: 包含 '-' 或常见币种代码
        if '-' in code or code in ['BTC', 'ETH', 'SOL', 'DOGE', 'XRP', 'ADA']:
            return self.ASSET_CRYPTO

        # 期货: 字母开头 (IF, IC, IH, IM 等)
        if code[:2].isalpha() and code[0].isalpha():
            return self.ASSET_FUTURES

        # 默认尝试股票
        return self.ASSET_STOCK

    def load(
        self,
        code: str,
        timeframe: str = 'daily',
        start: Optional[str] = None,
        end: Optional[str] = None,
        asset_type: Optional[str] = None
    ) -> pl.DataFrame:
        """
        加载资产数据

        Args:
            code: 资产代码
            timeframe: 周期 (daily, 1h, etc.)
            start: 开始日期 (YYYYMMDD)
            end: 结束日期 (YYYYMMDD)
            asset_type: 强制指定资产类型，None则自动识别

        Returns:
            OHLCV数据
        """
        # 检测资产类型
        if asset_type is None:
            asset_type = self.detect_asset_type(code)

        # 根据类型加载
        if asset_type == self.ASSET_STOCK:
            return self._load_stock(code, timeframe, start, end)
        elif asset_type == self.ASSET_FUTURES:
            return self._load_futures(code, timeframe, start, end)
        elif asset_type == self.ASSET_CRYPTO:
            return self._load_crypto(code, timeframe, start, end)
        else:
            raise ValueError(f"未知资产类型: {asset_type}")

    def _load_stock(
        self,
        code: str,
        timeframe: str,
        start: Optional[str],
        end: Optional[str]
    ) -> pl.DataFrame:
        """加载股票数据"""
        cache_path = os.path.join(self.data_dir, 'stocks', f"{code}_{timeframe}.parquet")

        if os.path.exists(cache_path):
            return self._stock_provider.load_from_cache(code, timeframe)

        # 下载并保存
        print(f"[UnifiedAssetLoader] 下载股票 {code} 数据...")
        from datetime import datetime
        start_dt = datetime.strptime(start, "%Y%m%d") if start else None
        end_dt = datetime.strptime(end, "%Y%m%d") if end else None

        self._stock_provider.fetch_and_save(code, timeframe, start_dt, end_dt)
        return self._stock_provider.load_from_cache(code, timeframe)

    def _load_futures(
        self,
        code: str,
        timeframe: str,
        start: Optional[str],
        end: Optional[str]
    ) -> pl.DataFrame:
        """加载期货数据"""
        cache_path = os.path.join(self.data_dir, 'futures', f"{code}_{timeframe}.parquet")

        if os.path.exists(cache_path):
            return self._futures_provider.load_from_cache(code, timeframe)

        # 下载并保存
        print(f"[UnifiedAssetLoader] 下载期货 {code} 数据...")
        from datetime import datetime
        start_dt = datetime.strptime(start, "%Y%m%d") if start else None
        end_dt = datetime.strptime(end, "%Y%m%d") if end else None

        self._futures_provider.fetch_and_save(code, timeframe, start_dt, end_dt)
        return self._futures_provider.load_from_cache(code, timeframe)

    def _load_crypto(
        self,
        code: str,
        timeframe: str,
        start: Optional[str],
        end: Optional[str]
    ) -> pl.DataFrame:
        """加载加密货币数据"""
        safe_code = code.replace('-', '_')
        cache_path = os.path.join(self.data_dir, 'crypto', f"{safe_code}_{timeframe}.parquet")

        if os.path.exists(cache_path):
            return self._crypto_provider.load_from_cache(code, timeframe)

        # 下载并保存
        print(f"[UnifiedAssetLoader] 下载加密货币 {code} 数据...")
        from datetime import datetime
        start_dt = datetime.strptime(start, "%Y%m%d") if start else None
        end_dt = datetime.strptime(end, "%Y%m%d") if end else None

        self._crypto_provider.fetch_and_save(code, timeframe, start_dt, end_dt)
        return self._crypto_provider.load_from_cache(code, timeframe)

    def list_cached_assets(self) -> dict:
        """
        列出已缓存的资产

        Returns:
            {'stocks': [...], 'futures': [...], 'crypto': [...]}
        """
        result = {
            'stocks': [],
            'futures': [],
            'crypto': []
        }

        for asset_type in result.keys():
            asset_dir = os.path.join(self.data_dir, asset_type)
            if os.path.exists(asset_dir):
                for f in os.listdir(asset_dir):
                    if f.endswith('.parquet'):
                        # 从文件名提取代码
                        code = f.replace('.parquet', '').rsplit('_', 1)[0]
                        result[asset_type].append(code)

        return result


def load_asset(
    code: str,
    timeframe: str = 'daily',
    data_dir: str = './data',
    **kwargs
) -> pl.DataFrame:
    """
    便捷函数: 加载任意资产数据

    Args:
        code: 资产代码
        timeframe: 周期
        data_dir: 数据目录
        **kwargs: 其他参数

    Returns:
        OHLCV数据

    Example:
        >>> # A股
        >>> df = load_asset('000001', data_dir='./data')
        >>> # 期货
        >>> df = load_asset('IF0', data_dir='./data')
        >>> # 加密货币
        >>> df = load_asset('BTC', timeframe='1h', data_dir='./data')
    """
    loader = UnifiedAssetLoader(data_dir)
    return loader.load(code, timeframe, **kwargs)
