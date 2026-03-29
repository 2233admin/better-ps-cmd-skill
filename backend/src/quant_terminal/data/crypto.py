"""
数字货币数据提供者

支持加密货币数据获取 (OKX, Binance等)
"""

import os
from datetime import datetime, timedelta
from typing import Optional, Dict
import polars as pl
import requests

from .base import DataProvider, DataConfig


class CryptoDataProvider(DataProvider):
    """
    加密货币数据提供者

    支持OKX等交易所数据

    Example:
        >>> provider = CryptoDataProvider()
        >>> df = provider.fetch('BTC-USDT', '1h')
    """

    # 交易所API配置
    EXCHANGES = {
        'okx': {
            'base_url': 'https://www.okx.com',
            'kline_endpoint': '/api/v5/market/history-candles',
        },
        'binance': {
            'base_url': 'https://api.binance.com',
            'kline_endpoint': '/api/v3/klines',
        }
    }

    # 标准代码映射
    CODE_MAPPING = {
        'BTC': 'BTC-USDT',
        'ETH': 'ETH-USDT',
        'SOL': 'SOL-USDT',
        'DOGE': 'DOGE-USDT',
    }

    def __init__(self, config: Optional[DataConfig] = None, exchange: str = 'okx'):
        super().__init__(config)
        self.exchange = exchange
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        if self.config.use_cache and self.config.cache_dir:
            # 加密数据存储在 crypto/ 子目录
            crypto_dir = os.path.join(self.config.cache_dir, 'crypto')
            os.makedirs(crypto_dir, exist_ok=True)

    def fetch(
        self,
        code: str,
        timeframe: str = "1h",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> pl.DataFrame:
        """获取加密货币数据

        Args:
            code: 交易对代码 (如 'BTC-USDT', 'BTC')
            timeframe: 周期，支持 '1m', '5m', '15m', '1h', '4h', '1d'
            start: 开始时间
            end: 结束时间
        """
        # 映射代码
        if '-' not in code and code.upper() in self.CODE_MAPPING:
            code = self.CODE_MAPPING[code.upper()]
        elif '-' not in code:
            code = f"{code.upper()}-USDT"

        cache_key = self.get_cache_key(f"crypto/{code}", timeframe, start, end)
        cached = self.cache_get(cache_key)
        if cached is not None:
            return cached

        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(days=30)

        try:
            if self.exchange == 'okx':
                df = self._fetch_okx(code, timeframe, start, end)
            elif self.exchange == 'binance':
                df = self._fetch_binance(code, timeframe, start, end)
            else:
                raise ValueError(f"不支持的交易所: {self.exchange}")

            if not self.validate_ohlcv(df):
                return pl.DataFrame()

            df = self.clean_data(df)
            self.cache_set(cache_key, df)

            return df

        except Exception as e:
            print(f"[CryptoDataProvider] 获取 {code} 数据失败: {e}")
            return pl.DataFrame()

    def _fetch_okx(
        self,
        inst_id: str,
        bar: str,
        start: datetime,
        end: datetime
    ) -> pl.DataFrame:
        """从OKX获取数据"""
        # 时间格式转换
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        # 周期映射
        timeframe_map = {
            '1m': '1m', '5m': '5m', '15m': '15m',
            '1h': '1H', '4h': '4H', '1d': '1D'
        }
        bar = timeframe_map.get(bar, '1H')

        url = f"{self.EXCHANGES['okx']['base_url']}{self.EXCHANGES['okx']['kline_endpoint']}"

        all_data = []
        current_end = end_ms

        while current_end > start_ms:
            params = {
                'instId': inst_id,
                'bar': bar,
                'before': start_ms,
                'after': current_end,
                'limit': 100
            }

            try:
                response = requests.get(url, params=params, timeout=30)
                data = response.json()

                if data.get('code') != '0':
                    break

                candles = data.get('data', [])
                if not candles:
                    break

                all_data.extend(candles)

                # 更新结束时间，用于分页
                current_end = int(candles[-1][0]) - 1

            except Exception as e:
                print(f"[CryptoDataProvider] OKX请求失败: {e}")
                break

        if not all_data:
            return pl.DataFrame()

        # 解析数据 [ts, open, high, low, close, vol, volCcy]
        df = pl.DataFrame({
            'datetime': [datetime.fromtimestamp(int(c[0]) / 1000) for c in all_data],
            'open': [float(c[1]) for c in all_data],
            'high': [float(c[2]) for c in all_data],
            'low': [float(c[3]) for c in all_data],
            'close': [float(c[4]) for c in all_data],
            'volume': [float(c[5]) for c in all_data],
        })

        return df.sort('datetime')

    def _fetch_binance(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime
    ) -> pl.DataFrame:
        """从Binance获取数据"""
        # 转换代码格式
        symbol = symbol.replace('-', '')

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        url = f"{self.EXCHANGES['binance']['base_url']}{self.EXCHANGES['binance']['kline_endpoint']}"

        params = {
            'symbol': symbol,
            'interval': interval,
            'startTime': start_ms,
            'endTime': end_ms,
            'limit': 1000
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            data = response.json()

            if not isinstance(data, list):
                return pl.DataFrame()

            # Binance格式 [ts, open, high, low, close, vol, ...]
            df = pl.DataFrame({
                'datetime': [datetime.fromtimestamp(c[0] / 1000) for c in data],
                'open': [float(c[1]) for c in data],
                'high': [float(c[2]) for c in data],
                'low': [float(c[3]) for c in data],
                'close': [float(c[4]) for c in data],
                'volume': [float(c[5]) for c in data],
            })

            return df

        except Exception as e:
            print(f"[CryptoDataProvider] Binance请求失败: {e}")
            return pl.DataFrame()

    def fetch_and_save(
        self,
        code: str,
        timeframe: str = "1h",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        save_path: Optional[str] = None
    ) -> str:
        """获取数据并保存"""
        df = self.fetch(code, timeframe, start, end)

        if df.is_empty():
            raise ValueError(f"未能获取 {code} 的数据")

        if save_path is None:
            # 存储在 crypto/ 子目录
            crypto_dir = os.path.join(self.config.cache_dir, 'crypto')
            os.makedirs(crypto_dir, exist_ok=True)

            # 标准化文件名
            safe_code = code.replace('-', '_')
            save_path = os.path.join(crypto_dir, f"{safe_code}_{timeframe}.parquet")

        df.write_parquet(save_path)
        print(f"[CryptoDataProvider] 数据已保存到: {save_path}")

        return save_path

    def load_from_cache(self, code: str, timeframe: str = "1h") -> pl.DataFrame:
        """从本地缓存加载数据"""
        safe_code = code.replace('-', '_')
        cache_path = os.path.join(
            self.config.cache_dir, 'crypto',
            f"{safe_code}_{timeframe}.parquet"
        )

        if not os.path.exists(cache_path):
            raise FileNotFoundError(f"缓存文件不存在: {cache_path}")

        return pl.read_parquet(cache_path)


def load_crypto_for_backtest(
    code: str,
    timeframe: str = "1h",
    data_dir: str = "./data",
    exchange: str = "okx"
) -> pl.DataFrame:
    """便捷函数: 加载加密货币数据用于回测"""
    provider = CryptoDataProvider(
        DataConfig(cache_dir=data_dir, use_cache=True),
        exchange=exchange
    )

    safe_code = code.replace('-', '_')
    cache_path = os.path.join(data_dir, 'crypto', f"{safe_code}_{timeframe}.parquet")

    if os.path.exists(cache_path):
        return provider.load_from_cache(code, timeframe)
    else:
        print(f"[load_crypto_for_backtest] 缓存不存在，尝试下载 {code} 数据...")
        provider.fetch_and_save(code, timeframe, save_path=cache_path)
        return provider.load_from_cache(code, timeframe)
