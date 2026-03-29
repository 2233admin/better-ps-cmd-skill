"""
Dual Thrust 策略

开盘区间突破策略，由Michael Chalek在1980年代开发

策略逻辑:
1. 计算N周期的 Range = Max(HH-LC, HC-LL)
2. 上轨 = 开盘价 + K1 * Range
3. 下轨 = 开盘价 - K2 * Range
4. 突破上轨做多，突破下轨做空

参数:
    n_periods: 计算周期
    k1: 上轨系数
    k2: 下轨系数
"""

from typing import Dict, List, Any
import polars as pl
import numpy as np
from .base import Strategy, StrategyConfig, Signal, SignalType


class DualThrustConfig(StrategyConfig):
    """Dual Thrust配置"""
    def __init__(
        self,
        n_periods: int = 5,
        k1: float = 0.5,
        k2: float = 0.5,
        **kwargs
    ):
        super().__init__(name="dual_thrust", **kwargs)
        self.n_periods = n_periods
        self.k1 = k1
        self.k2 = k2


class DualThrustStrategy(Strategy):
    """
    Dual Thrust 开盘区间突破策略

    适用于股指期货的高频突破策略

    Example:
        >>> config = DualThrustConfig(n_periods=5, k1=0.6, k2=0.6)
        >>> strategy = DualThrustStrategy(config)
        >>> signals = strategy.generate_signals(df)
    """

    def __init__(self, config: DualThrustConfig = None):
        config = config or DualThrustConfig()
        super().__init__(config)
        self.n_periods = config.n_periods
        self.k1 = config.k1
        self.k2 = config.k2
        self._position = 0  # 当前持仓状态

    @property
    def name(self) -> str:
        return f"dual_thrust_{self.n_periods}_{self.k1:.2f}_{self.k2:.2f}"

    def set_params(self, **params) -> "DualThrustStrategy":
        """设置策略参数"""
        super().set_params(**params)
        if 'n_periods' in params:
            self.n_periods = params['n_periods']
        if 'k1' in params:
            self.k1 = params['k1']
        if 'k2' in params:
            self.k2 = params['k2']
        return self

    def generate_signals(self, df: pl.DataFrame) -> List[Signal]:
        """生成Dual Thrust交易信号"""
        if not self.validate_data(df):
            raise ValueError("数据缺少必需列")

        # 计算指标
        df_with_indicators = self._calculate_indicators(df)

        # 生成信号
        signals = []
        position = 0

        for row in df_with_indicators.iter_rows(named=True):
            timestamp = row['datetime']
            high = row['high']
            low = row['low']
            open_price = row['open']
            upper = row['upper_band']
            lower = row['lower_band']

            # 跳过无法计算上轨下轨的K线
            if upper is None or lower is None or upper == 0 or lower == 0:
                continue

            signal_type = None

            if position == 0:
                # 空仓状态
                if high >= upper:
                    signal_type = SignalType.BUY
                    position = 1
                elif low <= lower:
                    signal_type = SignalType.SELL
                    position = -1
            elif position == 1:
                # 多头状态
                if low <= lower:
                    signal_type = SignalType.SELL
                    position = -1
            elif position == -1:
                # 空头状态
                if high >= upper:
                    signal_type = SignalType.BUY
                    position = 1

            if signal_type is not None:
                signals.append(Signal(
                    timestamp=timestamp,
                    code=row.get('code', 'unknown'),
                    signal_type=signal_type,
                    price=row['close'],
                    confidence=1.0,
                    metadata={
                        'upper_band': upper,
                        'lower_band': lower,
                        'position_after': position
                    }
                ))

        return signals

    def _calculate_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """计算Dual Thrust指标"""
        n = self.n_periods

        # 计算HH, LL, HC, LC
        df = df.with_columns([
            pl.col('high').rolling_max(window_size=n).alias('hh'),
            pl.col('low').rolling_min(window_size=n).alias('ll'),
            pl.col('close').rolling_max(window_size=n).alias('hc'),
            pl.col('close').rolling_min(window_size=n).alias('lc'),
        ])

        # 计算Range和上下轨
        df = df.with_columns([
            (pl.max_horizontal(pl.col('hh') - pl.col('lc'), pl.col('hc') - pl.col('ll'))).alias('range'),
        ])

        df = df.with_columns([
            (pl.col('open') + self.k1 * pl.col('range')).alias('upper_band'),
            (pl.col('open') - self.k2 * pl.col('range')).alias('lower_band'),
        ])

        return df

    def generate_signals_vectorized(self, df: pl.DataFrame) -> pl.DataFrame:
        """向量化信号生成 (用于批量回测)

        Returns:
            包含signal列的DataFrame
        """
        df = self._calculate_indicators(df)

        # 向量化信号生成
        df = df.with_columns([
            pl.when(pl.col('high') >= pl.col('upper_band'))
            .then(1)
            .when(pl.col('low') <= pl.col('lower_band'))
            .then(-1)
            .otherwise(0)
            .alias('raw_signal')
        ])

        # 应用状态机
        signals = []
        position = 0

        for row in df.iter_rows(named=True):
            raw = row['raw_signal']
            final_signal = 0

            if position == 0:
                if raw == 1:
                    final_signal = 1
                    position = 1
                elif raw == -1:
                    final_signal = -1
                    position = -1
            elif position == 1:
                if raw == -1:
                    final_signal = -1
                    position = -1
            elif position == -1:
                if raw == 1:
                    final_signal = 1
                    position = 1

            signals.append(final_signal)

        return df.with_columns([
            pl.Series('signal', signals)
        ])
