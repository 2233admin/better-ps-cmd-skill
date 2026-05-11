#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal 自适应自修正交易系统 (Adaptive Self-Correcting System)

核心理念:
- 市场状态不是一成不变的
- 系统实时检测市场环境并自动切换参数
- 滚动评估表现，自动淘汰无效参数
- 自学习最优参数映射
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import numpy as np
import polars as pl
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.strategy.backtest import VectorBacktester


class MarketState(Enum):
    """市场状态枚举"""
    STRONG_TREND_UP = "strong_trend_up"      # 强上升趋势
    STRONG_TREND_DOWN = "strong_trend_down"  # 强下降趋势
    WEAK_TREND_UP = "weak_trend_up"          # 弱上升趋势
    WEAK_TREND_DOWN = "weak_trend_down"      # 弱下降趋势
    RANGE_BOUND = "range_bound"              # 震荡
    HIGH_VOLATILITY = "high_volatility"      # 高波动
    LOW_VOLATILITY = "low_volatility"        # 低波动


@dataclass
class StrategyConfig:
    """策略配置"""
    n_periods: int
    k1: float
    k2: float
    name: str
    performance_score: float = 0.0
    trades_count: int = 0


class MarketStateDetector:
    """市场状态检测器"""

    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def detect(self, df: pl.DataFrame, current_idx: int) -> MarketState:
        """检测当前市场状态"""
        if current_idx < self.lookback:
            return MarketState.RANGE_BOUND

        closes = df['close'].to_numpy()
        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()
        volumes = df['volume'].to_numpy()

        # 获取窗口数据
        window_closes = closes[max(0, current_idx-self.lookback):current_idx]
        window_highs = highs[max(0, current_idx-self.lookback):current_idx]
        window_lows = lows[max(0, current_idx-self.lookback):current_idx]
        window_vols = volumes[max(0, current_idx-self.lookback):current_idx]

        # 计算趋势强度 (ADX简化版)
        returns = np.diff(window_closes) / window_closes[:-1]

        # 方向性移动
        up_moves = sum(1 for r in returns if r > 0)
        down_moves = sum(1 for r in returns if r < 0)

        # 趋势强度
        trend_strength = abs(up_moves - down_moves) / len(returns) if len(returns) > 0 else 0

        # 波动率
        volatility = np.std(returns) * np.sqrt(252) if len(returns) > 1 else 0

        # 价格位置
        price_range = max(window_highs) - min(window_lows)
        price_position = (closes[current_idx-1] - min(window_lows)) / price_range if price_range > 0 else 0.5

        # 成交量趋势
        vol_ma_short = np.mean(window_vols[-5:]) if len(window_vols) >= 5 else np.mean(window_vols)
        vol_ma_long = np.mean(window_vols)
        vol_trend = vol_ma_short / vol_ma_long if vol_ma_long > 0 else 1

        # 状态判断逻辑
        if volatility > 0.30:  # 高波动
            if price_position > 0.6:
                return MarketState.HIGH_VOLATILITY
            else:
                return MarketState.HIGH_VOLATILITY

        if trend_strength > 0.6:  # 强趋势
            if price_position > 0.6 and np.mean(returns[-5:]) > 0:
                return MarketState.STRONG_TREND_UP
            elif price_position < 0.4 and np.mean(returns[-5:]) < 0:
                return MarketState.STRONG_TREND_DOWN

        if trend_strength > 0.3:  # 弱趋势
            if price_position > 0.55:
                return MarketState.WEAK_TREND_UP
            elif price_position < 0.45:
                return MarketState.WEAK_TREND_DOWN

        if volatility < 0.12:  # 低波动
            return MarketState.LOW_VOLATILITY

        return MarketState.RANGE_BOUND


