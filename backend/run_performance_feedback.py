#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal 表现反馈自适应系统 (Performance-Feedback Adaptive System)

核心设计:
- 每季度末评估所有候选参数的表现
- 选择表现最好的参数用于下一季度
- 在线学习: 自动发现各品种的最优参数序列
- 无需预设状态->参数映射
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import numpy as np
import polars as pl
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings('ignore')

from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.strategy.backtest import VectorBacktester


@dataclass
class ParameterConfig:
    """参数配置"""
    n_periods: int
    k1: float
    k2: float
    name: str
    performance_history: List[float] = field(default_factory=list)


class PerformanceFeedbackAdaptiveSystem:
    """
    表现反馈自适应系统

    每季度:
    1. 用所有候选参数回测上一季度
    2. 选择夏普最高的参数
    3. 用于下一季度交易
    4. 季度末评估实际表现
    5. 更新参数历史表现记录
    """

    def __init__(self):
        # 候选参数池 (基于网格搜索发现的优秀参数组合)
        self.candidate_configs = [
            ParameterConfig(n_periods=3, k1=0.7, k2=0.3, name="aggressive_bull"),
            ParameterConfig(n_periods=3, k1=0.3, k2=0.7, name="aggressive_bear"),
            ParameterConfig(n_periods=4, k1=0.3, k2=0.3, name="if0_optimal"),
            ParameterConfig(n_periods=4, k1=0.4, k2=0.4, name="ih0_optimal"),
            ParameterConfig(n_periods=5, k1=0.5, k2=0.4, name="ic0_optimal"),
            ParameterConfig(n_periods=4, k1=0.5, k2=0.4, name="balanced"),
            ParameterConfig(n_periods=5, k1=0.4, k2=0.4, name="conservative"),
            ParameterConfig(n_periods=6, k1=0.4, k2=0.4, name="range_friendly"),
        ]

        self.quarter_length = 60  # 季度长度
        self.lookback_quarters = 4  # 回顾4个季度选择最优

        # 记录每季度的选择和实际表现
        self.selection_history: List[Dict] = []

    def evaluate_config(self, df: pl.DataFrame, config: ParameterConfig,
                       start_idx: int, end_idx: int) -> float:
        """评估某参数在指定区间的表现"""
        closes = df['close'].to_numpy()
        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()
        opens = df['open'].to_numpy()

        returns = []
        position = 0

        for i in range(start_idx, end_idx):
            if i < config.n_periods:
                continue

            hh = np.max(highs[i-config.n_periods:i])
            ll = np.min(lows[i-config.n_periods:i])
            hc = np.max(closes[i-config.n_periods:i])
            lc = np.min(closes[i-config.n_periods:i])

            range_val = max(hh - lc, hc - ll)
            upper = opens[i] + config.k1 * range_val
            lower = opens[i] - config.k2 * range_val

            current_high = highs[i]
            current_low = lows[i]

            raw_signal = 0
            if current_high >= upper:
                raw_signal = 1
            elif current_low <= lower:
                raw_signal = -1

            signal = 0
            if position == 0:
                if raw_signal == 1:
                    signal = 1
                    position = 1
                elif raw_signal == -1:
                    signal = -1
                    position = -1
            elif position == 1:
                if raw_signal == -1:
                    signal = -1
                    position = -1
                else:
                    signal = 0
            elif position == -1:
                if raw_signal == 1:
                    signal = 1
                    position = 1
                else:
                    signal = 0

            if signal != 0 and i < end_idx - 1:
                ret = (closes[i+1] - closes[i]) / closes[i] * signal
                returns.append(ret)

        if len(returns) < 3:
            return -999

        sharpe = np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252)
        return sharpe

    def select_best_config(self, df: pl.DataFrame, current_idx: int) -> ParameterConfig:
        """基于过去表现选择最优参数"""
        # 回顾过去几个季度
        lookback_start = max(0, current_idx - self.quarter_length * self.lookback_quarters)

        best_config = None
        best_sharpe = -999

        print(f"\n    [参数评估 {lookback_start}-{current_idx}]")

        for config in self.candidate_configs:
            # 评估该参数在过去几个季度的平均表现
            quarter_sharpes = []

            for q in range(self.lookback_quarters):
                q_start = current_idx - (q + 1) * self.quarter_length
                q_end = current_idx - q * self.quarter_length

                if q_start < lookback_start:
                    continue

                sharpe = self.evaluate_config(df, config, q_start, q_end)
                if sharpe > -100:  # 有效评估
                    quarter_sharpes.append(sharpe)

            # 计算平均表现
            if len(quarter_sharpes) > 0:
                avg_sharpe = np.mean(quarter_sharpes)
                config.performance_history.append(avg_sharpe)

                print(f"      {config.name:20s}: 平均夏普 {avg_sharpe:+.3f}")

                if avg_sharpe > best_sharpe:
                    best_sharpe = avg_sharpe
                    best_config = config
            else:
                # 无历史数据时，使用默认保守参数
                if best_config is None:
                    best_config = config

        if best_config is None:
            best_config = self.candidate_configs[4]  # 默认使用ic0_optimal

        print(f"    -> 选择: {best_config.name} (夏普{best_sharpe:+.3f})")

        return best_config

    def run(self, df: pl.DataFrame) -> pl.DataFrame:
        """运行动力反馈自适应系统"""
        n = len(df)
        if n < 300:
            return df.with_columns([pl.Series('signal', [0] * n)])

        closes = df['close'].to_numpy()
        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()
        opens = df['open'].to_numpy()

        signals = [0] * n
        position = 0

        # 初始化: 前200日用于初始参数选择
        current_idx = 200
        current_config = None

        while current_idx < n - self.quarter_length:
            quarter_end = min(current_idx + self.quarter_length, n)

            # 选择本季度使用的参数 (基于过去表现)
            current_config = self.select_best_config(df, current_idx)

            print(f"  [季度 {current_idx}-{quarter_end}] 使用参数: {current_config.name}")

            # 在本季度使用该参数
            for i in range(current_idx, quarter_end):
                if i < current_config.n_periods:
                    signals[i] = 0
                    continue

                hh = np.max(highs[i-current_config.n_periods:i])
                ll = np.min(lows[i-current_config.n_periods:i])
                hc = np.max(closes[i-current_config.n_periods:i])
                lc = np.min(closes[i-current_config.n_periods:i])

                range_val = max(hh - lc, hc - ll)
                upper = opens[i] + current_config.k1 * range_val
                lower = opens[i] - current_config.k2 * range_val

                current_high = highs[i]
                current_low = lows[i]

                raw_signal = 0
                if current_high >= upper:
                    raw_signal = 1
                elif current_low <= lower:
                    raw_signal = -1

                # 状态机
                if position == 0:
                    if raw_signal == 1:
                        signal = 1
                        position = 1
                    elif raw_signal == -1:
                        signal = -1
                        position = -1
                    else:
                        signal = 0
                elif position == 1:
                    if raw_signal == -1:
                        signal = -1
                        position = -1
                    else:
                        signal = 0
                elif position == -1:
                    if raw_signal == 1:
                        signal = 1
                        position = 1
                    else:
                        signal = 0

                signals[i] = signal

            # 季度末: 评估本季度实际表现
            quarter_rets = []
            for i in range(current_idx, min(quarter_end - 1, n - 1)):
                if signals[i] != 0:
                    r = (closes[i+1] - closes[i]) / closes[i] * signals[i]
                    quarter_rets.append(r)

            if len(quarter_rets) > 0:
                actual_sharpe = np.mean(quarter_rets) / (np.std(quarter_rets) + 1e-10) * np.sqrt(252)
                actual_return = np.sum(quarter_rets)

                self.selection_history.append({
                    'quarter_start': current_idx,
                    'config_name': current_config.name,
                    'actual_sharpe': actual_sharpe,
                    'actual_return': actual_return,
                })

                print(f"    本季度实际: 收益{actual_return:+.2%}, 夏普{actual_sharpe:+.3f}")

            current_idx += self.quarter_length

        return df.with_columns([pl.Series('signal', signals)])


