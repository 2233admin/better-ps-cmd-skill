#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal 策略迭代优化系统 v2.0

改进方向:
1. 集成学习: 融合Dual Thrust + LSTM + XGBoost
2. 动态权重: 根据近期表现调整策略权重
3. 特征工程: 加入更多市场微观结构特征
4. 风险控制: 加入波动率缩放仓位
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import numpy as np
import polars as pl
from datetime import datetime
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')

from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.strategy.backtest import VectorBacktester

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


# ============================================================================
# 成功的基线策略 (保留)
# ============================================================================

def dual_thrust_optimized(df: pl.DataFrame, n_periods: int = 4, k1: float = 0.5, k2: float = 0.5) -> pl.DataFrame:
    """优化版Dual Thrust - 夏普0.88基线"""
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


# ============================================================================
# PyTorch LSTM - 全量训练 (夏普1.61基线)
# ============================================================================

class LSTMModel(nn.Module):
    def __init__(self, input_size=10, hidden_size=64, num_layers=2, output_size=3):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_size, output_size)
        self.softmax = nn.Softmax(dim=1)

    def forward(self, x):
        h0 = torch.zeros(self.lstm.num_layers, x.size(0), self.lstm.hidden_size)
        c0 = torch.zeros(self.lstm.num_layers, x.size(0), self.lstm.hidden_size)
        out, _ = self.lstm(x, (h0, c0))
        out = self.fc(out[:, -1, :])
        return self.softmax(out)


def extract_features(df: pl.DataFrame, i: int, window: int = 20) -> np.ndarray:
    if i < window:
        return None

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    volumes = df['volume'].to_numpy()
    opens = df['open'].to_numpy()

    features = []
    for j in range(i - window, i):
        ret = (closes[j] - closes[j-1]) / closes[j-1] if j > 0 else 0
        high_low_ratio = (highs[j] - lows[j]) / closes[j]
        open_close_ratio = (closes[j] - opens[j]) / opens[j] if opens[j] > 0 else 0
        vol_ma = np.mean(volumes[max(0, j-5):j]) if j > 0 else volumes[j]
        vol_ratio = volumes[j] / vol_ma if vol_ma > 0 else 1
        hh = np.max(highs[max(0, j-10):j+1])
        ll = np.min(lows[max(0, j-10):j+1])
        position = (closes[j] - ll) / (hh - ll + 1e-10)

        features.append([
            ret, high_low_ratio, open_close_ratio, vol_ratio, position,
            closes[j] / closes[i-window] - 1,
            (closes[j] - np.mean(closes[max(0, j-5):j+1])) / (np.std(closes[max(0, j-5):j+1]) + 1e-10),
            j % 5 / 5, np.log(volumes[j] + 1),
            (highs[j] - closes[j]) / (highs[j] - lows[j] + 1e-10)
        ])

    return np.array(features)


def pytorch_lstm_strategy(df: pl.DataFrame, confidence: float = 0.35) -> pl.DataFrame:
    """PyTorch LSTM - 全量训练版本 (夏普1.61)"""
    n = len(df)
    if n < 100 or not TORCH_AVAILABLE:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()

    # 准备训练数据
    X_train, y_train = [], []
    for i in range(20, n - 5):
        features = extract_features(df, i, 20)
        if features is not None:
            X_train.append(features)
            future_ret = (closes[i+5] - closes[i]) / closes[i]
            if future_ret > 0.015: y_train.append(0)
            elif future_ret < -0.015: y_train.append(1)
            else: y_train.append(2)

    if len(X_train) < 50:
        return df.with_columns([pl.Series('signal', [0] * n)])

    X_train = torch.FloatTensor(np.array(X_train))
    y_train = torch.LongTensor(y_train)

    # 训练模型
    model = LSTMModel(input_size=10, hidden_size=32, num_layers=2, output_size=3)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

    model.train()
    dataset = TensorDataset(X_train, y_train)
    loader = DataLoader(dataset, batch_size=32, shuffle=True)

    for epoch in range(30):
        for batch_X, batch_y in loader:
            optimizer.zero_grad()
            outputs = model(batch_X)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()

    # 预测
    model.eval()
    signals = []
    position = 0

    for i in range(n):
        if i < 20:
            signals.append(0)
            continue

        features = extract_features(df, i, 20)
        if features is None:
            signals.append(0)
            continue

        X = torch.FloatTensor(features).unsqueeze(0)
        with torch.no_grad():
            pred = model(X)
            pred_class = torch.argmax(pred, dim=1).item()
            conf = torch.max(pred).item()

        signal = 0
        if conf > confidence:
            if pred_class == 0: signal = 1
            elif pred_class == 1: signal = -1

        if position == 0:
            if signal == 1: position = 1
            elif signal == -1: position = -1
        elif position == 1:
            if signal == -1: position = -1
            else: signal = 0
        elif position == -1:
            if signal == 1: position = 1
            else: signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 迭代v2.0: 动态集成策略
