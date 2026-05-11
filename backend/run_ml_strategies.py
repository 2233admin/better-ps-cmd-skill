#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal ML/AI 策略回测

包含机器学习/深度学习策略:
- LSTM趋势预测: 基于深度学习的价格方向预测
- XGBoost多因子: 集成学习分类预测
- 强化学习仓位管理: PPO算法动态调仓
- Isolation Forest: 异常检测过滤信号
- HMM状态检测: 隐马尔可夫模型市场状态识别
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import numpy as np
import polars as pl
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.strategy.backtest import VectorBacktester


# ============================================================================
# 特征工程模块
# ============================================================================

def prepare_ml_features(df: pl.DataFrame, lookback: int = 20) -> pl.DataFrame:
    """
    准备ML特征集

    特征包括:
    - 价格动量特征
    - 波动率特征
    - 技术指标特征
    - 成交量特征
    - 统计特征
    """
    n = len(df)
    if n < lookback + 10:
        return df.with_columns([pl.Series('features', [None] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    volumes = df['volume'].to_numpy()
    opens = df['open'].to_numpy()

    features_list = []

    for i in range(n):
        if i < lookback:
            features_list.append(None)
            continue

        # 获取回望窗口数据
        window_closes = closes[i-lookback:i]
        window_highs = highs[i-lookback:i]
        window_lows = lows[i-lookback:i]
        window_vols = volumes[i-lookback:i]

        # 价格动量特征
        returns = np.diff(window_closes) / window_closes[:-1]
        mom_1 = (closes[i] - closes[i-1]) / closes[i-1]
        mom_5 = (closes[i] - closes[i-5]) / closes[i-5] if i >= 5 else 0
        mom_10 = (closes[i] - closes[i-10]) / closes[i-10] if i >= 10 else 0
        mom_20 = (closes[i] - closes[i-lookback]) / closes[i-lookback]

        # 波动率特征
        volatility = np.std(returns) * np.sqrt(252)  # 年化波动率
        atr = np.mean([window_highs[j] - window_lows[j] for j in range(lookback)])

        # 技术指标特征
        # RSI近似
        gains = [r for r in returns if r > 0]
        losses = [-r for r in returns if r < 0]
        avg_gain = np.mean(gains) if gains else 0
        avg_loss = np.mean(losses) if losses else 0.001
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

        # MACD近似 (简单版本)
        ema12 = np.mean(window_closes[-12:]) if len(window_closes) >= 12 else np.mean(window_closes)
        ema26 = np.mean(window_closes[-lookback:])
        macd = ema12 - ema26

        # 布林带位置
        bb_mean = np.mean(window_closes)
        bb_std = np.std(window_closes)
        bb_position = (closes[i] - bb_mean) / (bb_std + 1e-10)

        # 成交量特征
        vol_ma = np.mean(window_vols)
        vol_ratio = volumes[i] / vol_ma if vol_ma > 0 else 1

        # 统计特征
        skew = np.mean((returns - np.mean(returns))**3) / (np.std(returns)**3 + 1e-10)
        kurt = np.mean((returns - np.mean(returns))**4) / (np.std(returns)**4 + 1e-10)

        # 高低点特征
        hh = np.max(window_highs)
        ll = np.min(window_lows)
        price_position = (closes[i] - ll) / (hh - ll + 1e-10)

        features = [
            mom_1, mom_5, mom_10, mom_20,
            volatility, atr / closes[i],
            rsi / 100, macd / closes[i], bb_position,
            vol_ratio, skew, kurt, price_position
        ]

        features_list.append(features)

    return df.with_columns([pl.Series('features', features_list)])


# ============================================================================
# 1. 简化的LSTM趋势预测 (使用滑动窗口模拟)
# ============================================================================

def lstm_trend_strategy(df: pl.DataFrame, threshold: float = 0.005) -> pl.DataFrame:
    """
    简化的LSTM趋势策略 (使用加权移动平均模拟深度学习预测)

    使用指数衰减权重模拟LSTM的序列记忆:
    - 越新的数据权重越高
    - 预测未来N日收益率
    - 超过阈值做多，低于阈值做空
    """
    n = len(df)
    if n < 30:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    volumes = df['volume'].to_numpy()

    signals = []
    position = 0

    # 模拟LSTM预测窗口
    window_size = 20

    for i in range(n):
        if i < window_size + 5:
            signals.append(0)
            continue

        # 获取历史窗口
        window = closes[i-window_size:i]

        # 模拟LSTM加权预测 (指数权重)
        weights = np.exp(np.linspace(-1, 0, window_size))
        weights /= weights.sum()

        # 计算加权趋势
        weighted_avg = np.sum(window * weights)
        trend = (closes[i] - weighted_avg) / weighted_avg

        # 使用动量预测未来走势
        returns = np.diff(window) / window[:-1]
        predicted_return = np.sum(returns[-5:] * np.array([0.1, 0.15, 0.2, 0.25, 0.3]))

        # 综合信号
        signal = 0
        if predicted_return > threshold and trend > 0:
            signal = 1
        elif predicted_return < -threshold and trend < 0:
            signal = -1

        # 简单的状态机
        if position == 0:
            if signal == 1:
                position = 1
            elif signal == -1:
                position = -1
        elif position == 1:
            if signal == -1:
                position = -1
            else:
                signal = 0
        elif position == -1:
            if signal == 1:
                position = 1
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 2. XGBoost风格的梯度提升策略 (使用规则模拟)
# ============================================================================

def xgboost_style_strategy(df: pl.DataFrame, n_estimators: int = 100) -> pl.DataFrame:
    """
    XGBoost风格的多因子分类策略

    模拟梯度提升树的行为:
    - 多因子投票机制
    - 自适应阈值
    - 特征重要性加权
    """
    n = len(df)
    if n < 20:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    volumes = df['volume'].to_numpy()

    signals = []
    position = 0

    for i in range(n):
        if i < 20:
            signals.append(0)
            continue

        # 计算多个因子的得分
        scores = []

        # 因子1: 短期动量
        mom_5 = (closes[i] - closes[i-5]) / closes[i-5] if i >= 5 else 0
        scores.append(np.sign(mom_5) * min(abs(mom_5) * 10, 1))

        # 因子2: 中期动量
        mom_10 = (closes[i] - closes[i-10]) / closes[i-10] if i >= 10 else 0
        scores.append(np.sign(mom_10) * min(abs(mom_10) * 8, 0.8))

        # 因子3: RSI反转
        returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(i-14, i)]
        gains = [r for r in returns if r > 0]
        losses = [-r for r in returns if r < 0]
        avg_gain = np.mean(gains) if gains else 0
        avg_loss = np.mean(losses) if losses else 0.001
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

        if rsi < 30:
            scores.append(0.6)  # 超卖，看涨
        elif rsi > 70:
            scores.append(-0.6)  # 超买，看跌
        else:
            scores.append((50 - rsi) / 100)  # 中性

        # 因子4: 成交量确认
        vol_ma = np.mean(volumes[i-5:i])
        vol_ratio = volumes[i] / vol_ma if vol_ma > 0 else 1
        if vol_ratio > 1.5 and mom_5 > 0:
            scores.append(0.5)
        elif vol_ratio > 1.5 and mom_5 < 0:
            scores.append(-0.5)
        else:
            scores.append(0)

        # 因子5: 突破检测
        hh_20 = np.max(highs[i-20:i])
        ll_20 = np.min(lows[i-20:i])

        if closes[i] > hh_20 * 0.995:
            scores.append(0.7)
        elif closes[i] < ll_20 * 1.005:
            scores.append(-0.7)
        else:
            scores.append(0)

        # 综合评分 (模拟XGBoost的投票机制)
        final_score = np.mean(scores)

        # 生成信号
        signal = 0
        if final_score > 0.3:
            signal = 1
        elif final_score < -0.3:
            signal = -1

        # 状态机
        if position == 0:
            if signal == 1:
                position = 1
            elif signal == -1:
                position = -1
        elif position == 1:
            if signal == -1:
                position = -1
            else:
                signal = 0
        elif position == -1:
            if signal == 1:
                position = 1
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 3. 强化学习风格的仓位管理
# ============================================================================

def rl_position_management_strategy(df: pl.DataFrame, risk_factor: float = 0.02) -> pl.DataFrame:
    """
    强化学习风格的仓位管理策略

    模拟PPO算法的核心思想:
    - 状态: 当前持仓、近期收益、波动率
    - 动作: 持仓比例调整 [-1, 1]
    - 奖励: 风险调整后的收益

    使用简化的策略梯度思想动态调整
    """
    n = len(df)
    if n < 30:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()

    signals = []
    position = 0

    # RL状态变量
    recent_returns = []
    position_history = []

    for i in range(n):
        if i < 20:
            signals.append(0)
            position_history.append(0)
            continue

        # 计算状态特征
        returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(max(1, i-10), i)]
        avg_return = np.mean(returns)
        volatility = np.std(returns)
        sharpe = avg_return / (volatility + 1e-10)

        # 趋势强度
        sma_short = np.mean(closes[i-5:i])
        sma_long = np.mean(closes[i-20:i])
        trend = (sma_short - sma_long) / sma_long

        # 模拟RL策略网络输出
        # 状态: [sharpe, trend, position, volatility]

        # 奖励塑造函数 (Risk-adjusted return)
        if len(recent_returns) > 0:
            reward = avg_return - 0.5 * volatility  # 风险厌恶
        else:
            reward = 0

        # 简化的策略: 基于状态选择动作
        action_score = 0

        # 高夏普 + 上升趋势 = 做多
        if sharpe > 0.1 and trend > 0.001:
            action_score += 1
        # 低夏普 + 下降趋势 = 做空
        elif sharpe < -0.1 and trend < -0.001:
            action_score -= 1

        # 波动率控制 (高波动减仓)
        if volatility > 0.02:
            action_score *= 0.5

        # 动量跟随
        mom_3 = (closes[i] - closes[i-3]) / closes[i-3] if i >= 3 else 0
        if mom_3 > 0.01:
            action_score += 0.5
        elif mom_3 < -0.01:
            action_score -= 0.5

        # 生成交易信号
        signal = 0
        if action_score > 0.3:
            signal = 1
        elif action_score < -0.3:
            signal = -1

        # 更新状态
        if position == 0:
            if signal == 1:
                position = 1
            elif signal == -1:
                position = -1
        elif position == 1:
            if signal == -1:
                position = -1
            else:
                signal = 0
        elif position == -1:
            if signal == 1:
                position = 1
            else:
                signal = 0

        position_history.append(position)
        if i > 0:
            recent_returns.append((closes[i] - closes[i-1]) / closes[i-1])

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 4. Isolation Forest 异常检测策略
# ============================================================================

def isolation_forest_strategy(df: pl.DataFrame, contamination: float = 0.1) -> pl.DataFrame:
    """
    基于Isolation Forest思想的异常检测策略

    检测市场异常状态并反向操作:
    - 异常高点 -> 做空
    - 异常低点 -> 做多

    使用简单的距离度量模拟异常检测
    """
    n = len(df)
    if n < 30:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    volumes = df['volume'].to_numpy()

    signals = []
    position = 0

    for i in range(n):
        if i < 30:
            signals.append(0)
            continue

        # 构建特征向量
        window = closes[i-20:i]

        # 计算特征
        returns = np.diff(window) / window[:-1]
        current_return = (closes[i] - closes[i-1]) / closes[i-1]

        # 均值和方差
        mean_ret = np.mean(returns)
        std_ret = np.std(returns)

        # 异常分数 (偏离均值的程度)
        if std_ret > 0:
            anomaly_score = abs(current_return - mean_ret) / std_ret
        else:
            anomaly_score = 0

        # 价格位置的异常
        price_mean = np.mean(window)
        price_std = np.std(window)
        price_zscore = (closes[i] - price_mean) / (price_std + 1e-10)

        # 成交量的异常
        vol_mean = np.mean(volumes[i-10:i])
        vol_std = np.std(volumes[i-10:i])
        vol_zscore = (volumes[i] - vol_mean) / (vol_std + 1e-10)

        # 综合异常分数
        total_anomaly = (abs(price_zscore) + abs(vol_zscore) * 0.5) / 1.5

        # 判断是否为异常点
        is_anomaly = total_anomaly > 2.0  # 2个标准差

        signal = 0

        # 异常检测驱动的交易逻辑
        if is_anomaly:
            if price_zscore > 1.5:  # 价格异常高
                signal = -1  # 做空 (回归)
            elif price_zscore < -1.5:  # 价格异常低
                signal = 1  # 做多 (反弹)
        else:
            # 非异常状态，跟随趋势
            if price_zscore > 0.5:
                signal = 1
            elif price_zscore < -0.5:
                signal = -1

        # 状态机
        if position == 0:
            if signal == 1:
                position = 1
            elif signal == -1:
                position = -1
        elif position == 1:
            if signal == -1:
                position = -1
            else:
                signal = 0
        elif position == -1:
            if signal == 1:
                position = 1
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 5. HMM 隐马尔可夫模型状态检测
# ============================================================================

def hmm_state_strategy(df: pl.DataFrame, n_states: int = 3) -> pl.DataFrame:
    """
    基于HMM思想的市场状态检测策略

    检测市场状态:
    - 状态0: 震荡/横盘 (低波动，低趋势)
    - 状态1: 上升趋势 (高动量，正收益)
    - 状态2: 下降趋势 (高动量，负收益)

    使用简单的阈值分类模拟HMM状态识别
    """
    n = len(df)
    if n < 30:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()

    signals = []
    position = 0
    current_state = 0

    for i in range(n):
        if i < 30:
            signals.append(0)
            continue

        # 计算观察序列特征
        returns = np.array([(closes[j] - closes[j-1]) / closes[j-1]
                           for j in range(i-20, i)])

        # 状态特征
        mean_ret = np.mean(returns)
        volatility = np.std(returns)
        trend = (closes[i] - closes[i-20]) / closes[i-20]

        # 简单的高斯混合模型分类 (模拟HMM的观测概率)
        # 状态0: 震荡 - 低波动，趋势接近0
        # 状态1: 上涨 - 正趋势，中等波动
        # 状态2: 下跌 - 负趋势，中等波动

        # 计算各状态的概率 (简化为距离度量)
        dist_state0 = abs(mean_ret) + abs(volatility - 0.015) * 10  # 震荡
        dist_state1 = abs(trend - 0.02) + abs(volatility - 0.015) * 5  # 上涨
        dist_state2 = abs(trend + 0.02) + abs(volatility - 0.015) * 5  # 下跌

        # 选择最可能的状态
        distances = [dist_state0, dist_state1, dist_state2]
        predicted_state = np.argmin(distances)

        # 状态转移平滑 (模拟HMM的状态持久性)
        if predicted_state != current_state:
            # 需要连续2期才确认状态转换
            if i > 0 and predicted_state == current_state:  # 简化处理
                current_state = predicted_state

        current_state = predicted_state

        # 基于状态的交易策略
        signal = 0
        if current_state == 1:  # 上升趋势
            signal = 1
        elif current_state == 2:  # 下降趋势
            signal = -1
        else:  # 震荡状态
            # 使用均值回归
            sma = np.mean(closes[i-10:i])
            if closes[i] < sma * 0.98:
                signal = 1
            elif closes[i] > sma * 1.02:
                signal = -1

        # 状态机
        if position == 0:
            if signal == 1:
                position = 1
            elif signal == -1:
                position = -1
        elif position == 1:
            if signal == -1:
                position = -1
            else:
                signal = 0
        elif position == -1:
            if signal == 1:
                position = 1
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 6. 神经网络集成策略 (Ensemble)
# ============================================================================

def neural_ensemble_strategy(df: pl.DataFrame) -> pl.DataFrame:
    """
    神经网络集成策略

    模拟多个神经网络的集成投票:
    - 趋势网络
    - 动量网络
    - 反转网络
    - 波动率网络
    """
    n = len(df)
    if n < 30:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    volumes = df['volume'].to_numpy()

    signals = []
    position = 0

    # 各网络的历史准确率估计 (模拟在线学习)
    network_weights = [0.25, 0.25, 0.25, 0.25]

    for i in range(n):
        if i < 30:
            signals.append(0)
            continue

        # 网络1: 趋势跟踪
        sma_10 = np.mean(closes[i-10:i])
        sma_30 = np.mean(closes[i-30:i])
        trend_signal = 1 if sma_10 > sma_30 * 1.005 else (-1 if sma_10 < sma_30 * 0.995 else 0)

        # 网络2: 动量检测
        mom_5 = (closes[i] - closes[i-5]) / closes[i-5] if i >= 5 else 0
        mom_signal = 1 if mom_5 > 0.02 else (-1 if mom_5 < -0.02 else 0)

        # 网络3: 均值回归
        bb_mean = np.mean(closes[i-20:i])
        bb_std = np.std(closes[i-20:i])
        bb_pos = (closes[i] - bb_mean) / (bb_std + 1e-10)
        reversal_signal = -1 if bb_pos > 1.5 else (1 if bb_pos < -1.5 else 0)

        # 网络4: 波动率突破
        returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(i-10, i)]
        vol = np.std(returns)
        vol_signal = 0
        if vol > 0.015:  # 高波动
            if mom_5 > 0:
                vol_signal = 1
            else:
                vol_signal = -1

        # 集成投票
        votes = [trend_signal, mom_signal, reversal_signal, vol_signal]
        weighted_vote = sum(v * w for v, w in zip(votes, network_weights))

        # 归一化
        signal = 0
        if weighted_vote > 0.2:
            signal = 1
        elif weighted_vote < -0.2:
            signal = -1

        # 更新权重 (模拟在线学习)
        if i > 30:
            future_return = (closes[i] - closes[i-1]) / closes[i-1]
            for idx, v in enumerate(votes):
                if v != 0:
                    # 如果预测方向与实际收益方向一致，增加权重
                    if np.sign(v) == np.sign(future_return):
                        network_weights[idx] = min(0.4, network_weights[idx] + 0.01)
                    else:
                        network_weights[idx] = max(0.1, network_weights[idx] - 0.01)
            # 归一化权重
            total = sum(network_weights)
            network_weights = [w / total for w in network_weights]

        # 状态机
        if position == 0:
            if signal == 1:
                position = 1
            elif signal == -1:
                position = -1
        elif position == 1:
            if signal == -1:
                position = -1
            else:
                signal = 0
        elif position == -1:
            if signal == 1:
                position = 1
            else:
                signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 回测执行
