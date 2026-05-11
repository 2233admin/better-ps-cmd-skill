"""
文华财经期货数据提供者

支持从文华三立期货软件获取数据，接入Quant Terminal统一数据接口

功能:
1. 从文华期货客户端读取实时行情数据
2. 从本地文件导入历史数据
3. 支持模拟数据生成（用于测试）
4. 统一接口与三立期货数据兼容

Example:
    >>> from quant_terminal.data import WenhuaFuturesProvider
    >>> provider = WenhuaFuturesProvider()
    >>> df = provider.fetch('IF0', '1m')  # 获取股指期货1分钟数据
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from pathlib import Path
import polars as pl
import pandas as pd
import numpy as np
from loguru import logger

from .base import DataProvider, DataConfig


class WenhuaFuturesProvider(DataProvider):
    """
    文华财经期货数据提供者

    基于wenhuasanli项目API封装，接入Quant Terminal统一数据接口

    支持合约:
    - 股指期货: IF, IC, IH, IM (中金所)
    - 商品期货: 上期所、大商所、郑商所、能源中心

    数据源:
    1. 文华三立期货软件导出数据
    2. 本地缓存数据
    3. 模拟数据生成 (用于测试)

    注意: 使用本类需要安装文华三立期货软件
    """

    # 合约代码映射 (主力合约 -> 具体合约)
    CONTRACT_MAP = {
        # 股指期货
        'IF0': 'IF2512',      # 沪深300主力
        'IC0': 'IC2512',      # 中证500主力
        'IH0': 'IH2512',      # 上证50主力
        'IM0': 'IM2512',      # 中证1000主力
        # 国债期货
        'TF0': 'TF2506',      # 5年期国债
        'TS0': 'TS2506',      # 2年期国债
        'TL0': 'TL2506',      # 30年期国债
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
        'TF': 10000,    # 5年期国债
        'TS': 20000,    # 2年期国债
        'TL': 10000,    # 30年期国债
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

    # 保证金比例
    MARGIN_RATE = 0.15

    def __init__(
        self,
        config: Optional[DataConfig] = None,
        install_path: str = r"C:\wh6三立期货"
    ):
        """
        初始化文华期货数据提供者

        Args:
            config: 数据配置
            install_path: 文华三立期货软件安装路径
        """
        super().__init__(config)
        self.install_path = install_path
        self._ensure_cache_dir()

        # 尝试加载文华API
        self._api = None
        self._load_wenhua_api()

    def _load_wenhua_api(self):
        """加载文华API"""
        try:
            # 导入wenhuasanli项目的API
            import sys
            wenhua_path = r"C:\Users\Administrator\wenhuasanli\src"
            if wenhua_path not in sys.path:
                sys.path.insert(0, wenhua_path)

            from wenhua_api_wrapper import WenhuaAPI
            self._api = WenhuaAPI(self.install_path)
            logger.info(f"[WenhuaFutures] 文华API加载成功")
        except Exception as e:
            logger.warning(f"[WenhuaFutures] 文华API加载失败，将使用模拟数据: {e}")
            self._api = None

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        if self.config.use_cache and self.config.cache_dir:
            for exchange in ['cffex', 'shfe', 'dce', 'czce', 'ine']:
                dir_path = os.path.join(
                    self.config.cache_dir, 'futures', 'wenhua', exchange
                )
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
        获取文华期货数据

        Args:
            code: 合约代码 (支持主力合约如 'IF0')
            timeframe: 周期 (1m, 5m, 15m, 1h, 1d)
            start: 开始时间
            end: 结束时间
            **kwargs: 额外参数

        Returns:
            OHLCV数据DataFrame
        """
        # 映射主力合约代码
        mapped_code = self.CONTRACT_MAP.get(code, code)

        # 检查缓存
        cache_key = self.get_cache_key(
            f"wenhua/{mapped_code}", timeframe, start, end
        )
        cached = self.cache_get(cache_key)
        if cached is not None:
            return cached

        # 尝试从本地文件加载
        df = self._load_from_local(mapped_code, timeframe, start, end)
        if not df.is_empty():
            self.cache_set(cache_key, df)
            return df

        # 尝试从文华导出文件加载
        df = self._load_from_wenhua_export(mapped_code, timeframe)
        if not df.is_empty():
            self.cache_set(cache_key, df)
            return df

        # 尝试从文华API读取
        if self._api:
            df = self._load_from_api(mapped_code, timeframe, start, end)
            if not df.is_empty():
                self.cache_set(cache_key, df)
                return df

        # 尝试从 akshare 获取实盘数据
        df = self._load_from_akshare(code, mapped_code, timeframe, start, end)
        if not df.is_empty():
            self.cache_set(cache_key, df)
            return df

        # 生成模拟数据 (用于测试)
        logger.warning(f"[WenhuaFutures] 未找到 {mapped_code} 数据，生成模拟数据")
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
            self.config.cache_dir, 'futures', 'wenhua', exchange,
            f"{code}_{timeframe}.parquet"
        )

        if os.path.exists(cache_path):
            try:
                df = pl.read_parquet(cache_path)

                # 过滤时间范围
                if start and end:
                    df = df.filter(
                        (pl.col('datetime') >= start) & (pl.col('datetime') <= end)
                    )
                return df
            except Exception as e:
                logger.debug(f"[WenhuaFutures] 加载本地缓存失败: {e}")

        return pl.DataFrame()

    def _load_from_wenhua_export(self, code: str, timeframe: str) -> pl.DataFrame:
        """
        从文华三立导出文件加载

        支持格式:
        - 文华财经导出CSV
        - 博易大师导出CSV
        """
        export_paths = [
            # 文华导出路径
            os.path.expanduser(f"~/Documents/文华三立/{code}_{timeframe}.csv"),
            os.path.expanduser(f"~/Documents/wh6三立/{code}_{timeframe}.csv"),
            os.path.expanduser(f"~/Downloads/{code}_{timeframe}.csv"),
            # 博易大师导出路径
            os.path.expanduser(f"~/Documents/博易大师/{code}_{timeframe}.csv"),
            f"./data/wenhua_export/{code}_{timeframe}.csv",
            f"./data/boyue_export/{code}_{timeframe}.csv",
        ]

        for path in export_paths:
            if os.path.exists(path):
                try:
                    # 尝试读取CSV (文华导出格式)
                    df = pl.read_csv(
                        path,
                        has_header=True,
                        encoding='gbk',
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
                        'Date': 'datetime',
                        'Open': 'open',
                        'High': 'high',
                        'Low': 'low',
                        'Close': 'close',
                        'Volume': 'volume',
                    }
                    df = df.rename({k: v for k, v in column_mapping.items() if k in df.columns})

                    # 转换时间格式
                    if 'datetime' in df.columns:
                        df = df.with_columns([
                            pl.col('datetime').str.to_datetime(
                                "%Y-%m-%d %H:%M:%S",
                                strict=False
                            ).alias('datetime')
                        ])

                    # 添加code列
                    if 'code' not in df.columns:
                        df = df.with_columns(pl.lit(code).alias('code'))

                    logger.info(f"[WenhuaFutures] 从导出文件加载: {path}")
                    return df

                except Exception as e:
                    logger.debug(f"[WenhuaFutures] 加载导出文件失败 {path}: {e}")

        return pl.DataFrame()

    def _load_from_api(
        self,
        code: str,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime]
    ) -> pl.DataFrame:
        """从文华API读取数据"""
        if not self._api:
            return pl.DataFrame()

        try:
            # 获取所有用户数据
            users = self._api.get_all_users()
            if not users:
                return pl.DataFrame()

            # 读取第一个用户的数据
            user_id = users[0]
            user_data = self._api.read_user_data(user_id)

            # 这里可以根据实际需求解析用户数据
            # 目前返回空，由模拟数据填充
            logger.debug(f"[WenhuaFutures] 从API读取用户数据: {user_id}")

        except Exception as e:
            logger.debug(f"[WenhuaFutures] API读取失败: {e}")

        return pl.DataFrame()

    def _load_from_akshare(
        self,
        symbol: str,
        mapped_code: str,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime]
    ) -> pl.DataFrame:
        """通过 akshare 获取新浪期货真实历史数据

        支持周期: 1m, 5m, 15m, 30m, 1h, 1d
        """
        try:
            import akshare as ak

            # 周期映射
            period_map = {
                '1m': '1',
                '5m': '5',
                '15m': '15',
                '30m': '30',
                '1h': '60',
            }

            # akshare 主力合约接口更适合用原始 symbol (如 IF0)
            query_symbol = symbol if symbol in self.CONTRACT_MAP else mapped_code

            if timeframe in period_map:
                period = period_map[timeframe]
                df_pd = ak.futures_zh_minute_sina(symbol=query_symbol, period=period)

                if df_pd is None or df_pd.empty:
                    return pl.DataFrame()

                # 统一列名
                df_pd = df_pd.rename(columns={
                    'datetime': 'datetime',
                    'open': 'open',
                    'high': 'high',
                    'low': 'low',
                    'close': 'close',
                    'volume': 'volume',
                    'hold': 'open_interest',
                })

            elif timeframe == '1d':
                # 日线：优先使用新浪期货主力连续日线数据
                df_pd = None
                try:
                    end_str = end.strftime('%Y%m%d') if end else datetime.now().strftime('%Y%m%d')
                    start_str = start.strftime('%Y%m%d') if start else '20180101'
                    # 主力连续接口更适合 IF0/RB0 这种主力合约代码
                    df_pd = ak.futures_main_sina(symbol=query_symbol, start_date=start_str, end_date=end_str)
                    # 主力连续返回的列名是中文，需要标准化
                    col_map = {
                        '日期': 'datetime',
                        '开盘价': 'open',
                        '最高价': 'high',
                        '最低价': 'low',
                        '收盘价': 'close',
                        '成交量': 'volume',
                        '持仓量': 'open_interest',
                        '动态结算价': 'settle',
                    }
                    df_pd = df_pd.rename(columns={k: v for k, v in col_map.items() if k in df_pd.columns})
                except Exception as e:
                    logger.debug(f"[WenhuaFutures] futures_main_sina 失败: {e}")

                if df_pd is None or df_pd.empty:
                    try:
                        df_pd = ak.futures_zh_daily_sina(symbol=mapped_code)
                        df_pd = df_pd.rename(columns={
                            'date': 'datetime',
                            'open': 'open',
                            'high': 'high',
                            'low': 'low',
                            'close': 'close',
                            'volume': 'volume',
                            'hold': 'open_interest',
                        })
                    except Exception as e2:
                        logger.debug(f"[WenhuaFutures] futures_zh_daily_sina 失败: {e2}")
                        return pl.DataFrame()
            else:
                return pl.DataFrame()

            # 确保类型正确
            df_pd['datetime'] = pd.to_datetime(df_pd['datetime'])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df_pd.columns:
                    df_pd[col] = pd.to_numeric(df_pd[col], errors='coerce')
            if 'open_interest' in df_pd.columns:
                df_pd['open_interest'] = pd.to_numeric(df_pd['open_interest'], errors='coerce')
            else:
                df_pd['open_interest'] = 0

            df_pd['code'] = mapped_code
            df_pd = df_pd[['datetime', 'code', 'open', 'high', 'low', 'close', 'volume', 'open_interest']]

            # 过滤时间范围
            if start:
                df_pd = df_pd[df_pd['datetime'] >= start]
            if end:
                df_pd = df_pd[df_pd['datetime'] <= end]

            df = pl.from_pandas(df_pd)
            logger.info(f"[WenhuaFutures] 从 akshare 获取 {query_symbol} {timeframe} 数据: {df.height} 条")
            return df

        except Exception as e:
            logger.debug(f"[WenhuaFutures] akshare 获取失败: {e}")
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
        base_prices = {
            'IF': 4000, 'IC': 6000, 'IH': 2800, 'IM': 6500,
            'TF': 105, 'TS': 102, 'TL': 108,
            'RB': 3500, 'HC': 3600, 'I': 800, 'J': 2000, 'JM': 1500,
            'CU': 70000, 'AL': 18000, 'ZN': 22000, 'NI': 130000,
            'AU': 500, 'AG': 6000, 'SC': 550,
            'TA': 5800, 'MA': 2500, 'PP': 7500, 'L': 8000,
            'P': 7500, 'Y': 7800, 'M': 3500, 'C': 2500,
            'CF': 16000, 'SR': 6200,
        }

        base_price = 5000
        volatility = 0.02

        for prefix, price in base_prices.items():
            if code.upper().startswith(prefix):
                base_price = price
                # 股指期货波动较大
                if prefix in ['IF', 'IC', 'IH', 'IM']:
                    volatility = 0.02
                # 国债期货波动较小
                elif prefix in ['TF', 'TS', 'TL']:
                    volatility = 0.005
                else:
                    volatility = 0.015
                break

        # 生成时间序列
        if timeframe == '1m':
            periods = int((end - start).total_seconds() / 60)
        elif timeframe == '5m':
            periods = int((end - start).total_seconds() / 300)
        elif timeframe == '15m':
            periods = int((end - start).total_seconds() / 900)
        elif timeframe == '1h':
            periods = int((end - start).total_seconds() / 3600)
        elif timeframe == '1d':
            periods = (end - start).days
        else:
            periods = (end - start).days

        periods = min(max(periods, 1), 10000)  # 限制范围

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
        if timeframe == '1m':
            dates = [start + timedelta(minutes=i) for i in range(periods)]
        elif timeframe == '5m':
            dates = [start + timedelta(minutes=i*5) for i in range(periods)]
        elif timeframe == '15m':
            dates = [start + timedelta(minutes=i*15) for i in range(periods)]
        elif timeframe == '1h':
            dates = [start + timedelta(hours=i) for i in range(periods)]
        else:
            dates = [start + timedelta(days=i) for i in range(periods)]

        return pl.DataFrame({
            'datetime': dates,
            'code': [code] * periods,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
        })

    def import_from_wenhua(
        self,
        csv_path: str,
        code: str,
        timeframe: str
    ) -> str:
        """
        导入文华三立期货导出数据

        Args:
            csv_path: CSV导出文件路径
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
            pl.col('datetime').str.to_datetime(
                "%Y-%m-%d %H:%M:%S",
                strict=False
            ).alias('datetime')
        ])

        # 添加code列
        if 'code' not in df.columns:
            df = df.with_columns(pl.lit(code).alias('code'))

        # 保存到标准位置
        exchange = self._get_exchange(code)
        save_path = os.path.join(
            self.config.cache_dir, 'futures', 'wenhua', exchange,
            f"{code}_{timeframe}.parquet"
        )
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        df.write_parquet(save_path)

        logger.info(f"[WenhuaFutures] 导入完成: {csv_path} -> {save_path}")
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
            'source': 'wenhua',
        }

    def list_contracts(self, exchange: Optional[str] = None) -> List[str]:
        """列出支持的合约"""
        contracts = list(self.CONTRACT_MAP.keys())
        if exchange:
            contracts = [c for c in contracts if self._get_exchange(c) == exchange]
        return contracts

    def get_account_info(self) -> Optional[Dict]:
        """
        获取账户信息 (需要文华软件运行)

        Returns:
            账户信息字典，如果无法获取则返回None
        """
        if not self._api:
            return None

        try:
            users = self._api.get_all_users()
            if not users:
                return None

            user_id = users[0]
            user_data = self._api.read_user_data(user_id)

            if user_data.get('bill'):
                bill = self._api.parse_bill(user_data['bill'])
                return {
                    'user_id': user_id,
                    'client_id': bill.get('client_id'),
                    'client_name': bill.get('client_name'),
                    'account_no': bill.get('account_no'),
                    'date': bill.get('date'),
                }

        except Exception as e:
            logger.error(f"[WenhuaFutures] 获取账户信息失败: {e}")

        return None


def load_wenhua_for_backtest(
    code: str,
    timeframe: str = "1m",
    data_dir: str = "./data"
) -> pl.DataFrame:
    """便捷函数: 加载文华期货数据用于回测"""
    provider = WenhuaFuturesProvider(
        DataConfig(cache_dir=data_dir, use_cache=True)
    )
    return provider.fetch(code, timeframe)
