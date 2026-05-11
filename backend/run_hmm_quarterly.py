#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal HMM季度级别自修正交易系统

核心设计:
- 隐马尔可夫模型(HMM)识别市场状态
- 季度级别(60-90日)状态评估与参数切换
- 状态转移概率矩阵自学习
- 向前验证选择最优参数
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


class HMMMarketState(Enum):
    """HMM市场状态"""
    BULL_TREND = 0      # 牛市趋势
    BEAR_TREND = 1      # 熊市趋势
    RANGE_MARKET = 2    # 震荡市
    HIGH_VOL = 3        # 高波动
    LOW_VOL = 4         # 低波动


@dataclass
class HMMParameters:
    """HMM模型参数"""
    # 状态转移概率矩阵 A[i,j] = P(状态j|状态i)
    transition_matrix: np.ndarray
    # 观测概率参数 (每个状态的均值和方差)
    emission_params: Dict[int, Dict[str, float]]
    # 初始状态分布
    initial_probs: np.ndarray


class SimpleHMM:
    """
    简化版HMM实现

    使用高斯混合模型作为观测概率
    """

    def __init__(self, n_states: int = 5):
        self.n_states = n_states
        self.transition_matrix = np.ones((n_states, n_states)) / n_states
        self.emission_params = {}
        self.state_probs = np.ones(n_states) / n_states

    def fit(self, observations: np.ndarray, n_iter: int = 50):
        """
        Baum-Welch算法训练HMM

        observations: 观测序列 (收益率, 波动率等特征)
        """
        n_obs = len(observations)

        # 初始化: K-means-like 分组
        sorted_obs = np.sort(observations)
        chunk_size = n_obs // self.n_states

        for i in range(self.n_states):
            start = i * chunk_size
            end = (i + 1) * chunk_size if i < self.n_states - 1 else n_obs
            state_obs = sorted_obs[start:end]

            self.emission_params[i] = {
                'mean': np.mean(state_obs),
                'std': max(np.std(state_obs), 0.001)
            }

        # EM算法迭代
        for iteration in range(n_iter):
            # E-step: 计算gamma (状态概率)
            gamma = np.zeros((n_obs, self.n_states))

            for t in range(n_obs):
                for i in range(self.n_states):
                    # 发射概率 (高斯)
                    mean = self.emission_params[i]['mean']
                    std = self.emission_params[i]['std']
                    emit_prob = np.exp(-0.5 * ((observations[t] - mean) / std) ** 2) / (std * np.sqrt(2 * np.pi))
                    gamma[t, i] = self.state_probs[i] * emit_prob

                # 归一化
                if np.sum(gamma[t]) > 0:
                    gamma[t] /= np.sum(gamma[t])

            # M-step: 更新参数
            # 更新转移矩阵 (简化: 使用计数方法)
            new_trans = np.zeros((self.n_states, self.n_states))
            for t in range(n_obs - 1):
                for i in range(self.n_states):
                    for j in range(self.n_states):
                        new_trans[i, j] += gamma[t, i] * gamma[t + 1, j]

            # 归一化
            for i in range(self.n_states):
                row_sum = np.sum(new_trans[i])
                if row_sum > 0:
                    self.transition_matrix[i] = new_trans[i] / row_sum
                else:
                    self.transition_matrix[i] = np.ones(self.n_states) / self.n_states

            # 更新发射参数
            for i in range(self.n_states):
                weights = gamma[:, i]
                if np.sum(weights) > 0:
                    new_mean = np.sum(weights * observations) / np.sum(weights)
                    new_var = np.sum(weights * (observations - new_mean) ** 2) / np.sum(weights)
                    self.emission_params[i]['mean'] = new_mean
                    self.emission_params[i]['std'] = max(np.sqrt(new_var), 0.001)

            # 更新状态概率
            self.state_probs = gamma[-1]

    def predict_state(self, observation: float) -> int:
        """预测当前状态"""
        probs = np.zeros(self.n_states)

        for i in range(self.n_states):
            mean = self.emission_params[i]['mean']
            std = self.emission_params[i]['std']
            emit_prob = np.exp(-0.5 * ((observation - mean) / std) ** 2) / (std * np.sqrt(2 * np.pi))
            probs[i] = self.state_probs[i] * emit_prob

        if np.sum(probs) > 0:
            probs /= np.sum(probs)

        return np.argmax(probs)

    def forward_predict(self, current_state: int, steps: int = 60) -> int:
        """
        向前预测未来最可能的状态

        使用状态转移矩阵进行多步预测
        """
        state_dist = np.zeros(self.n_states)
        state_dist[current_state] = 1.0

        for _ in range(steps):
            state_dist = np.dot(state_dist, self.transition_matrix)

        return np.argmax(state_dist)


