#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal 全指标期货回测

使用项目内置的所有技术指标：
- MACD, KDJ, RSI, BOLL
- MA, EMA, BBI
- ATR, WR, CCI
- OBV, BIAS, ROC
- VWAP (来自 factors)
- 自定义多因子组合
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import numpy as np
import polars as pl
from datetime import datetime
from typing import Dict, List

from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.data.indicators import calc_indicators
from app.strategy.factors import calc_all_factors
from app.strategy.backtest import VectorBacktester


def prepare_kline_data(df: pl.DataFrame) -> List[dict]:
    """将 Polars DataFrame 转换为 kline 格式"""
    kline = []
    for i in range(len(df)):
        row = df.slice(i, 1)
        kline.append({
            'open': float(row[0, 'open']) if 'open' in row.columns else 0,
            'high': float(row[0, 'high']) if 'high' in row.columns else 0,
            'low': float(row[0, 'low']) if 'low' in row.columns else 0,
            'close': float(row[0, 'close']) if 'close' in row.columns else 0,
            'vol': float(row[0, 'volume']) if 'volume' in row.columns else 0,
        })
    return kline


def multi_factor_strategy(df: pl.DataFrame, indicators: Dict) -> pl.DataFrame:
    """
    多因子综合策略

    综合多个指标的信号：
    - MACD: 趋势方向
    - RSI: 超买超卖
    - BOLL: 区间位置
    - KDJ: 动量确认
    - MA: 趋势强度
    """
    n = len(df)
    signals = []

    # 获取各指标数据
    macd = indicators.get('MACD', {})
    rsi = indicators.get('RSI', {})
    boll = indicators.get('BOLL', {})
    kdj = indicators.get('KDJ', {})
    ma = indicators.get('MA', {})

    macd_line = macd.get('MACD', [0] * n)
    rsi6 = rsi.get('RSI6', [50] * n)
    rsi12 = rsi.get('RSI12', [50] * n)
    upper = boll.get('UPPER', [0] * n)
    lower = boll.get('LOWER', [0] * n)
    k_line = kdj.get('K', [50] * n)
    d_line = kdj.get('D', [50] * n)
    ma5 = ma.get('MA5', [0] * n)
    ma20 = ma.get('MA20', [0] * n)

    closes = df['close'].to_numpy()

    for i in range(n):
        if i < 30:  # 前30天数据不足
            signals.append(0)
            continue

        score = 0
        reasons = []

        # MACD 信号
        if macd_line[i] > 0 and macd_line[i-1] <= 0:
            score += 2
            reasons.append('MACD金叉')
        elif macd_line[i] < 0 and macd_line[i-1] >= 0:
            score -= 2
            reasons.append('MACD死叉')
        elif macd_line[i] > 0:
            score += 1
        else:
            score -= 1

        # RSI 信号
        if rsi6[i] < 30:
            score += 2
            reasons.append('RSI超卖')
        elif rsi6[i] > 70:
            score -= 2
            reasons.append('RSI超买')

        # 布林带信号
        if closes[i] < lower[i]:
            score += 1.5
            reasons.append('跌破下轨')
        elif closes[i] > upper[i]:
            score -= 1.5
            reasons.append('突破上轨')

        # KDJ 信号
        if k_line[i] > d_line[i] and k_line[i-1] <= d_line[i-1] and k_line[i] < 30:
            score += 1.5
            reasons.append('KDJ低位金叉')
        elif k_line[i] < d_line[i] and k_line[i-1] >= d_line[i-1] and k_line[i] > 70:
            score -= 1.5
            reasons.append('KDJ高位死叉')

        # 均线信号
        if ma5[i] > ma20[i]:
            score += 1
        else:
            score -= 1

        # 综合判断
        if score >= 4:
            signals.append(1)
        elif score <= -4:
            signals.append(-1)
        else:
            signals.append(0)

    return df.with_columns([
        pl.Series('signal', signals)
    ])


def macd_rsi_combo_strategy(df: pl.DataFrame, indicators: Dict) -> pl.DataFrame:
    """
    MACD + RSI 组合策略

    经典组合：
    - MACD确认趋势方向
    - RSI确认买卖点
    """
    n = len(df)
    signals = []

    macd = indicators.get('MACD', {})
    rsi = indicators.get('RSI', {})

    macd_line = macd.get('MACD', [0] * n)
    dif = macd.get('DIF', [0] * n)
    dea = macd.get('DEA', [0] * n)
    rsi6 = rsi.get('RSI6', [50] * n)

    for i in range(n):
        if i < 26:
            signals.append(0)
            continue

        # MACD金叉 + RSI从超卖区回升
        if (macd_line[i] > 0 and macd_line[i-1] <= 0 and
            rsi6[i] > 30 and rsi6[i-1] <= 35):
            signals.append(1)
        # MACD死叉 + RSI从超买区回落
        elif (macd_line[i] < 0 and macd_line[i-1] >= 0 and
              rsi6[i] < 70 and rsi6[i-1] >= 65):
            signals.append(-1)
        else:
            signals.append(0)

    return df.with_columns([
        pl.Series('signal', signals)
    ])


