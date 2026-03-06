"""因子库 - 技术指标和量化因子计算"""

import polars as pl
import numpy as np


def calc_vwap(df: pl.DataFrame) -> pl.DataFrame:
    """计算 VWAP (Volume Weighted Average Price)"""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cum_sum()
    cum_vol = df["volume"].cum_sum()
    vwap = cum_tp_vol / cum_vol
    return df.with_columns(vwap.alias("vwap"))


def calc_ma(df: pl.DataFrame, periods: list[int] | None = None) -> pl.DataFrame:
    """计算移动平均线"""
    if periods is None:
        periods = [5, 10, 20, 60]
    for p in periods:
        df = df.with_columns(
            df["close"].rolling_mean(window_size=p).alias(f"ma{p}")
        )
    return df


def calc_ema(df: pl.DataFrame, periods: list[int] | None = None) -> pl.DataFrame:
    """计算指数移动平均"""
    if periods is None:
        periods = [12, 26]
    for p in periods:
        df = df.with_columns(
            df["close"].ewm_mean(span=p).alias(f"ema{p}")
        )
    return df


def calc_bollinger(df: pl.DataFrame, period: int = 20, std_dev: float = 2.0) -> pl.DataFrame:
    """计算布林带"""
    ma = df["close"].rolling_mean(window_size=period)
    std = df["close"].rolling_std(window_size=period)
    return df.with_columns([
        ma.alias("bb_mid"),
        (ma + std_dev * std).alias("bb_upper"),
        (ma - std_dev * std).alias("bb_lower"),
    ])


def calc_rsi(df: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """计算 RSI"""
    delta = df["close"].diff()
    gain = delta.clip(lower_bound=0).rolling_mean(window_size=period)
    loss = (-delta.clip(upper_bound=0)).rolling_mean(window_size=period)
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return df.with_columns(rsi.alias("rsi"))


def calc_macd(
    df: pl.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pl.DataFrame:
    """计算 MACD"""
    ema_fast = df["close"].ewm_mean(span=fast)
    ema_slow = df["close"].ewm_mean(span=slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm_mean(span=signal)
    histogram = macd_line - signal_line
    return df.with_columns([
        macd_line.alias("macd"),
        signal_line.alias("macd_signal"),
        histogram.alias("macd_hist"),
    ])


def calc_atr(df: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """计算 ATR (Average True Range)"""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()

    # 逐元素取最大值
    tr_values = np.maximum(
        high_low.to_numpy(),
        np.maximum(high_close.to_numpy(), low_close.to_numpy()),
    )
    tr = pl.Series("tr", tr_values)
    atr = tr.rolling_mean(window_size=period)
    return df.with_columns([tr.alias("tr"), atr.alias("atr")])


def calc_volume_ratio(df: pl.DataFrame, period: int = 5) -> pl.DataFrame:
    """计算量比"""
    avg_vol = df["volume"].rolling_mean(window_size=period).shift(1)
    vol_ratio = df["volume"] / avg_vol
    return df.with_columns(vol_ratio.alias("volume_ratio"))


def calc_momentum(df: pl.DataFrame, period: int = 10) -> pl.DataFrame:
    """计算动量"""
    mom = df["close"] / df["close"].shift(period) - 1
    return df.with_columns(mom.alias(f"momentum_{period}"))


def calc_all_factors(df: pl.DataFrame) -> pl.DataFrame:
    """计算所有因子"""
    df = calc_vwap(df)
    df = calc_ma(df)
    df = calc_ema(df)
    df = calc_bollinger(df)
    df = calc_rsi(df)
    df = calc_macd(df)
    df = calc_atr(df)
    df = calc_volume_ratio(df)
    df = calc_momentum(df)
    return df
