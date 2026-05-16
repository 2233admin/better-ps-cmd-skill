#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal Dual Thrust 参数网格搜索

搜索参数:
- n_periods: 观察周期 [3, 4, 5, 6, 7]
- k1: 做多系数 [0.3, 0.4, 0.5, 0.6, 0.7]
- k2: 做空系数 [0.3, 0.4, 0.5, 0.6, 0.7]

目标: 最大化夏普比率
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import numpy as np
import polars as pl
from itertools import product
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings('ignore')

from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.strategy.backtest import VectorBacktester


def dual_thrust_strategy(df: pl.DataFrame, n_periods: int, k1: float, k2: float) -> pl.DataFrame:
    """Dual Thrust策略 - 参数化版本"""
    n = len(df)
    if n < n_periods + 1:
        return df.with_columns([pl.Series('signal', [0] * n)])

    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    closes = df['close'].to_numpy()
    opens = df['open'].to_numpy()

    signals = []
    position = 0

    for i in range(n):
        if i < n_periods:
            signals.append(0)
            continue

        hh = np.max(highs[i-n_periods:i])
        ll = np.min(lows[i-n_periods:i])
        hc = np.max(closes[i-n_periods:i])
        lc = np.min(closes[i-n_periods:i])

        range_val = max(hh - lc, hc - ll)
        upper = opens[i] + k1 * range_val
        lower = opens[i] - k2 * range_val

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


def grid_search_dual_thrust(code: str, data: pl.DataFrame) -> List[Dict]:
    """对单个品种进行网格搜索"""

    # 参数网格
    n_periods_list = [3, 4, 5, 6, 7]
    k1_list = [0.3, 0.4, 0.5, 0.6, 0.7]
    k2_list = [0.3, 0.4, 0.5, 0.6, 0.7]

    total_combinations = len(n_periods_list) * len(k1_list) * len(k2_list)
    print(f"  总参数组合数: {total_combinations}")

    results = []
    backtester = VectorBacktester(
        initial_capital=1_000_000,
        commission=0.0001,
        slippage=0.0002
    )

    for idx, (n_periods, k1, k2) in enumerate(product(n_periods_list, k1_list, k2_list), 1):
        signals = dual_thrust_strategy(data, n_periods, k1, k2)
        result = backtester.run(data, signals)

        results.append({
            'code': code,
            'n_periods': n_periods,
            'k1': k1,
            'k2': k2,
            'total_return': result.total_return,
            'annual_return': result.annual_return,
            'max_drawdown': result.max_drawdown,
            'sharpe_ratio': result.sharpe_ratio,
            'trades': result.total_trades,
        })

        if idx % 25 == 0:
            print(f"    进度: {idx}/{total_combinations}")

    return results


def analyze_results(results: List[Dict], code: str):
    """分析网格搜索结果"""

    print(f"\n{'='*70}")
    print(f"{code} 参数搜索分析")
    print('='*70)

    # 按夏普排序
    sorted_results = sorted(results, key=lambda x: x['sharpe_ratio'], reverse=True)

    print("\nTop 10 参数组合:")
    print(f"{'排名':<6} {'n_periods':<12} {'k1':<8} {'k2':<8} {'夏普':<10} {'收益':<10} {'回撤':<8} {'交易':<8}")
    print("-" * 80)

    for i, r in enumerate(sorted_results[:10], 1):
        print(f"{i:<6} {r['n_periods']:<12} {r['k1']:<8.1f} {r['k2']:<8.1f} "
              f"{r['sharpe_ratio']:<10.2f} {r['total_return']:>+8.2%}  "
              f"{r['max_drawdown']:>6.2%}  {r['trades']:<8}")

    # 最佳参数
    best = sorted_results[0]
    print(f"\n[最佳参数]")
    print(f"  n_periods={best['n_periods']}, k1={best['k1']}, k2={best['k2']}")
    print(f"  夏普: {best['sharpe_ratio']:.2f} | 收益: {best['total_return']:+.2%} | 回撤: {best['max_drawdown']:.2%}")

    # 参数敏感性分析
    print(f"\n[参数敏感性分析]")

    # n_periods分析
    print("\n  n_periods 影响:")
    for n in sorted(set(r['n_periods'] for r in results)):
        n_results = [r for r in results if r['n_periods'] == n]
        avg_sharpe = np.mean([r['sharpe_ratio'] for r in n_results])
        best_sharpe = max([r['sharpe_ratio'] for r in n_results])
        print(f"    {n}日: 平均夏普={avg_sharpe:+.3f}, 最佳夏普={best_sharpe:+.3f}")

    # k1分析
    print("\n  k1 (做多系数) 影响:")
    for k in sorted(set(r['k1'] for r in results)):
        k_results = [r for r in results if r['k1'] == k]
        avg_sharpe = np.mean([r['sharpe_ratio'] for r in k_results])
        best_sharpe = max([r['sharpe_ratio'] for r in k_results])
        print(f"    {k}: 平均夏普={avg_sharpe:+.3f}, 最佳夏普={best_sharpe:+.3f}")

    # k2分析
    print("\n  k2 (做空系数) 影响:")
    for k in sorted(set(r['k2'] for r in results)):
        k_results = [r for r in results if r['k2'] == k]
        avg_sharpe = np.mean([r['sharpe_ratio'] for r in k_results])
        best_sharpe = max([r['sharpe_ratio'] for r in k_results])
        print(f"    {k}: 平均夏普={avg_sharpe:+.3f}, 最佳夏普={best_sharpe:+.3f}")

    return best


def main():
    """主函数"""
    print("=" * 70)
    print("Quant Terminal Dual Thrust 参数网格搜索")
    print("=" * 70)
    print("\n搜索空间:")
    print("  n_periods: [3, 4, 5, 6, 7]")
    print("  k1 (做多): [0.3, 0.4, 0.5, 0.6, 0.7]")
    print("  k2 (做空): [0.3, 0.4, 0.5, 0.6, 0.7]")
    print("=" * 70)

    codes = ['IF0', 'IC0', 'IH0']

    # 下载数据
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    all_best_params = {}

    for code in codes:
        print(f"\n{'='*70}")
        print(f"开始搜索 {code} 最优参数...")
        print('='*70)

        data = load_futures_for_backtest(code, 'daily')
        if data.is_empty():
            print(f"  无数据，跳过")
            continue

        print(f"  数据量: {len(data)} 条")

        results = grid_search_dual_thrust(code, data)
        best = analyze_results(results, code)
        all_best_params[code] = best

    # 汇总
    print("\n" + "=" * 70)
    print("所有品种最优参数汇总")
    print("=" * 70)

    for code, params in all_best_params.items():
        print(f"\n  {code}:")
        print(f"    参数: n_periods={params['n_periods']}, k1={params['k1']}, k2={params['k2']}")
        print(f"    表现: 夏普{params['sharpe_ratio']:.2f}, 收益{params['total_return']:+.2%}, 回撤{params['max_drawdown']:.2%}")

    # 生成优化后的策略代码
    print("\n" + "=" * 70)
    print("生成的优化策略配置")
    print("=" * 70)
    print("\ndef get_optimized_params(code: str) -> dict:")
    print('    """获取优化后的Dual Thrust参数"""')
    print("    params = {")
    for code, p in all_best_params.items():
        print(f"        '{code}': {{'n_periods': {p['n_periods']}, 'k1': {p['k1']}, 'k2': {p['k2']}}},")
    print("    }")
    print("    return params.get(code, {'n_periods': 4, 'k1': 0.5, 'k2': 0.5})")


if __name__ == "__main__":
    main()