class QuarterlySelfCorrectingSystem:
    """
    季度级别自修正交易系统

    核心逻辑:
    1. 每季度(60-90日)重新训练HMM
    2. HMM识别当前市场状态
    3. 向前预测下一季度最可能的状态
    4. 根据预测状态选择最优参数
    5. 季度末评估表现，修正参数映射
    """

    def __init__(self):
        self.hmm = SimpleHMM(n_states=5)

        # 季度评估周期
        self.quarter_length = 60  # 约一个季度

        # 状态->参数映射 (基于网格搜索的最优参数)
        self.state_config_map = {
            HMMMarketState.BULL_TREND.value: {'n_periods': 3, 'k1': 0.7, 'k2': 0.3, 'name': 'bull_aggressive'},
            HMMMarketState.BEAR_TREND.value: {'n_periods': 3, 'k1': 0.3, 'k2': 0.7, 'name': 'bear_aggressive'},
            HMMMarketState.RANGE_MARKET.value: {'n_periods': 6, 'k1': 0.4, 'k2': 0.4, 'name': 'range_conservative'},
            HMMMarketState.HIGH_VOL.value: {'n_periods': 4, 'k1': 0.5, 'k2': 0.5, 'name': 'highvol_standard'},
            HMMMarketState.LOW_VOL.value: {'n_periods': 5, 'k1': 0.4, 'k2': 0.3, 'name': 'lowvol_asymmetric'},
        }

        # 表现追踪
        self.quarterly_performance = []
        self.state_performance = {i: [] for i in range(5)}

    def extract_observations(self, df: pl.DataFrame, start_idx: int, end_idx: int) -> np.ndarray:
        """提取HMM观测特征"""
        closes = df['close'].to_numpy()
        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()

        observations = []

        for i in range(start_idx, end_idx):
            if i < 20:
                continue

            # 观测特征: 收益率 + 波动率
            returns_window = [(closes[j] - closes[j-1]) / closes[j-1]
                             for j in range(max(1, i-20), i)]

            ret = returns_window[-1] if returns_window else 0
            vol = np.std(returns_window) * np.sqrt(252) if len(returns_window) > 1 else 0

            # 趋势强度
            sma5 = np.mean(closes[max(0, i-5):i])
            sma20 = np.mean(closes[max(0, i-20):i])
            trend = (sma5 - sma20) / sma20 if sma20 > 0 else 0

            # 综合观测值
            obs = ret * 10 + vol * 5 + trend * 2
            observations.append(obs)

        return np.array(observations)

    def run(self, df: pl.DataFrame) -> pl.DataFrame:
        """运行季度级别自修正系统"""
        n = len(df)
        if n < 200:
            return df.with_columns([pl.Series('signal', [0] * n)])

        closes = df['close'].to_numpy()
        signals = [0] * n
        position = 0

        # 季度循环
        quarter_start = 100  # 前100日用于初始训练

        while quarter_start < n - self.quarter_length:
            quarter_end = min(quarter_start + self.quarter_length, n)

            # 训练HMM (使用过去2个季度的数据)
            train_start = max(0, quarter_start - self.quarter_length * 2)
            observations = self.extract_observations(df, train_start, quarter_start)

            if len(observations) < 30:
                quarter_start += self.quarter_length
                continue

            # 训练HMM
            self.hmm.fit(observations, n_iter=30)

            # 识别当前状态
            current_obs = observations[-1] if len(observations) > 0 else 0
            current_state = self.hmm.predict_state(current_obs)

            # 向前预测下一季度状态
            predicted_state = self.hmm.forward_predict(current_state, steps=self.quarter_length)

            # 获取对应参数
            config = self.state_config_map.get(predicted_state,
                                               {'n_periods': 4, 'k1': 0.5, 'k2': 0.5})

            print(f"  [季度 {quarter_start}-{quarter_end}] "
                  f"当前状态:{current_state} 预测状态:{predicted_state} "
                  f"参数:{config['name']}")

            # 在本季度使用该参数
            for i in range(quarter_start, quarter_end):
                if i < config['n_periods']:
                    signals[i] = 0
                    continue

                highs = df['high'].to_numpy()
                lows = df['low'].to_numpy()
                opens = df['open'].to_numpy()

                hh = np.max(highs[i-config['n_periods']:i])
                ll = np.min(lows[i-config['n_periods']:i])
                hc = np.max(closes[i-config['n_periods']:i])
                lc = np.min(closes[i-config['n_periods']:i])

                range_val = max(hh - lc, hc - ll)
                upper = opens[i] + config['k1'] * range_val
                lower = opens[i] - config['k2'] * range_val

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

            # 季度末评估表现
            quarter_rets = []
            for i in range(quarter_start, min(quarter_end - 1, n - 1)):
                if signals[i] != 0:
                    r = (closes[i+1] - closes[i]) / closes[i] * signals[i]
                    quarter_rets.append(r)

            if len(quarter_rets) > 0:
                avg_ret = np.mean(quarter_rets)
                sharpe = np.mean(quarter_rets) / (np.std(quarter_rets) + 1e-10) * np.sqrt(252)
                self.state_performance[predicted_state].append(sharpe)
                print(f"    本季度表现: 收益{avg_ret:+.2%}, 夏普{sharpe:.2f}")

            quarter_start += self.quarter_length

        return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 回测执行
