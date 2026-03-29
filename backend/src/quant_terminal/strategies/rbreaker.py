"""
R-Breaker 策略

日内回转策略，根据昨日价格计算6个关键价位

关键价位:
    突破买入价 = 昨日最高 + 0.35 * (昨日收盘 - 昨日最低)
    观察卖出价 = 昨日最高 + 0.25 * (昨日收盘 - 昨日最低)
    反转卖出价 = 1.07/2 * (昨日最高 + 昨日最低) - 0.07 * 昨日最低
    反转买入价 = 1.07/2 * (昨日最高 + 昨日最低) - 0.07 * 昨日最高
    观察买入价 = 昨日最低 - 0.25 * (昨日最高 - 昨日收盘)
    突破卖出价 = 昨日最低 - 0.35 * (昨日最高 - 昨日收盘)

交易规则:
    1. 价格>突破买入价，趋势策略开仓做多
    2. 价格<突破卖出价，趋势策略开仓做空
    3. 持仓时价格<反转卖出价，反转策略平多开空
    4. 持仓时价格>反转买入价，反转策略平空开多
"""

from typing import List
import polars as pl
from .base import Strategy, StrategyConfig, Signal, SignalType


class RBreakerConfig(StrategyConfig):
    """R-Breaker配置"""
    def __init__(
        self,
        break_factor: float = 0.35,
        observe_factor: float = 0.25,
        reverse_factor: float = 0.07,
        **kwargs
    ):
        super().__init__(name="rbreaker", **kwargs)
        self.break_factor = break_factor
        self.observe_factor = observe_factor
        self.reverse_factor = reverse_factor


class RBreakerConfig(StrategyConfig):
    """R-Breaker配置"""
    def __init__(
        self,
        break_factor: float = 0.35,
        observe_factor: float = 0.25,
        reverse_factor: float = 0.07,
        **kwargs
    ):
        super().__init__(name="rbreaker", **kwargs)
        self.break_factor = break_factor
        self.observe_factor = observe_factor
        self.reverse_factor = reverse_factor


class RBreakerStrategy(Strategy):
    """
    R-Breaker 日内回转策略

    经典的日内交易策略，结合了趋势和反转两种交易模式

    Example:
        >>> config = RBreakerConfig()
        >>> strategy = RBreakerStrategy(config)
        >>> signals = strategy.generate_signals(df)
    """

    def __init__(self, config: RBreakerConfig = None):
        config = config or RBreakerConfig()
        super().__init__(config)
        self.break_factor = config.break_factor
        self.observe_factor = config.observe_factor
        self.reverse_factor = config.reverse_factor
        self._position = 0

    @property
    def name(self) -> str:
        return f"rbreaker_{self.break_factor:.2f}"

    def generate_signals(self, df: pl.DataFrame) -> List[Signal]:
        """生成R-Breaker交易信号"""
        if not self.validate_data(df):
            raise ValueError("数据缺少必需列")

        # 计算关键价位
        df_with_levels = self._calculate_levels(df)

        # 生成信号
        signals = []
        position = 0

        for row in df_with_levels.iter_rows(named=True):
            timestamp = row['datetime']
            high = row['high']
            low = row['low']
            close = row['close']

            # 获取6个关键价位
            bbreak = row['break_buy']
            sbreak = row['break_sell']
            treverse = row['trend_reverse']
            rreverse = row['range_reverse']

            if bbreak == 0:
                continue

            signal_type = None

            # 趋势策略
            if position == 0:
                if high >= bbreak:
                    signal_type = SignalType.BUY
                    position = 1
                elif low <= sbreak:
                    signal_type = SignalType.SELL
                    position = -1
            # 持仓状态下的反转策略
            elif position == 1:
                if low <= treverse:
                    signal_type = SignalType.SELL
                    position = -1
            elif position == -1:
                if high >= rreverse:
                    signal_type = SignalType.BUY
                    position = 1

            if signal_type is not None:
                signals.append(Signal(
                    timestamp=timestamp,
                    code=row.get('code', 'unknown'),
                    signal_type=signal_type,
                    price=close,
                    confidence=1.0,
                    metadata={
                        'break_buy': bbreak,
                        'break_sell': sbreak,
                        'trend_reverse': treverse,
                        'range_reverse': rreverse,
                        'position_after': position
                    }
                ))

        return signals

    def _calculate_levels(self, df: pl.DataFrame) -> pl.DataFrame:
        """计算R-Breaker的6个关键价位"""
        # 获取昨日数据
        df = df.with_columns([
            pl.col('high').shift(1).alias('prev_high'),
            pl.col('low').shift(1).alias('prev_low'),
            pl.col('close').shift(1).alias('prev_close'),
        ])

        # 计算关键价位
        df = df.with_columns([
            # 突破买入价
            (pl.col('prev_high') + self.break_factor * (pl.col('prev_close') - pl.col('prev_low')))
            .alias('break_buy'),
            # 突破卖出价
            (pl.col('prev_low') - self.break_factor * (pl.col('prev_high') - pl.col('prev_close')))
            .alias('break_sell'),
            # 观察卖出价
            (pl.col('prev_high') + self.observe_factor * (pl.col('prev_close') - pl.col('prev_low')))
            .alias('observe_sell'),
            # 观察买入价
            (pl.col('prev_low') - self.observe_factor * (pl.col('prev_high') - pl.col('prev_close')))
            .alias('observe_buy'),
        ])

        # 反转价位
        df = df.with_columns([
            ((1 + self.reverse_factor) / 2 * (pl.col('prev_high') + pl.col('prev_low'))
             - self.reverse_factor * pl.col('prev_low'))
            .alias('trend_reverse'),
            ((1 + self.reverse_factor) / 2 * (pl.col('prev_high') + pl.col('prev_low'))
             - self.reverse_factor * pl.col('prev_high'))
            .alias('range_reverse'),
        ])

        return df