class AdaptiveDualThrust:
    """
    自适应Dual Thrust策略

    特点:
    1. 多组候选参数
    2. 实时市场状态检测
    3. 表现追踪 + 自动选择最优参数
    4. 滚动学习更新参数权重
    """

    def __init__(self):
        # 候选参数池 (基于网格搜索发现的优秀参数)
        self.candidate_configs = [
            # 趋势行情参数
            StrategyConfig(n_periods=3, k1=0.7, k2=0.3, name="trend_aggressive"),
            StrategyConfig(n_periods=4, k1=0.5, k2=0.4, name="trend_standard"),
            StrategyConfig(n_periods=5, k1=0.5, k2=0.4, name="trend_conservative"),

            # 震荡行情参数
            StrategyConfig(n_periods=4, k1=0.3, k2=0.3, name="range_tight"),
            StrategyConfig(n_periods=6, k1=0.4, k2=0.4, name="range_wide"),

            # 高波动参数
            StrategyConfig(n_periods=3, k1=0.4, k2=0.4, name="highvol_quick"),
            StrategyConfig(n_periods=7, k1=0.6, k2=0.6, name="highvol_slow"),

            # 低波动参数
            StrategyConfig(n_periods=4, k1=0.3, k2=0.5, name="lowvol_asymmetric"),
        ]

        # 状态->参数的映射权重 (动态学习)
        self.state_config_weights: Dict[MarketState, Dict[str, float]] = {
            state: {cfg.name: 1.0 / len(self.candidate_configs)
                   for cfg in self.candidate_configs}
            for state in MarketState
        }

        # 状态检测器
        self.state_detector = MarketStateDetector(lookback=20)

        # 表现追踪窗口
        self.performance_window = 50
        self.recent_performances: List[Dict] = []

    def select_best_config(self, state: MarketState) -> StrategyConfig:
        """根据市场状态选择最佳配置"""
        weights = self.state_config_weights[state]

        # 选择权重最高的配置
        best_name = max(weights, key=weights.get)
        for cfg in self.candidate_configs:
            if cfg.name == best_name:
                return cfg

        return self.candidate_configs[0]

    def update_weights(self, state: MarketState, config_name: str, reward: float):
        """更新配置权重 (强化学习式更新)"""
        weights = self.state_config_weights[state]

        # 奖励好的配置
        learning_rate = 0.1
        weights[config_name] += learning_rate * reward

        # 惩罚其他配置 (轻微)
        for name in weights:
            if name != config_name:
                weights[name] -= learning_rate * reward * 0.1

        # 确保权重为正
        for name in weights:
            weights[name] = max(0.01, weights[name])

        # 归一化
        total = sum(weights.values())
        for name in weights:
            weights[name] /= total

    def generate_signal(self, df: pl.DataFrame, current_idx: int, config: StrategyConfig) -> int:
        """使用指定配置生成信号"""
        if current_idx < config.n_periods:
            return 0

        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()
        closes = df['close'].to_numpy()
        opens = df['open'].to_numpy()

        hh = np.max(highs[current_idx-config.n_periods:current_idx])
        ll = np.min(lows[current_idx-config.n_periods:current_idx])
        hc = np.max(closes[current_idx-config.n_periods:current_idx])
        lc = np.min(closes[current_idx-config.n_periods:current_idx])

        range_val = max(hh - lc, hc - ll)
        upper = opens[current_idx] + config.k1 * range_val
        lower = opens[current_idx] - config.k2 * range_val

        current_high = highs[current_idx]
        current_low = lows[current_idx]

        if current_high >= upper:
            return 1
        elif current_low <= lower:
            return -1
        return 0

    def run(self, df: pl.DataFrame) -> pl.DataFrame:
        """运行自适应策略"""
        n = len(df)
        if n < 50:
            return df.with_columns([pl.Series('signal', [0] * n)])

        closes = df['close'].to_numpy()
        signals = []
        position = 0
        current_config = None
        current_state = None
        config_entry_idx = 0

        for i in range(n):
            if i < 30:
                signals.append(0)
                continue

            # 每20日检测一次市场状态
            if i % 20 == 0 or current_state is None:
                new_state = self.state_detector.detect(df, i)

                # 状态切换时选择新配置
                if new_state != current_state:
                    current_state = new_state
                    current_config = self.select_best_config(current_state)
                    config_entry_idx = i
                    print(f"  [{i}] 状态切换: {current_state.value} -> 使用 {current_config.name}")

            # 生成信号
            raw_signal = self.generate_signal(df, i, current_config)

            # 状态机管理仓位
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

            signals.append(signal)

            # 更新配置权重 (每5日评估一次)
            if i % 5 == 0 and i > config_entry_idx + 5 and position != 0:
                # 计算当前配置的近期收益
                period_rets = []
                for j in range(config_entry_idx, min(i, n-1)):
                    if signals[j] != 0:
                        r = (closes[j+1] - closes[j]) / closes[j] * signals[j]
                        period_rets.append(r)

                if len(period_rets) > 0:
                    avg_ret = np.mean(period_rets)
                    # 根据收益更新权重
                    self.update_weights(current_state, current_config.name, avg_ret * 100)

        return df.with_columns([pl.Series('signal', signals)])


