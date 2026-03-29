"""
数据提供者基类

定义统一的数据访问接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
import polars as pl


@dataclass
class DataConfig:
    """数据配置"""
    # 数据源配置
    source: str = "akshare"  # akshare, tdx, okx
    cache_dir: str = "./data/cache"
    use_cache: bool = True
    cache_ttl: int = 3600  # 缓存有效期(秒)

    # API配置
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    base_url: Optional[str] = None

    # 数据库配置
    db_path: Optional[str] = None


def resample_df(
    df: pl.DataFrame,
    timeframe: str,
    time_col: str = "datetime"
) -> pl.DataFrame:
    """重采样数据到指定周期

    Args:
        df: 输入数据
        timeframe: 目标周期 (1m, 5m, 15m, 1h, 1d)
        time_col: 时间列名

    Returns:
        重采样后的数据
    """
    # 根据timeframe确定分组规则
    if timeframe.endswith('m'):
        minutes = int(timeframe[:-1])
        df = df.with_columns([
            pl.col(time_col).dt.truncate(f"{minutes}m").alias('group_time')
        ])
    elif timeframe.endswith('h'):
        hours = int(timeframe[:-1])
        df = df.with_columns([
            pl.col(time_col).dt.truncate(f"{hours}h").alias('group_time')
        ])
    elif timeframe == '1d':
        df = df.with_columns([
            pl.col(time_col).dt.truncate("1d").alias('group_time')
        ])
    else:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    # 聚合
    return df.group_by('group_time').agg([
        pl.col('open').first().alias('open'),
        pl.col('high').max().alias('high'),
        pl.col('low').min().alias('low'),
        pl.col('close').last().alias('close'),
        pl.col('volume').sum().alias('volume'),
    ]).rename({'group_time': time_col}).sort(time_col)


class DataProvider(ABC):
    """数据提供者基类

    所有数据源必须继承此类

    Example:
        class MyDataProvider(DataProvider):
            def fetch(self, code, timeframe, start, end):
                # 实现数据获取
                return df
    """

    def __init__(self, config: Optional[DataConfig] = None):
        """初始化数据提供者

        Args:
            config: 数据配置
        """
        self.config = config or DataConfig()
        self._cache: Dict[str, pl.DataFrame] = {}

    @abstractmethod
    def fetch(
        self,
        code: str,
        timeframe: str = "1d",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> pl.DataFrame:
        """获取数据

        Args:
            code: 品种代码
            timeframe: 周期 (1m, 5m, 15m, 1h, 1d, 1w)
            start: 开始时间
            end: 结束时间
            **kwargs: 额外参数

        Returns:
            OHLCV数据DataFrame
        """
        pass

    def fetch_batch(
        self,
        codes: List[str],
        timeframe: str = "1d",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> Dict[str, pl.DataFrame]:
        """批量获取数据

        Args:
            codes: 品种代码列表
            timeframe: 周期
            start: 开始时间
            end: 结束时间
            **kwargs: 额外参数

        Returns:
            品种代码到数据的映射
        """
        results = {}
        for code in codes:
            try:
                results[code] = self.fetch(code, timeframe, start, end, **kwargs)
            except Exception as e:
                print(f"[DataProvider] 获取 {code} 数据失败: {e}")
        return results

    def get_cache_key(
        self,
        code: str,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime]
    ) -> str:
        """生成缓存键"""
        start_str = start.strftime("%Y%m%d") if start else ""
        end_str = end.strftime("%Y%m%d") if end else ""
        return f"{code}_{timeframe}_{start_str}_{end_str}"

    def cache_get(self, key: str) -> Optional[pl.DataFrame]:
        """从缓存获取数据"""
        if not self.config.use_cache:
            return None
        return self._cache.get(key)

    def cache_set(self, key: str, df: pl.DataFrame) -> None:
        """设置缓存"""
        if self.config.use_cache:
            self._cache[key] = df

    def validate_ohlcv(self, df: pl.DataFrame) -> bool:
        """验证OHLCV数据格式

        Args:
            df: 输入数据

        Returns:
            是否有效
        """
        required_cols = {'datetime', 'open', 'high', 'low', 'close', 'volume'}

        if not required_cols.issubset(set(df.columns)):
            missing = required_cols - set(df.columns)
            print(f"[DataProvider] 数据缺少列: {missing}")
            return False

        # 检查数据有效性
        if df.is_empty():
            print("[DataProvider] 数据为空")
            return False

        # 检查OHLC关系
        invalid_ohlc = (
            (df['high'] < df['low']).sum() +
            (df['high'] < df['open']).sum() +
            (df['high'] < df['close']).sum() +
            (df['low'] > df['open']).sum() +
            (df['low'] > df['close']).sum()
        )

        if invalid_ohlc > 0:
            print(f"[DataProvider] 警告: {invalid_ohlc} 条无效OHLC数据")

        return True

    def clean_data(self, df: pl.DataFrame) -> pl.DataFrame:
        """清洗数据

        Args:
            df: 输入数据

        Returns:
            清洗后的数据
        """
        # 去重
        df = df.unique(subset=['datetime'])

        # 排序
        df = df.sort('datetime')

        # 处理无效值
        df = df.with_columns([
            pl.col('open').fill_null(pl.col('close')),
            pl.col('high').fill_null(pl.col('close')),
            pl.col('low').fill_null(pl.col('close')),
            pl.col('volume').fill_null(0),
        ])

        # 修正OHLC关系
        df = df.with_columns([
            pl.max_horizontal(['high', 'open', 'close']).alias('high'),
            pl.min_horizontal(['low', 'open', 'close']).alias('low'),
        ])

        return df

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(source={self.config.source})"
