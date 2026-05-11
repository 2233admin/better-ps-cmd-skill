#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal 经典CTA策略回测

包含经典期货策略:
- Dual Thrust: Michael Chalek开发, Futures Truth Magazine Top 10
- R-Breaker: Richard Saidenberg开发, 日内反转+趋势混合
- ATR动态仓位: 波动率加权仓位管理
- 海龟交易法则: 唐奇安通道突破
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import numpy as np
import polars as pl
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.data.indicators import calc_indicators
from app.strategy.backtest import VectorBacktester


class FuturesResult:
    """期货回测结果"""
    def __init__(self, **kwargs):
        self.total_return = kwargs.get('total_return', 0.0)
        self.annual_return = kwargs.get('annual_return', 0.0)
        self.max_drawdown = kwargs.get('max_drawdown', 0.0)
        self.sharpe_ratio = kwargs.get('sharpe_ratio', 0.0)
        self.total_trades = kwargs.get('total_trades', 0)
        self.final_capital = kwargs.get('final_capital', 0.0)


# ============================================================================
# Dual Thrust 策略 (Michael Chalek)
# ============================================================================

def dual_thrust_strategy(
    df: pl.DataFrame,
    n_periods: int = 4,
    k1: float = 0.5,
    k2: float = 0.5,
    use_daily_range: bool = True
) -> pl.DataFrame:
    """
    Dual Thrust 策略 - 经典突破系统

    逻辑:
    - 计算N日区间的最高最高价(HH)、最低最低价(LL)、最高收盘价(HC)、最低收盘价(LC)
    - Range = Max(HH - LC, HC - LL)
    - 上轨 = 开盘 + K1 * Range
    - 下轨 = 开盘 - K2 * Range
    - 突破上轨做多，突破下轨做空

    Args:
        n_periods: 观察周期(默认4日)
        k1: 做多系数(通常0.3-0.7)
        k2: 做空系数(通常0.3-0.7)
        use_daily_range: 是否使用日内的范围计算
    """
    n = len(df)
    if n < n_periods + 1:
        return df.with_columns([pl.Series('signal', [0] * n)])

    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    closes = df['close'].to_numpy()
    opens = df['open'].to_numpy()

    signals = []
    position = 0  # 0: 空仓, 1: 多头, -1: 空头

    for i in range(n):
        if i < n_periods:
            signals.append(0)
            continue

        # 计算前N周期的HH, LL, HC, LC
        hh = np.max(highs[i-n_periods:i])
        ll = np.min(lows[i-n_periods:i])
        hc = np.max(closes[i-n_periods:i])
        lc = np.min(closes[i-n_periods:i])

        # 计算Range
        range_val = max(hh - lc, hc - ll)

        # 计算上下轨 (基于当日开盘价)
        upper = opens[i] + k1 * range_val
        lower = opens[i] - k2 * range_val

        current_high = highs[i]
        current_low = lows[i]

        # 信号生成
        signal = 0

        if position == 0:
            # 空仓时: 突破开仓
            if current_high >= upper:
                signal = 1
                position = 1
            elif current_low <= lower:
                signal = -1
                position = -1
        elif position == 1:
            # 多头时: 突破下轨平仓反空
            if current_low <= lower:
                signal = -1
                position = -1
            else:
                signal = 0
        elif position == -1:
            # 空头时: 突破上轨平仓反多
            if current_high >= upper:
                signal = 1
                position = 1
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# R-Breaker 策略 (Richard Saidenberg)
# ============================================================================