def run_backtest(code: str, strategy_name: str, strategy_func, **kwargs):
    """运行回测"""
    print(f"\n【{code} - {strategy_name}】")
    print("-" * 60)

    data = load_futures_for_backtest(code, 'daily')
    if data.is_empty():
        print("  无数据")
        return None

    print(f"  数据量: {len(data)} 条")

    signals = strategy_func(data, **kwargs)

    buy_count = signals.filter(pl.col('signal') == 1).shape[0]
    sell_count = signals.filter(pl.col('signal') == -1).shape[0]
    print(f"  买入: {buy_count} 次, 卖出: {sell_count} 次")

    backtester = VectorBacktester(
        initial_capital=1_000_000,
        commission=0.0001,
        slippage=0.0002
    )

    result = backtester.run(data, signals)

    print(f"  总收益: {result.total_return:+.2%}  年化: {result.annual_return:+.2%}")
    print(f"  回撤: {result.max_drawdown:.2%}  夏普: {result.sharpe_ratio:.2f}  交易: {result.total_trades}")

    return {
        'code': code, 'strategy': strategy_name,
        'total_return': result.total_return, 'annual_return': result.annual_return,
        'max_drawdown': result.max_drawdown, 'sharpe_ratio': result.sharpe_ratio,
        'trades': result.total_trades,
    }