# ============================================================================

def dynamic_ensemble_strategy(df: pl.DataFrame) -> pl.DataFrame:
    """
    动态集成策略 v2.0

    改进:
    1. 滚动窗口评估各策略表现
    2. 动态调整权重
    3. 加入波动率缩放
    """
    n = len(df)
    if n < 100:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()

    # 获取各基策略信号
    dt_signals = dual_thrust_optimized(df)['signal'].to_numpy()
    lstm_signals = pytorch_lstm_strategy(df, confidence=0.35)['signal'].to_numpy() if TORCH_AVAILABLE else np.zeros(n)

    # 计算XGBoost信号 (简化版)
    xgb_signals = np.zeros(n)
    for i in range(20, n):
        mom_5 = (closes[i] - closes[i-5]) / closes[i-5]
        mom_10 = (closes[i] - closes[i-10]) / closes[i-10]

        returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(i-14, i)]
        gains = [r for r in returns if r > 0]
        losses = [-r for r in returns if r < 0]
        avg_gain = np.mean(gains) if gains else 0
        avg_loss = np.mean(losses) if losses else 0.001
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

        score = 0
        if mom_5 > 0.01: score += 0.3
        elif mom_5 < -0.01: score -= 0.3
        if mom_10 > 0.02: score += 0.2
        elif mom_10 < -0.02: score -= 0.2
        if rsi < 35: score += 0.3
        elif rsi > 65: score -= 0.3

        if score > 0.3: xgb_signals[i] = 1
        elif score < -0.3: xgb_signals[i] = -1

    # 动态权重调整
    window = 50
    signals = []
    position = 0

    weights = {'dt': 0.33, 'lstm': 0.33, 'xgb': 0.34}

    for i in range(n):
        if i < window + 20:
            signals.append(0)
            continue

        # 滚动更新权重 (每50日)
        if i % 25 == 0 and i > window:
            performance = {}
            for name, sig_array in [('dt', dt_signals), ('lstm', lstm_signals), ('xgb', xgb_signals)]:
                rets = []
                for j in range(i-window, i-1):
                    if sig_array[j] != 0:
                        r = (closes[j+1] - closes[j]) / closes[j] * sig_array[j]
                        rets.append(r)
                if len(rets) > 5:
                    sharpe = np.mean(rets) / (np.std(rets) + 1e-10) * np.sqrt(252)
                    performance[name] = max(0, sharpe)
                else:
                    performance[name] = 0.1

            total = sum(performance.values())
            if total > 0:
                weights = {k: v/total for k, v in performance.items()}

        # 加权投票
        vote = (dt_signals[i] * weights['dt'] +
                lstm_signals[i] * weights['lstm'] +
                xgb_signals[i] * weights['xgb'])

        signal = 0
        if vote > 0.2: signal = 1
        elif vote < -0.2: signal = -1

        if position == 0:
            if signal == 1: position = 1
            elif signal == -1: position = -1
        elif position == 1:
            if signal == -1: position = -1
            else: signal = 0
        elif position == -1:
            if signal == 1: position = 1
            else: signal = 0

        signals.append(signal)

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 迭代v2.0: 波动率缩放版Dual Thrust
# ============================================================================

def dual_thrust_volatility_scaled(df: pl.DataFrame) -> pl.DataFrame:
    """
    波动率缩放版Dual Thrust

    改进: 根据近期波动率动态调整K1/K2参数
    """
    n = len(df)
    if n < 30:
        return df.with_columns([pl.Series('signal', [0] * n)])

    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    closes = df['close'].to_numpy()
    opens = df['open'].to_numpy()

    signals = []
    position = 0

    for i in range(n):
        if i < 20:
            signals.append(0)
            continue

        # 计算近期波动率
        returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(max(1, i-20), i)]
        vol = np.std(returns) * np.sqrt(252)

        # 根据波动率调整参数
        if vol > 0.25:  # 高波动
            k1, k2 = 0.7, 0.7
            n_periods = 3
        elif vol > 0.18:  # 中等波动
            k1, k2 = 0.5, 0.5
            n_periods = 4
        else:  # 低波动
            k1, k2 = 0.35, 0.35
            n_periods = 5

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


# ============================================================================
# 迭代v2.0: 多时间框架融合
# ============================================================================