def r_breaker_strategy(
    df: pl.DataFrame,
    f1: float = 0.35,
    f2: float = 0.07,
    f3: float = 0.25
) -> pl.DataFrame:
    """
    R-Breaker 策略 - 反转与趋势混合系统

    连续15年入选Futures Truth Magazine Top 10

    逻辑:
    基于昨日价格计算6个关键价位:
    - 突破买入价(Bbreak) = 今日开盘 + f1 * (昨日高点 - 昨日低点)
    - 观察卖出价(Ssetup) = 今日开盘 + f2 * (昨日高点 - 昨日低点)
    - 反转卖出价(Senter) = f3/2 * (昨日高点 + 昨日低点) - f3 * 昨日低点
    - 反转买入价(Benter) = f3/2 * (昨日高点 + 昨日低点) - f3 * 昨日高点
    - 观察买入价(Bsetup) = 今日开盘 - f2 * (昨日高点 - 昨日低点)
    - 突破卖出价(Sbreak) = 今日开盘 - f1 * (昨日高点 - 昨日低点)

    交易规则:
    1. 价格>突破买入价 → 开多
    2. 价格<突破卖出价 → 开空
    3. 价格>观察卖出价后回落<反转卖出价 → 开空(反转)
    4. 价格<观察买入价后回升>反转买入价 → 开多(反转)

    Args:
        f1: 趋势突破系数(默认0.35)
        f2: 观察系数(默认0.07)
        f3: 反转系数(默认0.25)
    """
    n = len(df)
    if n < 2:
        return df.with_columns([pl.Series('signal', [0] * n)])

    opens = df['open'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    closes = df['close'].to_numpy()

    signals = []
    position = 0
    has_observed_high = False  # 是否已触及观察卖出价
    has_observed_low = False   # 是否已触及观察买入价

    for i in range(n):
        if i < 1:
            signals.append(0)
            continue

        # 使用昨日数据计算关键价位
        prev_high = highs[i-1]
        prev_low = lows[i-1]
        prev_close = closes[i-1]
        today_open = opens[i]

        day_range = prev_high - prev_low

        # 计算6个关键价位
        bbreak = today_open + f1 * day_range  # 突破买入
        ssetup = today_open + f2 * day_range  # 观察卖出
        senter = (prev_high + prev_low) / 2 - f3 * day_range  # 反转卖出
        benter = (prev_high + prev_low) / 2 + f3 * day_range  # 反转买入
        bsetup = today_open - f2 * day_range  # 观察买入
        sbreak = today_open - f1 * day_range  # 突破卖出

        current_high = highs[i]
        current_low = lows[i]
        current_close = closes[i]

        signal = 0

        # 更新观察标记
        if current_high > ssetup:
            has_observed_high = True
        if current_low < bsetup:
            has_observed_low = True

        if position == 0:
            # 空仓: 趋势突破
            if current_high >= bbreak:
                signal = 1
                position = 1
            elif current_low <= sbreak:
                signal = -1
                position = -1

        elif position == 1:
            # 多头: 检查反转或继续持有多头
            if has_observed_high and current_low <= senter:
                # 反转卖出信号
                signal = -1
                position = -1
                has_observed_high = False
            else:
                signal = 0

        elif position == -1:
            # 空头: 检查反转或继续持有空头
            if has_observed_low and current_high >= benter:
                # 反转买入信号
                signal = 1
                position = 1
                has_observed_low = False
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# ATR波动率通道突破 + 动态仓位
# ============================================================================

def atr_channel_breakout_strategy(
    df: pl.DataFrame,
    atr_period: int = 14,
    channel_period: int = 20,
    atr_multiplier: float = 2.0
) -> pl.DataFrame:
    """
    ATR通道突破策略 - 波动率自适应

    逻辑:
    - 基于N日最高/最低价构建通道
    - 使用ATR作为通道宽度动态调整
    - ATR上升表示波动增加，收窄突破条件

    Args:
        atr_period: ATR计算周期
        channel_period: 通道计算周期(唐奇安通道)
        atr_multiplier: ATR乘数
    """
    n = len(df)
    if n < channel_period + 1:
        return df.with_columns([pl.Series('signal', [0] * n)])

    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    closes = df['close'].to_numpy()

    # 计算ATR
    tr_list = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_list.append(tr)

    atr_list = [np.mean(tr_list[max(0, i-atr_period):i+1]) if i > 0 else tr_list[0]
                for i in range(len(tr_list))]
    atr_list = [atr_list[0]] + atr_list  # 补第一个值

    signals = []
    position = 0

    for i in range(n):
        if i < channel_period:
            signals.append(0)
            continue

        # 计算唐奇安通道
        hh = np.max(highs[i-channel_period:i])
        ll = np.min(lows[i-channel_period:i])

        # ATR自适应通道
        atr = atr_list[i] if i < len(atr_list) else atr_list[-1]
        upper = hh - atr_multiplier * atr * 0.5
        lower = ll + atr_multiplier * atr * 0.5

        current_close = closes[i]
        current_high = highs[i]
        current_low = lows[i]

        signal = 0

        if position == 0:
            if current_high >= upper:
                signal = 1
                position = 1
            elif current_low <= lower:
                signal = -1
                position = -1
        elif position == 1:
            if current_low <= lower:
                signal = -1
                position = -1
            else:
                signal = 0
        elif position == -1:
            if current_high >= upper:
                signal = 1
                position = 1
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 海龟交易法则 (Donchian Channel)
# ============================================================================

def turtle_strategy(
    df: pl.DataFrame,
    entry_period: int = 20,
    exit_period: int = 10,
    use_filter: bool = True
) -> pl.DataFrame:
    """
    海龟交易法则 - 经典趋势跟踪系统

    逻辑:
    - 突破entry_period日高点做多
    - 突破entry_period日低点做空
    - 跌破exit_period日低点平多
    - 涨破exit_period日高点平空
    - 可选: 使用200日均线过滤，价格>均线才做多

    Args:
        entry_period: 入场周期(经典海龟用20日)
        exit_period: 出场周期(经典海龟用10日)
        use_filter: 是否使用均线过滤
    """
    n = len(df)
    if n < entry_period + 1:
        return df.with_columns([pl.Series('signal', [0] * n)])

    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    closes = df['close'].to_numpy()

    # 计算200日均线过滤
    ma200 = np.array([np.mean(closes[max(0, i-200):i+1]) for i in range(n)]) if use_filter else None

    signals = []
    position = 0

    for i in range(n):
        if i < entry_period:
            signals.append(0)
            continue

        # 计算通道
        entry_hh = np.max(highs[i-entry_period:i])
        entry_ll = np.min(lows[i-entry_period:i])
        exit_hh = np.max(highs[i-exit_period:i]) if i >= exit_period else entry_hh
        exit_ll = np.min(lows[i-exit_period:i]) if i >= exit_period else entry_ll

        current_high = highs[i]
        current_low = lows[i]

        signal = 0
        trend_up = True
        if use_filter and ma200 is not None:
            trend_up = closes[i] > ma200[i]

        if position == 0:
            # 入场
            if current_high >= entry_hh and trend_up:
                signal = 1
                position = 1
            elif current_low <= entry_ll:
                signal = -1
                position = -1
        elif position == 1:
            # 多头出场
            if current_low <= exit_ll:
                signal = -1  # 平多
                position = 0
            else:
                signal = 0
        elif position == -1:
            # 空头出场
            if current_high >= exit_hh:
                signal = 1  # 平空
                position = 0
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 参数优化版本 Dual Thrust
# ============================================================================

def dual_thrust_optimized(df: pl.DataFrame) -> pl.DataFrame:
    """
    优化的Dual Thrust - 针对股指期货优化参数

    根据市场特性调整:
    - IF0 (沪深300): 波动较大，使用较宽通道 k1=0.6, k2=0.4
    - IC0 (中证500): 波动最大，k1=0.7, k2=0.5
    - IH0 (上证50):  波动较小，k1=0.4, k2=0.3
    """
    n = len(df)
    closes = df['close'].to_numpy()

    # 根据标的识别(通过历史波动率自适应)
    if n > 20:
        returns = np.diff(closes) / closes[:-1]
        volatility = np.std(returns[-20:]) * np.sqrt(252)

        # 根据波动率自适应参数
        if volatility > 0.25:  # 高波动
            k1, k2 = 0.7, 0.5
            n_periods = 3
        elif volatility > 0.18:  # 中等波动
            k1, k2 = 0.5, 0.4
            n_periods = 4
        else:  # 低波动
            k1, k2 = 0.4, 0.3
            n_periods = 5
    else:
        k1, k2, n_periods = 0.5, 0.5, 4

    return dual_thrust_strategy(df, n_periods=n_periods, k1=k1, k2=k2)


# ============================================================================
# 回测执行
# ============================================================================

def run_cta_backtest(code: str, strategy_name: str, strategy_func, **kwargs):
    """运行CTA策略回测"""
    print(f"\n【{code} - {strategy_name}】")
    print("-" * 60)

    # 加载数据
    data = load_futures_for_backtest(code, 'daily')
    if data.is_empty():
        print("  无数据")
        return None

    print(f"  数据量: {len(data)} 条")

    # 生成信号
    signals = strategy_func(data, **kwargs)

    # 统计
    buy_count = signals.filter(pl.col('signal') == 1).shape[0]
    sell_count = signals.filter(pl.col('signal') == -1).shape[0]
    hold_count = signals.filter(pl.col('signal') == 0).shape[0]
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
        'sharpe_ratio': result.sharpe_ratio,
        'trades': result.total_trades,
    }