class SelfCorrectingEnsemble:
    """
    自修正集成系统

    多策略并行运行，自动识别表现最好的策略
    动态调整资金分配
    """

    def __init__(self):
        self.strategies = {
            'adaptive_dt': AdaptiveDualThrust(),
        }
        self.strategy_weights = {name: 1.0 for name in self.strategies}
        self.performance_history = {name: [] for name in self.strategies}

    def run(self, df: pl.DataFrame) -> pl.DataFrame:
        """运行自修正集成"""
        # 这里可以扩展为真正的多策略集成
        # 目前使用自适应DT作为核心
        adaptive = AdaptiveDualThrust()
        return adaptive.run(df)


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
    print("Quant Terminal 自适应自修正交易系统")
    print("=" * 70)
    print("\n核心特性:")
    print("  - 实时市场状态检测 (7种状态)")
    print("  - 动态参数切换 (8组候选配置)")
    print("  - 在线学习更新最优参数映射")
    print("  - 表现追踪与自修正")
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

        # 自适应策略
        adaptive = AdaptiveDualThrust()
        result = run_backtest(code, "Adaptive_DualThrust", adaptive.run)
        if result:
            all_results.append(result)

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

            for i in range(n):
                if i < cfg['n_periods']:
                    signals.append(0)
                    continue

                highs = df['high'].to_numpy()
                lows = df['low'].to_numpy()
                closes = df['close'].to_numpy()
                opens = df['open'].to_numpy()

                hh = np.max(highs[i-cfg['n_periods']:i])
                ll = np.min(lows[i-cfg['n_periods']:i])
                hc = np.max(closes[i-cfg['n_periods']:i])
                lc = np.min(closes[i-cfg['n_periods']:i])

                range_val = max(hh - lc, hc - ll)
                upper = opens[i] + cfg['k1'] * range_val
                lower = opens[i] - cfg['k2'] * range_val

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
    print("自适应 vs 固定参数 对比")
    print("=" * 70)
    print(f"{'品种':<8} {'策略':<20} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
    print("-" * 70)

    for r in sorted(all_results, key=lambda x: x['sharpe_ratio'], reverse=True):
        marker = " [自适应]" if "Adaptive" in r['strategy'] else " [固定]"
        print(f"{r['code']:<8} {r['strategy']:<20} {r['total_return']:>+8.2%}  "
              f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
              f"{r['sharpe_ratio']:>6.2f}  {r['trades']:>4}{marker}")

    print("=" * 70)

    # 分析
    print("\n[自适应效果分析]")
    for code in codes:
        code_results = [r for r in all_results if r['code'] == code]
        if len(code_results) >= 2:
            adaptive_r = [r for r in code_results if "Adaptive" in r['strategy']][0]
            fixed_r = [r for r in code_results if "Fixed" in r['strategy']][0]

            print(f"\n  {code}:")
            print(f"    自适应: 夏普{adaptive_r['sharpe_ratio']:.2f}")
            print(f"    固定:   夏普{fixed_r['sharpe_ratio']:.2f}")

            diff = adaptive_r['sharpe_ratio'] - fixed_r['sharpe_ratio']
            if diff > 0:
                print(f"    -> 自适应优势: +{diff:.2f} 夏普")
            else:
                print(f"    -> 固定参数优势: {-diff:.2f} 夏普")


if __name__ == "__main__":
    main()