def bollinger_reversal_strategy(df: pl.DataFrame, indicators: Dict) -> pl.DataFrame:
    """
    布林带反转策略

    逻辑：
    - 价格触及下轨 + 缩量 = 买入
    - 价格触及上轨 + 放量 = 卖出
    """
    n = len(df)
    signals = []

    boll = indicators.get('BOLL', {})
    upper = boll.get('UPPER', [0] * n)
    lower = boll.get('LOWER', [0] * n)
    mid = boll.get('MID', [0] * n)

    closes = df['close'].to_numpy()
    volumes = df['volume'].to_numpy()

    for i in range(n):
        if i < 20:
            signals.append(0)
            continue

        vol_avg = np.mean(volumes[i-5:i])

        # 触及下轨，缩量
        if closes[i] < lower[i] * 1.01 and volumes[i] < vol_avg * 0.9:
            signals.append(1)
        # 触及上轨，放量
        elif closes[i] > upper[i] * 0.99 and volumes[i] > vol_avg * 1.1:
            signals.append(-1)
        else:
            signals.append(0)

    return df.with_columns([
        pl.Series('signal', signals)
    ])


def momentum_breakout_strategy(df: pl.DataFrame, indicators: Dict) -> pl.DataFrame:
    """
    动量突破策略

    使用 ROC + MA 确认
    """
    n = len(df)
    signals = []

    roc_data = indicators.get('ROC', {})
    roc = roc_data.get('ROC', [0] * n)

    ma = indicators.get('MA', {})
    ma20 = ma.get('MA20', [0] * n)

    closes = df['close'].to_numpy()
    volumes = df['volume'].to_numpy()

    for i in range(n):
        if i < 20:
            signals.append(0)
            continue

        vol_avg = np.mean(volumes[i-10:i])

        # ROC转正 + 放量 + 站上MA20
        if (roc[i] > 0 and roc[i-1] <= 0 and
            volumes[i] > vol_avg * 1.5 and
            closes[i] > ma20[i]):
            signals.append(1)
        # ROC转负 + 放量跌破MA20
        elif (roc[i] < 0 and roc[i-1] >= 0 and
              volumes[i] > vol_avg * 1.5 and
              closes[i] < ma20[i]):
            signals.append(-1)
        else:
            signals.append(0)

    return df.with_columns([
        pl.Series('signal', signals)
    ])


def run_indicator_backtest(code: str, strategy_name: str, strategy_func):
    """运行指定策略回测"""
    print(f"\n【{code} - {strategy_name}】")
    print("-" * 60)

    # 加载数据
    data = load_futures_for_backtest(code, 'daily')
    if data.is_empty():
        print("  无数据")
        return None

    print(f"  数据量: {len(data)}")

    # 准备K线数据
    kline = prepare_kline_data(data)

    # 计算所有指标
    indicators = calc_indicators(kline, indicators=[
        'MACD', 'KDJ', 'RSI', 'BOLL', 'MA', 'EMA', 'ROC', 'ATR', 'WR', 'CCI'
    ])

    if not indicators:
        print("  指标计算失败")
        return None

    # 生成信号
    data_with_signal = strategy_func(data, indicators)

    # 统计
    buy_count = data_with_signal.filter(pl.col('signal') == 1).shape[0]
    sell_count = data_with_signal.filter(pl.col('signal') == -1).shape[0]
    print(f"  买入: {buy_count} 次, 卖出: {sell_count} 次")

    # 回测
    backtester = VectorBacktester(
        initial_capital=1_000_000,
        commission=0.0001,
        slippage=0.0002
    )

    result = backtester.run(data, data_with_signal)

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
    """主函数 - 全指标策略对比"""
    print("=" * 70)
    print("Quant Terminal 全指标期货策略回测")
    print("=" * 70)

    # 下载数据
    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    # 策略列表
    strategies = [
        ('多因子综合', multi_factor_strategy),
        ('MACD+RSI组合', macd_rsi_combo_strategy),
        ('布林带反转', bollinger_reversal_strategy),
        ('动量突破', momentum_breakout_strategy),
    ]

    all_results = []

    for code in codes:
        print(f"\n{'='*70}")
        print(f"品种: {code}")
        print('='*70)

        for strategy_name, strategy_func in strategies:
            result = run_indicator_backtest(code, strategy_name, strategy_func)
            if result:
                all_results.append(result)

    # 汇总
    print("\n" + "=" * 70)
    print("策略对比汇总")
    print("=" * 70)
    print(f"{'品种':<8} {'策略':<16} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
    print("-" * 70)

    for r in all_results:
        print(f"{r['code']:<8} {r['strategy']:<16} {r['total_return']:>+8.2%}  "
              f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
              f"{r['sharpe_ratio']:>6.2f}  {r['trades']:>4}")

    print("=" * 70)

    # 找出最佳
    if all_results:
        best = max(all_results, key=lambda x: x['sharpe_ratio'])
        print(f"\n最佳策略 (按夏普比率):")
        print(f"  品种: {best['code']}  策略: {best['strategy']}")
        print(f"  夏普: {best['sharpe_ratio']:.2f}  总收益: {best['total_return']:+.2%}")


if __name__ == "__main__":
    main()