def main():
    """主函数 - 经典CTA策略对比"""
    print("=" * 70)
    print("Quant Terminal 经典CTA策略回测")
    print("=" * 70)
    print("\n策略说明:")
    print("  1. Dual Thrust    - Michael Chalek, 期货Top10系统")
    print("  2. R-Breaker      - Richard Saidenberg, 反转+趋势混合")
    print("  3. ATR通道突破    - 波动率自适应突破")
    print("  4. 海龟交易法则   - 唐奇安通道经典趋势跟踪")
    print("  5. 优化Dual Thrust - 波动率自适应参数")
    print("=" * 70)

    # 下载数据
    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    # 策略列表
    strategies = [
        ('Dual Thrust', dual_thrust_strategy, {'n_periods': 4, 'k1': 0.5, 'k2': 0.5}),
        ('R-Breaker', r_breaker_strategy, {}),
        ('ATR通道突破', atr_channel_breakout_strategy, {'atr_period': 14, 'channel_period': 20}),
        ('海龟交易法则', turtle_strategy, {'entry_period': 20, 'exit_period': 10}),
        ('Dual Thrust(优化)', dual_thrust_optimized, {}),
    ]

    all_results = []

    for code in codes:
        print(f"\n{'='*70}")
        print(f"品种: {code}")
        print('='*70)

        for strategy_name, strategy_func, kwargs in strategies:
            result = run_cta_backtest(code, strategy_name, strategy_func, **kwargs)
            if result:
                all_results.append(result)

    # 汇总
    print("\n" + "=" * 70)
    print("经典CTA策略对比汇总")
    print("=" * 70)
    print(f"{'品种':<8} {'策略':<20} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
    print("-" * 70)

    for r in all_results:
        print(f"{r['code']:<8} {r['strategy']:<20} {r['total_return']:>+8.2%}  "
              f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
              f"{r['sharpe_ratio']:>6.2f}  {r['trades']:>4}")

    print("=" * 70)

    # 找出各品种最佳
    print("\n【各品种最佳策略】")
    for code in codes:
        code_results = [r for r in all_results if r['code'] == code]
        if code_results:
            best = max(code_results, key=lambda x: x['sharpe_ratio'])
            print(f"  {code}: {best['strategy']} (夏普 {best['sharpe_ratio']:.2f}, 收益 {best['total_return']:+.2%})")

    # 全局最佳
    if all_results:
        best = max(all_results, key=lambda x: x['sharpe_ratio'])
        print(f"\n【全局最佳策略】")
        print(f"  品种: {best['code']}  策略: {best['strategy']}")
        print(f"  夏普: {best['sharpe_ratio']:.2f}  总收益: {best['total_return']:+.2%}  回撤: {best['max_drawdown']:.2%}")


if __name__ == "__main__":
    main()