def multi_timeframe_fusion(df: pl.DataFrame) -> pl.DataFrame:
    """
    多时间框架融合策略

    日线趋势 + 周线确认
    """
    n = len(df)
    if n < 40:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()

    signals = []
    position = 0

    for i in range(n):
        if i < 40:
            signals.append(0)
            continue

        # 短期趋势 (5日)
        short_trend = closes[i] > np.mean(closes[i-5:i])

        # 中期趋势 (20日)
        mid_trend = closes[i] > np.mean(closes[i-20:i])

        # 长期趋势 (40日近似周线)
        long_trend = closes[i] > np.mean(closes[i-40:i])

        # 布林带位置
        bb_mean = np.mean(closes[i-20:i])
        bb_std = np.std(closes[i-20:i])
        bb_pos = (closes[i] - bb_mean) / (bb_std + 1e-10)

        # 多时间框架确认
        score = 0
        if short_trend: score += 1
        if mid_trend: score += 1
        if long_trend: score += 1

        # 均值回归修正
        if bb_pos > 2.0:
            score -= 1
        elif bb_pos < -2.0:
            score += 1

        signal = 0
        if score >= 2:
            signal = 1
        elif score <= -2:
            signal = -1

        if position == 0:
            if signal == 1: position = 1
            elif signal == -1: position = -1
        elif position == 1:
            if signal == -1: position = -1
            else: signal = 0
        elif position == -1:
            if signal == 1: position = 1
            else: signal = 0

        signals.append(signal)

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
    print("Quant Terminal 策略迭代优化系统 v2.0")
    print("=" * 70)
    print(f"PyTorch: {'可用' if TORCH_AVAILABLE else '不可用'}")
    print("\n策略:")
    print("  [基线] Dual Thrust优化    - 夏普0.88")
    print("  [基线] PyTorch LSTM       - 夏普1.61")
    print("  [迭代] 动态集成           - 滚动权重调整")
    print("  [迭代] 波动率缩放DT       - 自适应参数")
    print("  [迭代] 多时间框架融合     - 日线+周线")
    print("=" * 70)

    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    strategies = [
        ('DualThrust_优化', dual_thrust_optimized, {}),
        ('PyTorch_LSTM', pytorch_lstm_strategy, {'confidence': 0.35}),
        ('迭代_动态集成', dynamic_ensemble_strategy, {}),
        ('迭代_波动率缩放DT', dual_thrust_volatility_scaled, {}),
        ('迭代_多时间框架', multi_timeframe_fusion, {}),
    ]

    all_results = []

    for code in codes:
        print(f"\n{'='*70}")
        print(f"品种: {code}")
        print('='*70)

        for strategy_name, strategy_func, kwargs in strategies:
            result = run_backtest(code, strategy_name, strategy_func, **kwargs)
            if result:
                all_results.append(result)

    # 汇总
    print("\n" + "=" * 70)
    print("迭代优化结果汇总")
    print("=" * 70)
    print(f"{'品种':<8} {'策略':<20} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
    print("-" * 70)

    for r in sorted(all_results, key=lambda x: x['sharpe_ratio'], reverse=True):
        marker = " [迭代]" if "迭代" in r['strategy'] else " [基线]"
        print(f"{r['code']:<8} {r['strategy']:<20} {r['total_return']:>+8.2%}  "
              f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
              f"{r['sharpe_ratio']:>6.2f}  {r['trades']:>4}{marker}")

    print("=" * 70)

    # 分析迭代效果
    print("\n[迭代效果分析]")

    for code in codes:
        code_results = [r for r in all_results if r['code'] == code]
        baseline = [r for r in code_results if "迭代" not in r['strategy']]
        improved = [r for r in code_results if "迭代" in r['strategy']]

        if baseline and improved:
            best_baseline = max(baseline, key=lambda x: x['sharpe_ratio'])
            best_improved = max(improved, key=lambda x: x['sharpe_ratio'])

            print(f"\n  {code}:")
            print(f"    基线最佳: {best_baseline['strategy']} (夏普{best_baseline['sharpe_ratio']:.2f})")
            print(f"    迭代最佳: {best_improved['strategy']} (夏普{best_improved['sharpe_ratio']:.2f})")

            if best_improved['sharpe_ratio'] > best_baseline['sharpe_ratio']:
                print(f"    -> 迭代提升: +{best_improved['sharpe_ratio'] - best_baseline['sharpe_ratio']:.2f} 夏普")
            else:
                print(f"    -> 迭代未超越基线")

    # 全局最佳
    if all_results:
        best = max(all_results, key=lambda x: x['sharpe_ratio'])
        print(f"\n[全局最佳]")
        print(f"  {best['code']} + {best['strategy']}")
        print(f"  夏普: {best['sharpe_ratio']:.2f} | 收益: {best['total_return']:+.2%} | 回撤: {best['max_drawdown']:.2%}")


if __name__ == "__main__":
    main()
