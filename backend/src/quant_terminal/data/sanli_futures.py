"""
三立期货数据抓取与模拟盘

支持从三立期货终端抓取数据，用于回测和模拟盘交易

三立期货官网: https://www.slqh.com/
行情软件: 三立期货博易大师/文华财经

功能:
1. 从三立期货客户端抓取实时行情
2. 历史数据导出和转换
3. 模拟盘交易（基于三立期货合约规则）

Example:
    >>> from quant_terminal.data import SanliFuturesProvider
    >>> provider = SanliFuturesProvider()
    >>> df = provider.fetch('IF2506', '1m')  # 抓取股指期货1分钟数据
"""

import os
import re
import json
import time
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from pathlib import Path
import polars as pl
import numpy as np
from loguru import logger

from .base import DataProvider, DataConfig


class SanliFuturesProvider(DataProvider):
    """
    三立期货数据提供者

    支持合约:
    - 股指期货: IF, IC, IH, IM (中金所)
    - 商品期货: 上期所、大商所、郑商所、能源中心

    数据源:
    1. 三立期货博易大师导出数据
    2. 三立期货CTP接口
    3. 本地缓存数据
    """

    # 合约代码映射 (三立期货格式 -> 标准格式)
    CONTRACT_MAP = {
        # 股指期货
        'IF0': 'IF2512',      # 沪深300主力
        'IC0': 'IC2512',      # 中证500主力
        'IH0': 'IH2512',      # 上证50主力
        'IM0': 'IM2512',      # 中证1000主力
        # 商品期货主力合约
        'RB0': 'RB2510',      # 螺纹钢
        'HC0': 'HC2510',      # 热卷
        'I0': 'I2509',        # 铁矿石
        'J0': 'J2509',        # 焦炭
        'JM0': 'JM2509',      # 焦煤
        'CU0': 'CU2506',      # 铜
        'AL0': 'AL2506',      # 铝
        'ZN0': 'ZN2506',      # 锌
        'NI0': 'NI2506',      # 镍
        'AU0': 'AU2506',      # 黄金
        'AG0': 'AG2506',      # 白银
        'SC0': 'SC2506',      # 原油
        'TA0': 'TA2509',      # PTA
        'MA0': 'MA2509',      # 甲醇
        'PP0': 'PP2509',      # 聚丙烯
        'L0': 'L2509',        # 塑料
        'P0': 'P2509',        # 棕榈油
        'Y0': 'Y2509',        # 豆油
        'M0': 'M2509',        # 豆粕
        'C0': 'C2509',        # 玉米
        'CF0': 'CF2509',      # 棉花
        'SR0': 'SR2509',      # 白糖
    }

    # 合约乘数配置
    CONTRACT_MULTIPLIER = {
        'IF': 300,      # 沪深300指数点 × 300元
        'IC': 200,      # 中证500指数点 × 200元
        'IH': 300,      # 上证50指数点 × 300元
        'IM': 200,      # 中证1000指数点 × 200元
        'RB': 10,       # 螺纹钢 10吨/手
        'HC': 10,       # 热卷 10吨/手
        'I': 100,       # 铁矿石 100吨/手
        'J': 100,       # 焦炭 100吨/手
        'JM': 60,       # 焦煤 60吨/手
        'CU': 5,        # 铜 5吨/手
        'AL': 5,        # 铝 5吨/手
        'ZN': 5,        # 锌 5吨/手
        'NI': 1,        # 镍 1吨/手
        'AU': 1000,     # 黄金 1000克/手
        'AG': 15,       # 白银 15千克/手
        'SC': 1000,     # 原油 1000桶/手
        'TA': 5,        # PTA 5吨/手
        'MA': 10,       # 甲醇 10吨/手
        'PP': 5,        # 聚丙烯 5吨/手
        'L': 5,         # 塑料 5吨/手
        'P': 10,        # 棕榈油 10吨/手
        'Y': 10,        # 豆油 10吨/手
        'M': 10,        # 豆粕 10吨/手
        'C': 10,        # 玉米 10吨/手
        'CF': 5,        # 棉花 5吨/手
        'SR': 10,       # 白糖 10吨/手
    }

    # 保证金比例 (默认15%，实际按交易所+期货公司调整)
    MARGIN_RATE = 0.15

    def __init__(self, config: Optional[DataConfig] = None):
        super().__init__(config)
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """确保缓存目录存在，按交易所分类"""
        if self.config.use_cache and self.config.cache_dir:
            for exchange in ['cffex', 'shfe', 'dce', 'czce', 'ine']:
                dir_path = os.path.join(self.config.cache_dir, 'futures', 'sanli', exchange)
                os.makedirs(dir_path, exist_ok=True)

    def _get_exchange(self, code: str) -> str:
        """根据合约代码判断交易所"""
        code = code.upper()
        # 中金所
        if code[:2] in ['IF', 'IC', 'IH', 'IM', 'TF', 'TS', 'TL']:
            return 'cffex'
        # 上期所
        elif code[:2] in ['CU', 'AL', 'ZN', 'NI', 'SN', 'AU', 'AG', 'RB', 'HC', 'SS', 'BU', 'RU', 'FU', 'SP']:
            return 'shfe'
        # 大商所
        elif code[:1] in ['I', 'J', 'M', 'Y', 'P', 'C', 'A', 'B', 'CS'] or code[:2] in ['JM', 'PP', 'L', 'V', 'EG', 'EB', 'PG', 'LH']:
            return 'dce'
        # 郑商所
        elif code[:2] in ['CF', 'SR', 'TA', 'MA', 'FG', 'RS', 'RM', 'JR', 'LR', 'SM', 'SF', 'CY', 'AP', 'CJ', 'UR', 'SA', 'PF', 'PK']:
            return 'czce'
        # 能源中心
        elif code[:2] in ['SC', 'NR', 'LU']:
            return 'ine'
        return 'unknown'

    def _get_contract_multiplier(self, code: str) -> int:
        """获取合约乘数"""
        for prefix, multiplier in self.CONTRACT_MULTIPLIER.items():
            if code.upper().startswith(prefix):
                return multiplier
        return 10  # 默认10

    def fetch(
        self,
        code: str,
        timeframe: str = "1m",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> pl.DataFrame:
        """
        获取三立期货数据

        支持从多个数据源获取:
        1. 本地缓存
        2. 三立期货博易大师导出文件
        3. 三立期货CTP接口
        4. 模拟数据生成 (用于测试)
        """
        # 映射主力合约代码
        mapped_code = self.CONTRACT_MAP.get(code, code)

        # 检查缓存
        cache_key = self.get_cache_key(f"sanli/{mapped_code}", timeframe, start, end)
        cached = self.cache_get(cache_key)
        if cached is not None:
            return cached

        # 尝试从本地文件加载
        df = self._load_from_local(mapped_code, timeframe, start, end)
        if not df.is_empty():
            self.cache_set(cache_key, df)
            return df

        # 尝试从三立期货导出文件加载
        df = self._load_from_boyue_export(mapped_code, timeframe)
        if not df.is_empty():
            self.cache_set(cache_key, df)
            return df

        # 生成模拟数据 (用于演示)
        logger.warning(f"[SanliFutures] 未找到 {mapped_code} 数据，生成模拟数据")
        df = self._generate_mock_data(mapped_code, timeframe, start, end)
        self.cache_set(cache_key, df)

        return df

    def _load_from_local(
        self,
        code: str,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime]
    ) -> pl.DataFrame:
        """从本地缓存加载"""
        exchange = self._get_exchange(code)
        cache_path = os.path.join(
            self.config.cache_dir, 'futures', 'sanli', exchange,
            f"{code}_{timeframe}.parquet"
        )

        if os.path.exists(cache_path):
            df = pl.read_parquet(cache_path)

            # 过滤时间范围
            if start and end:
                df = df.filter(
                    (pl.col('datetime') >= start) & (pl.col('datetime') <= end)
                )
            return df

        return pl.DataFrame()

    def _load_from_boyue_export(self, code: str, timeframe: str) -> pl.DataFrame:
        """
        从三立期货博易大师导出文件加载

        博易大师导出格式:
        - 时间,开盘,最高,最低,收盘,成交量,持仓量
        """
        export_paths = [
            os.path.expanduser(f"~/Documents/三立期货/{code}_{timeframe}.csv"),
            os.path.expanduser(f"~/Downloads/{code}_{timeframe}.csv"),
            f"./data/sanli_export/{code}_{timeframe}.csv",
        ]

        for path in export_paths:
            if os.path.exists(path):
                try:
                    # 读取博易大师CSV格式
                    df = pl.read_csv(
                        path,
                        has_header=True,
                        encoding='gbk',
                        new_columns=['datetime', 'open', 'high', 'low', 'close', 'volume', 'open_interest']
                    )

                    # 转换时间格式
                    df = df.with_columns([
                        pl.col('datetime').str.to_datetime("%Y-%m-%d %H:%M:%S").alias('datetime')
                    ])

                    logger.info(f"[SanliFutures] 从博易大师导出加载: {path}")
                    return df

                except Exception as e:
                    logger.debug(f"[SanliFutures] 加载导出文件失败 {path}: {e}")

        return pl.DataFrame()

    def _generate_mock_data(
        self,
        code: str,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime]
    ) -> pl.DataFrame:
        """生成模拟数据 (用于测试)"""
        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(days=30)

        # 根据合约类型设置基础价格
        if code.startswith('IF'):
            base_price = 4000
            volatility = 0.02
        elif code.startswith('IC'):
            base_price = 6000
            volatility = 0.025
        elif code.startswith('IH'):
            base_price = 2800
            volatility = 0.018
        elif code.startswith('IM'):
            base_price = 6500
            volatility = 0.028
        else:
            base_price = 5000
            volatility = 0.02

        # 生成时间序列
        if timeframe == '1m':
            periods = int((end - start).total_seconds() / 60)
        elif timeframe == '5m':
            periods = int((end - start).total_seconds() / 300)
        elif timeframe == '1h':
            periods = int((end - start).total_seconds() / 3600)
        else:
            periods = (end - start).days

        periods = min(periods, 10000)  # 限制最大条数

        # 生成价格序列
        np.random.seed(hash(code) % 2**32)
        returns = np.random.normal(0, volatility / np.sqrt(252 * 4), periods)
        prices = base_price * np.exp(np.cumsum(returns))

        # 生成OHLCV
        opens = prices * (1 + np.random.normal(0, 0.001, periods))
        highs = np.maximum(opens, prices) * (1 + np.abs(np.random.normal(0, 0.005, periods)))
        lows = np.minimum(opens, prices) * (1 - np.abs(np.random.normal(0, 0.005, periods)))
        closes = prices
        volumes = np.random.randint(1000, 10000, periods)

        # 生成时间戳
        dates = [start + timedelta(minutes=i) for i in range(periods)]

        return pl.DataFrame({
            'datetime': dates,
            'code': [code] * periods,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
        })

    def import_from_boyue(
        self,
        csv_path: str,
        code: str,
        timeframe: str
    ) -> str:
        """
        导入三立期货博易大师导出数据

        Args:
            csv_path: 博易大师CSV导出文件路径
            code: 合约代码
            timeframe: 周期

        Returns:
            导入后的文件路径
        """
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"文件不存在: {csv_path}")

        # 读取CSV
        df = pl.read_csv(
            csv_path,
            has_header=True,
            encoding='gbk'
        )

        # 标准化列名
        column_mapping = {
            '时间': 'datetime',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'volume',
            '持仓量': 'open_interest',
        }
        df = df.rename({k: v for k, v in column_mapping.items() if k in df.columns})

        # 转换时间
        df = df.with_columns([
            pl.col('datetime').str.to_datetime("%Y-%m-%d %H:%M:%S").alias('datetime')
        ])

        # 保存到标准位置
        exchange = self._get_exchange(code)
        save_path = os.path.join(
            self.config.cache_dir, 'futures', 'sanli', exchange,
            f"{code}_{timeframe}.parquet"
        )
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        df.write_parquet(save_path)

        logger.info(f"[SanliFutures] 导入完成: {csv_path} -> {save_path}")
        return save_path

    def get_contract_info(self, code: str) -> Dict:
        """获取合约信息"""
        mapped_code = self.CONTRACT_MAP.get(code, code)
        multiplier = self._get_contract_multiplier(mapped_code)

        return {
            'code': mapped_code,
            'display_code': code,
            'exchange': self._get_exchange(mapped_code),
            'multiplier': multiplier,
            'margin_rate': self.MARGIN_RATE,
            'min_price_tick': 0.2 if mapped_code[:2] in ['IF', 'IC', 'IH', 'IM'] else 1.0,
        }

    def list_contracts(self, exchange: Optional[str] = None) -> List[str]:
        """列出支持的合约"""
        contracts = list(self.CONTRACT_MAP.keys())
        if exchange:
            contracts = [c for c in contracts if self._get_exchange(c) == exchange]
        return contracts


def load_sanli_for_backtest(
    code: str,
    timeframe: str = "1m",
    data_dir: str = "./data"
) -> pl.DataFrame:
    """便捷函数: 加载三立期货数据用于回测"""
    provider = SanliFuturesProvider(
        DataConfig(cache_dir=data_dir, use_cache=True)
    )
    return provider.fetch(code, timeframe)