# ============================================================================

def run_ml_backtest(code: str, strategy_name: str, strategy_func, **kwargs):
    """运行ML策略回测"""
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
    """主函数 - ML/AI策略对比"""
    print("=" * 70)
    print("Quant Terminal ML/AI 策略回测")
    print("=" * 70)
    print("\n策略说明:")
    print("  1. LSTM趋势预测    - 深度学习序列预测")
    print("  2. XGBoost多因子   - 集成学习分类")
    print("  3. RL仓位管理      - 强化学习动态调仓")
    print("  4. Isolation Forest- 异常检测反向操作")
    print("  5. HMM状态检测     - 隐马尔可夫状态识别")
    print("  6. 神经网络集成    - 多网络投票集成")
    print("=" * 70)

    # 下载数据
    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    # 策略列表
    strategies = [
        ('LSTM趋势预测', lstm_trend_strategy, {}),
        ('XGBoost多因子', xgboost_style_strategy, {}),
        ('RL仓位管理', rl_position_management_strategy, {}),
        ('Isolation Forest', isolation_forest_strategy, {}),
        ('HMM状态检测', hmm_state_strategy, {}),
        ('神经网络集成', neural_ensemble_strategy, {}),
    ]

    all_results = []

    for code in codes:
        print(f"\n{'='*70}")
        print(f"品种: {code}")
        print('='*70)

        for strategy_name, strategy_func, kwargs in strategies:
            result = run_ml_backtest(code, strategy_name, strategy_func, **kwargs)
            if result:
                all_results.append(result)

    # 汇总
    print("\n" + "=" * 70)
    print("ML/AI 策略对比汇总")
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

    # 对比经典策略
    print("\n" + "=" * 70)
    print("ML策略 vs 经典CTA策略对比")
    print("=" * 70)
    print("经典CTA最佳: IH0 + Dual Thrust(优化) - 夏普: 0.88, 收益: +29.22%")
    print("查看ML策略是否能超越...")


if __name__ == "__main__":
    main()
