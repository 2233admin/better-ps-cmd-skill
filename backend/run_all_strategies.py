#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用 quant-terminal 内置策略进行期货回测
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import polars as pl
import numpy as np
from datetime import datetime
from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.strategy.factors import calc_all_factors, calc_rsi, calc_bollinger, calc_macd, calc_ma
from app.strategy.backtest import VectorBacktester


def generate_mean_reversion_signals(df: pl.DataFrame, deviation: float = 0.02) -> pl.DataFrame:
    """
    均值回归策略 (来自 signals.py)

    逻辑:
    - 当前价偏离 VWAP 超过 N 个标准差 → 反向操作
    - 价格远低于 VWAP → 买入 (超卖反弹)
    - 价格远高于 VWAP → 卖出 (超买回落)
    """
    # 计算 VWAP
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cum_sum()
    cum_vol = df["volume"].cum_sum()
    vwap = cum_tp_vol / cum_vol

    df = df.with_columns([
        vwap.alias("vwap"),
        ((pl.col("close") - vwap) / vwap).alias("deviation")
    ])

    # 生成信号
    signals = df.with_columns([
        pl.when(pl.col("deviation") < -deviation).then(1)   # 超卖买入
        .when(pl.col("deviation") > deviation).then(-1)     # 超买卖出
        .otherwise(0)
        .alias("signal")
    ])

    return signals


def generate_momentum_breakout_signals(df: pl.DataFrame, vol_threshold: float = 1.5) -> pl.DataFrame:
    """
    动量突破策略 (来自 signals.py)

    逻辑:
    - 成交量 > 前5日平均量的 N 倍
    - 价格突破当日高点
    → 买入信号
    """
    df = calc_ma(df, periods=[5])

    df = df.with_columns([
        (pl.col("volume") / pl.col("volume").rolling_mean(window_size=5)).alias("volume_ratio"),
        (pl.col("close") > pl.col("high").shift(1)).alias("price_breakout")
    ])

    signals = df.with_columns([
        pl.when(
            (pl.col("volume_ratio") > vol_threshold) &
            pl.col("price_breakout")
        ).then(1)
        .otherwise(0)
        .alias("signal")
    ])

    return signals


def generate_rsi_signals(df: pl.DataFrame, oversold: int = 30, overbought: int = 70) -> pl.DataFrame:
    """
    RSI 策略

    逻辑:
    - RSI < oversold → 买入 (超卖)
    - RSI > overbought → 卖出 (超买)
    """
    df = calc_rsi(df, period=14)

    signals = df.with_columns([
        pl.when(pl.col("rsi") < oversold).then(1)
        .when(pl.col("rsi") > overbought).then(-1)
        .otherwise(0)
        .alias("signal")
    ])

    return signals


def generate_macd_signals(df: pl.DataFrame) -> pl.DataFrame:
    """
    MACD 策略

    逻辑:
    - MACD 上穿信号线 → 买入
    - MACD 下穿信号线 → 卖出
    """
    df = calc_macd(df)

    signals = df.with_columns([
        pl.when(
            (pl.col("macd") > pl.col("macd_signal")) &
            (pl.col("macd").shift(1) <= pl.col("macd_signal").shift(1))
        ).then(1)
        .when(
            (pl.col("macd") < pl.col("macd_signal")) &
            (pl.col("macd").shift(1) >= pl.col("macd_signal").shift(1))
        ).then(-1)
        .otherwise(0)
        .alias("signal")
    ])

    return signals


def generate_bollinger_signals(df: pl.DataFrame) -> pl.DataFrame:
    """
    布林带策略

    逻辑:
    - 价格触及下轨 → 买入
    - 价格触及上轨 → 卖出
    """
    df = calc_bollinger(df)

    signals = df.with_columns([
        pl.when(pl.col("close") <= pl.col("bb_lower")).then(1)
        .when(pl.col("close") >= pl.col("bb_upper")).then(-1)
        .otherwise(0)
        .alias("signal")
    ])

    return signals


def run_strategy_backtest(code: str, strategy_name: str, strategy_func, **kwargs):
    """运行单个策略回测"""
    print(f"\n【{code} - {strategy_name}】")
    print("-" * 60)

    # 加载数据
    data = load_futures_for_backtest(code, 'daily')
    if data.is_empty():
        print(f"  无数据")
        return None

    print(f"  数据: {len(data)} 条")

    # 生成信号
    signals = strategy_func(data, **kwargs)

    buy_count = signals.filter(pl.col('signal') == 1).shape[0]
    sell_count = signals.filter(pl.col('signal') == -1).shape[0]
    print(f"  买入: {buy_count} 次, 卖出: {sell_count} 次")

    # 回测
    backtester = VectorBacktester(
        initial_capital=1_000_000,
        commission=0.0001,
        slippage=0.0002
    )

    result = backtester.run(data, signals)

    print(f"  总收益: {result.total_return:+.2%}  年化: {result.annual_return:+.2%}")
    print(f"  回撤: {result.max_drawdown:.2%}  夏普: {result.sharpe_ratio:.2f}  交易: {result.total_trades}")

    return {
        'code': code,
        'strategy': strategy_name,
        'total_return': result.total_return,
        'annual_return': result.annual_return,
        'max_drawdown': result.max_drawdown,
        'sharpe': result.sharpe_ratio,
        'trades': result.total_trades,
    }


def main():
    """主函数 - 多策略对比回测"""

    print("=" * 70)
    print("Quant Terminal 内置策略期货回测")
    print("=" * 70)

    # 下载数据
    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    # 策略列表
    strategies = [
        ('均值回归(VWAP)', generate_mean_reversion_signals, {'deviation': 0.02}),
        ('动量突破', generate_momentum_breakout_signals, {'vol_threshold': 1.5}),
        ('RSI超买超卖', generate_rsi_signals, {'oversold': 30, 'overbought': 70}),
        ('MACD金叉死叉', generate_macd_signals, {}),
        ('布林带', generate_bollinger_signals, {}),
    ]

    all_results = []

    for code in codes:
        print(f"\n{'='*70}")
        print(f"品种: {code}")
        print('='*70)

        for strategy_name, strategy_func, kwargs in strategies:
            result = run_strategy_backtest(code, strategy_name, strategy_func, **kwargs)
            if result:
                all_results.append(result)

    # 汇总报告
    print("\n" + "=" * 70)
    print("策略对比汇总")
    print("=" * 70)
    print(f"{'品种':<10} {'策略':<20} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<6} {'交易':<6}")
    print("-" * 70)

    for r in all_results:
        print(f"{r['code']:<10} {r['strategy']:<20} {r['total_return']:>+8.2%}  "
              f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
              f"{r['sharpe']:>4.2f}  {r['trades']:>4}")

    print("=" * 70)

    # 找出最佳策略
    best = max(all_results, key=lambda x: x['sharpe'])
    print(f"\n最佳策略 (按夏普比率):")
    print(f"  品种: {best['code']}  策略: {best['strategy']}")
    print(f"  夏普: {best['sharpe']:.2f}  总收益: {best['total_return']:+.2%}")


if __name__ == "__main__":
    main()