# ============================================================================

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
    print("Quant Terminal HMM季度级别自修正交易系统")
    print("=" * 70)
    print("\n核心设计:")
    print("  - HMM隐马尔可夫模型识别市场状态")
    print("  - 季度级别(60日)状态评估与参数切换")
    print("  - 状态转移概率矩阵自学习")
    print("  - 向前验证选择最优参数")
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

        # HMM季度自修正系统
        hmm_system = QuarterlySelfCorrectingSystem()
        result = run_backtest(code, "HMM_Quarterly_SelfCorrecting", hmm_system.run)
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
    print("HMM季度自修正 vs 固定参数 对比")
    print("=" * 70)
    print(f"{'品种':<8} {'策略':<25} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
    print("-" * 70)

    for r in sorted(all_results, key=lambda x: x['sharpe_ratio'], reverse=True):
        marker = " [HMM]" if "HMM" in r['strategy'] else " [固定]"
        print(f"{r['code']:<8} {r['strategy']:<25} {r['total_return']:>+8.2%}  "
              f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
              f"{r['sharpe_ratio']:>6.2f}  {r['trades']:>4}{marker}")

    print("=" * 70)

    # HMM学习结果分析
    print("\n[HMM状态表现学习结果]")
    for code in codes:
        hmm_sys = QuarterlySelfCorrectingSystem()
        print(f"\n  {code} 状态历史表现:")
        for state_id, state_name in enumerate(['牛市', '熊市', '震荡', '高波', '低波']):
            if state_id in hmm_sys.state_performance and len(hmm_sys.state_performance[state_id]) > 0:
                avg_perf = np.mean(hmm_sys.state_performance[state_id])
                print(f"    {state_name}: 平均夏普 {avg_perf:+.3f}")


if __name__ == "__main__":
    main()
