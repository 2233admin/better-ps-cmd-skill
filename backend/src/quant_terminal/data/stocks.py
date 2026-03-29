"""
股票数据提供者

支持A股市场数据获取
"""

import os
from datetime import datetime, timedelta
from typing import Optional, List
import polars as pl
import akshare as ak

from .base import DataProvider, DataConfig


class StockDataProvider(DataProvider):
    """
    A股数据提供者

    使用AKShare获取A股数据

    Example:
        >>> provider = StockDataProvider()
        >>> df = provider.fetch('000001', 'daily')  # 平安银行
    """

    # 市场后缀
    MARKET_SUFFIX = {
        'sh': '.SH',  # 上海
        'sz': '.SZ',  # 深圳
        'bj': '.BJ',  # 北京
    }

    def __init__(self, config: Optional[DataConfig] = None):
        super().__init__(config)
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        if self.config.use_cache and self.config.cache_dir:
            # 股票数据存储在 stocks/ 子目录
            stocks_dir = os.path.join(self.config.cache_dir, 'stocks')
            os.makedirs(stocks_dir, exist_ok=True)

    def fetch(
        self,
        code: str,
        timeframe: str = "daily",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> pl.DataFrame:
        """获取A股数据

        Args:
            code: 股票代码 (如 '000001', '600000')
            timeframe: 周期，支持 'daily', 'weekly', '60m'
            start: 开始日期
            end: 结束日期
        """
        cache_key = self.get_cache_key(f"stocks/{code}", timeframe, start, end)
        cached = self.cache_get(cache_key)
        if cached is not None:
            return cached

        # 默认日期
        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(days=365)

        try:
            if timeframe == "daily":
                df = self._fetch_daily(code, start, end)
            elif timeframe == "weekly":
                df = self._fetch_weekly(code, start, end)
            else:
                raise ValueError(f"不支持的周期: {timeframe}")

            if not self.validate_ohlcv(df):
                return pl.DataFrame()

            df = self.clean_data(df)
            self.cache_set(cache_key, df)

            return df

        except Exception as e:
            print(f"[StockDataProvider] 获取 {code} 数据失败: {e}")
            return pl.DataFrame()

    def _fetch_daily(
        self,
        code: str,
        start: datetime,
        end: datetime
    ) -> pl.DataFrame:
        """获取日频数据"""
        # 使用AKShare获取A股历史行情
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        # 判断市场
        if code.startswith('6') or code.startswith('5'):
            # 上海
            df_pd = ak.stock_zh_a_hist(symbol=code, period="daily",
                                       start_date=start_str, end_date=end_str, adjust="qfq")
        else:
            # 深圳/创业板/科创板
            df_pd = ak.stock_zh_a_hist(symbol=code, period="daily",
                                       start_date=start_str, end_date=end_str, adjust="qfq")

        if df_pd.empty:
            return pl.DataFrame()

        df = pl.from_pandas(df_pd)

        # 标准化列名
        column_mapping = {
            '日期': 'datetime',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'volume',
        }
        df = df.rename({k: v for k, v in column_mapping.items() if k in df.columns})

        # 转换日期
        df = df.with_columns([
            pl.col('datetime').str.to_datetime("%Y-%m-%d").alias('datetime')
        ])

        # 数值类型转换
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df = df.with_columns([pl.col(col).cast(pl.Float64).alias(col)])

        return df

    def _fetch_weekly(self, code: str, start: datetime, end: datetime) -> pl.DataFrame:
        """获取周频数据"""
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        df_pd = ak.stock_zh_a_hist(symbol=code, period="weekly",
                                   start_date=start_str, end_date=end_str, adjust="qfq")

        if df_pd.empty:
            return pl.DataFrame()

        df = pl.from_pandas(df_pd)
        column_mapping = {
            '日期': 'datetime',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'volume',
        }
        df = df.rename({k: v for k, v in column_mapping.items() if k in df.columns})
        df = df.with_columns([
            pl.col('datetime').str.to_datetime("%Y-%m-%d").alias('datetime')
        ])

        return df

    def fetch_stock_list(self, market: str = "all") -> pl.DataFrame:
        """获取股票列表

        Args:
            market: 'sh'(上海), 'sz'(深圳), 'bj'(北京), 'all'(全部)

        Returns:
            股票列表DataFrame
        """
        if market == "sh":
            df = ak.stock_sh_a_spot_em()
        elif market == "sz":
            df = ak.stock_sz_a_spot_em()
        elif market == "bj":
            df = ak.stock_bj_a_spot_em()
        else:
            # 全部
            sh = ak.stock_sh_a_spot_em()
            sz = ak.stock_sz_a_spot_em()
            bj = ak.stock_bj_a_spot_em()
            df = pl.concat([pl.from_pandas(sh), pl.from_pandas(sz), pl.from_pandas(bj)])
            return df

        return pl.from_pandas(df)

    def fetch_and_save(
        self,
        code: str,
        timeframe: str = "daily",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        save_path: Optional[str] = None
    ) -> str:
        """获取数据并保存"""
        df = self.fetch(code, timeframe, start, end)

        if df.is_empty():
            raise ValueError(f"未能获取 {code} 的数据")

        if save_path is None:
            # 存储在 stocks/ 子目录
            stocks_dir = os.path.join(self.config.cache_dir, 'stocks')
            os.makedirs(stocks_dir, exist_ok=True)
            save_path = os.path.join(stocks_dir, f"{code}_{timeframe}.parquet")

        df.write_parquet(save_path)
        print(f"[StockDataProvider] 数据已保存到: {save_path}")

        return save_path

    def load_from_cache(self, code: str, timeframe: str = "daily") -> pl.DataFrame:
        """从本地缓存加载数据"""
        cache_path = os.path.join(
            self.config.cache_dir, 'stocks',
            f"{code}_{timeframe}.parquet"
        )

        if not os.path.exists(cache_path):
            raise FileNotFoundError(f"缓存文件不存在: {cache_path}")

        return pl.read_parquet(cache_path)


def load_stock_for_backtest(
    code: str,
    timeframe: str = "daily",
    data_dir: str = "./data"
) -> pl.DataFrame:
    """便捷函数: 加载股票数据用于回测"""
    provider = StockDataProvider(
        DataConfig(cache_dir=data_dir, use_cache=True)
    )

    cache_path = os.path.join(data_dir, 'stocks', f"{code}_{timeframe}.parquet")

    if os.path.exists(cache_path):
        return provider.load_from_cache(code, timeframe)
    else:
        print(f"[load_stock_for_backtest] 缓存不存在，尝试下载 {code} 数据...")
        provider.fetch_and_save(code, timeframe, save_path=cache_path)
        return provider.load_from_cache(code, timeframe)
