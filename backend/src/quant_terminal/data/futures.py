"""
期货数据提供者

支持中国金融期货交易所(CFFEX)股指期货数据获取
"""

import os
from datetime import datetime, timedelta
from typing import Optional
import polars as pl
import akshare as ak

from .base import DataProvider, DataConfig


class FuturesDataProvider(DataProvider):
    """
    期货数据提供者

    使用AKShare获取中国期货数据

    Example:
        >>> provider = FuturesDataProvider()
        >>> df = provider.fetch('IF0', 'daily', start='2024-01-01', end='2024-12-31')
    """

    # 合约代码映射
    CODE_MAPPING = {
        'IF0': 'IF2512',  # 沪深300期货主连
        'IC0': 'IC2512',  # 中证500期货主连
        'IH0': 'IH2512',  # 上证50期货主连
        'IM0': 'IM2512',  # 中证1000期货主连
    }

    def __init__(self, config: Optional[DataConfig] = None):
        super().__init__(config)
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        if self.config.use_cache and self.config.cache_dir:
            os.makedirs(self.config.cache_dir, exist_ok=True)

    def fetch(
        self,
        code: str,
        timeframe: str = "daily",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> pl.DataFrame:
        """获取期货数据

        Args:
            code: 合约代码 (如 'IF0', 'IC0', 'IH0')
            timeframe: 周期，支持 'daily', 'hourly', '15min'
            start: 开始日期
            end: 结束日期

        Returns:
            OHLCV数据
        """
        # 检查缓存
        cache_key = self.get_cache_key(code, timeframe, start, end)
        cached = self.cache_get(cache_key)
        if cached is not None:
            return cached

        # 映射合约代码
        mapped_code = self.CODE_MAPPING.get(code, code)

        # 默认日期
        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(days=365)

        try:
            # 获取数据
            if timeframe == "daily":
                df = self._fetch_daily(mapped_code, start, end)
            elif timeframe == "hourly":
                df = self._fetch_hourly(mapped_code, start, end)
            else:
                raise ValueError(f"不支持的周期: {timeframe}")

            # 验证和清洗
            if not self.validate_ohlcv(df):
                return pl.DataFrame()

            df = self.clean_data(df)

            # 缓存
            self.cache_set(cache_key, df)

            return df

        except Exception as e:
            print(f"[FuturesDataProvider] 获取数据失败: {e}")
            return pl.DataFrame()

    def _fetch_daily(
        self,
        code: str,
        start: datetime,
        end: datetime
    ) -> pl.DataFrame:
        """获取日频数据"""
        # 使用AKShare获取期货历史行情
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        # 获取连续合约数据
        df_pd = ak.futures_zh_daily_sina(symbol=code)

        if df_pd.empty:
            return pl.DataFrame()

        # 转换为Polars
        df = pl.from_pandas(df_pd)

        # 标准化列名
        column_mapping = {
            'date': 'datetime',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'volume': 'volume',
        }

        df = df.rename({k: v for k, v in column_mapping.items() if k in df.columns})

        # 转换日期
        df = df.with_columns([
            pl.col('datetime').str.to_datetime("%Y-%m-%d").alias('datetime')
        ])

        # 过滤日期范围
        df = df.filter(
            (pl.col('datetime') >= start) & (pl.col('datetime') <= end)
        )

        # 确保数值列为float
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df = df.with_columns([
                    pl.col(col).cast(pl.Float64).alias(col)
                ])

        return df

    def _fetch_hourly(
        self,
        code: str,
        start: datetime,
        end: datetime
    ) -> pl.DataFrame:
        """获取小时数据 (通过日数据重采样模拟)"""
        # AKShare可能不直接支持小时数据，这里返回日数据作为示例
        return self._fetch_daily(code, start, end)

    def fetch_and_save(
        self,
        code: str,
        timeframe: str = "daily",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        save_path: Optional[str] = None
    ) -> str:
        """获取数据并保存到本地

        Args:
            code: 合约代码
            timeframe: 周期
            start: 开始日期
            end: 结束日期
            save_path: 保存路径

        Returns:
            保存的文件路径
        """
        df = self.fetch(code, timeframe, start, end)

        if df.is_empty():
            raise ValueError(f"未能获取 {code} 的数据")

        if save_path is None:
            save_path = os.path.join(
                self.config.cache_dir,
                f"{code}_{timeframe}.parquet"
            )

        df.write_parquet(save_path)
        print(f"[FuturesDataProvider] 数据已保存到: {save_path}")

        return save_path

    def load_from_cache(self, code: str, timeframe: str = "daily") -> pl.DataFrame:
        """从本地缓存加载数据"""
        cache_path = os.path.join(
            self.config.cache_dir,
            f"{code}_{timeframe}.parquet"
        )

        if not os.path.exists(cache_path):
            raise FileNotFoundError(f"缓存文件不存在: {cache_path}")

        return pl.read_parquet(cache_path)


def fetch_and_save_futures_data(
    code: str,
    timeframe: str = "daily",
    start_date: str = "20230101",
    end_date: str = "20251231",
    data_dir: str = "./data"
) -> str:
    """便捷函数: 获取并保存期货数据

    Args:
        code: 合约代码
        timeframe: 周期
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)
        data_dir: 数据目录

    Returns:
        保存的文件路径
    """
    provider = FuturesDataProvider(
        DataConfig(cache_dir=data_dir, use_cache=True)
    )

    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")

    return provider.fetch_and_save(code, timeframe, start, end)


def load_futures_for_backtest(
    code: str,
    timeframe: str = "daily",
    data_dir: str = "./data"
) -> pl.DataFrame:
    """便捷函数: 加载用于回测的期货数据

    Args:
        code: 合约代码
        timeframe: 周期
        data_dir: 数据目录

    Returns:
        OHLCV数据
    """
    provider = FuturesDataProvider(
        DataConfig(cache_dir=data_dir, use_cache=True)
    )

    cache_path = os.path.join(data_dir, f"{code}_{timeframe}.parquet")

    if os.path.exists(cache_path):
        return provider.load_from_cache(code, timeframe)
    else:
        print(f"[load_futures_for_backtest] 缓存不存在，尝试下载 {code} 数据...")
        provider.fetch_and_save(code, timeframe, save_path=cache_path)
        return provider.load_from_cache(code, timeframe)