def main():
    """主函数"""
    print("=" * 70)
    print("Quant Terminal 表现反馈自适应系统")
    print("=" * 70)
    print("\n核心设计:")
    print("  - 每季度末评估所有候选参数")
    print("  - 选择历史表现最优的参数用于下季度")
    print("  - 在线学习最优参数序列")
    print("  - 无需预设状态映射")
    print("=" * 70)

    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    all_results = []

    for code in codes:
        print(f"\n{'='*70}")
        print(f"品种: {code}")
        print('='*70)

        # 表现反馈自适应系统
        adaptive_system = PerformanceFeedbackAdaptiveSystem()
        result = run_backtest(code, "Performance_Feedback_Adaptive", adaptive_system.run)
        if result:
            all_results.append(result)

            # 打印选择历史
            print(f"\n  [参数选择历史]")
            for sel in adaptive_system.selection_history:
                print(f"    季度{sel['quarter_start']}: {sel['config_name']:20s} "
                      f"-> 夏普{sel['actual_sharpe']:+.3f}")

        # 对比: 固定最优参数
        def fixed_optimal(df):
            configs = {
                'IF0': {'n_periods': 4, 'k1': 0.3, 'k2': 0.3},
                'IC0': {'n_periods': 5, 'k1': 0.5, 'k2': 0.4},
                'IH0': {'n_periods': 4, 'k1': 0.4, 'k2': 0.4},
            }
            cfg = configs.get(code, {'n_periods': 4, 'k1': 0.5, 'k2': 0.5})
            n = len(df)
            signals = []
            position = 0
            closes = df['close'].to_numpy()

            for i in range(n):
                if i < cfg['n_periods']:
                    signals.append(0)
                    continue

                highs = df['high'].to_numpy()
                lows = df['low'].to_numpy()
                opens = df['open'].to_numpy()

                hh = np.max(highs[i-cfg['n_periods']:i])
                ll = np.min(lows[i-cfg['n_periods']:i])
                hc = np.max(closes[i-cfg['n_periods']:i])
                lc = np.min(closes[i-cfg['n_periods']:i])

                range_val = max(hh - lc, hc - ll)
                upper = opens[i] + cfg['k1'] * range_val
                lower = opens[i] + cfg['k2'] * range_val

                signal = 0
                if position == 0:
                    if highs[i] >= upper:
                        signal = 1
                        position = 1
                    elif lows[i] <= lower:
                        signal = -1
                        position = -1
                elif position == 1:
                    if lows[i] <= lower:
                        signal = -1
                        position = -1
                    else:
                        signal = 0
                elif position == -1:
                    if highs[i] >= upper:
                        signal = 1
                        position = 1
                    else:
                        signal = 0

                signals.append(signal)

            return df.with_columns([pl.Series('signal', signals)])

        result_fixed = run_backtest(code, "Fixed_Optimal", fixed_optimal)
        if result_fixed:
            all_results.append(result_fixed)

    # 汇总
    print("\n" + "=" * 70)
    print("表现反馈自适应 vs 固定参数 对比")
    print("=" * 70)
    print(f"{'品种':<8} {'策略':<28} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
    print("-" * 70)

    for r in sorted(all_results, key=lambda x: x['sharpe_ratio'], reverse=True):
        marker = " [自适应]" if "Performance" in r['strategy'] else " [固定]"
        print(f"{r['code']:<8} {r['strategy']:<28} {r['total_return']:>+8.2%}  "
              f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
              f"{r['sharpe_ratio']:>6.2f}  {r['trades']:>4}{marker}")

    print("=" * 70)

    # 分析
    print("\n[自适应效果分析]")
    for code in codes:
        code_results = [r for r in all_results if r['code'] == code]
        if len(code_results) >= 2:
            adaptive_r = [r for r in code_results if "Performance" in r['strategy']][0]
            fixed_r = [r for r in code_results if "Fixed" in r['strategy']][0]

            print(f"\n  {code}:")
            print(f"    表现反馈自适应: 夏普{adaptive_r['sharpe_ratio']:.2f}")
            print(f"    固定最优:       夏普{fixed_r['sharpe_ratio']:.2f}")

            diff = adaptive_r['sharpe_ratio'] - fixed_r['sharpe_ratio']
            if diff > 0:
                print(f"    -> 自适应优势: +{diff:.2f} 夏普")
            else:
                print(f"    -> 固定参数优势: {-diff:.2f} 夏普")


if __name__ == "__main__":
    main()
