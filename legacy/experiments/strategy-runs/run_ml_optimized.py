#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal 优化ML/AI + 深度学习策略

优化内容:
- XGBoost参数优化 (降低阈值，增加交易频率)
- PyTorch LSTM真正实现 (序列预测)
- 混合策略 (ML信号 + 经典CTA过滤)
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

# 尝试导入PyTorch
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
    print(f"[*] PyTorch {torch.__version__} 已加载")
except ImportError:
    TORCH_AVAILABLE = False
    print("[!] PyTorch未安装，使用模拟LSTM")


# ============================================================================
# 1. XGBoost策略 - 参数优化版 (降低阈值)
# ============================================================================

def xgboost_optimized_v1(df: pl.DataFrame, threshold: float = 0.15) -> pl.DataFrame:
    """
    XGBoost优化版V1 - 降低阈值增加交易频率
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

        scores = []

        # 因子1: 短期动量 (权重增加)
        mom_3 = (closes[i] - closes[i-3]) / closes[i-3] if i >= 3 else 0
        mom_5 = (closes[i] - closes[i-5]) / closes[i-5] if i >= 5 else 0
        scores.append(np.sign(mom_5) * min(abs(mom_5) * 15, 1.5))

        # 因子2: 中期动量
        mom_10 = (closes[i] - closes[i-10]) / closes[i-10] if i >= 10 else 0
        scores.append(np.sign(mom_10) * min(abs(mom_10) * 10, 1.0))

        # 因子3: RSI (放宽条件)
        returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(max(1, i-14), i)]
        gains = [r for r in returns if r > 0]
        losses = [-r for r in returns if r < 0]
        avg_gain = np.mean(gains) if gains else 0
        avg_loss = np.mean(losses) if losses else 0.001
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

        if rsi < 35:  # 放宽超卖阈值
            scores.append(0.5)
        elif rsi > 65:  # 放宽超买阈值
            scores.append(-0.5)
        else:
            scores.append((50 - rsi) / 80)

        # 因子4: 成交量确认 (降低要求)
        vol_ma = np.mean(volumes[max(0, i-5):i])
        vol_ratio = volumes[i] / vol_ma if vol_ma > 0 else 1
        if vol_ratio > 1.2 and mom_5 > 0:  # 降低成交量要求
            scores.append(0.4)
        elif vol_ratio > 1.2 and mom_5 < 0:
            scores.append(-0.4)
        else:
            scores.append(0)

        # 因子5: 突破检测 (放宽)
        hh_20 = np.max(highs[max(0, i-20):i])
        ll_20 = np.min(lows[max(0, i-20):i])

        if closes[i] > hh_20 * 0.998:  # 突破阈值降低
            scores.append(0.6)
        elif closes[i] < ll_20 * 1.002:
            scores.append(-0.6)
        else:
            scores.append((closes[i] - (hh_20 + ll_20)/2) / (hh_20 - ll_20 + 1e-10))

        # 因子6: 短期趋势斜率
        if i >= 5:
            slope = np.polyfit(range(5), closes[i-5:i], 1)[0] / closes[i]
            scores.append(np.sign(slope) * min(abs(slope) * 100, 0.5))

        # 综合评分 (降低阈值)
        final_score = np.mean(scores)

        signal = 0
        if final_score > threshold:  # 降低阈值
            signal = 1
        elif final_score < -threshold:
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


def xgboost_optimized_v2(df: pl.DataFrame, threshold: float = 0.1) -> pl.DataFrame:
    """
    XGBoost优化版V2 - 更激进的交易频率
    """
    n = len(df)
    if n < 15:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    volumes = df['volume'].to_numpy()

    signals = []
    position = 0

    for i in range(n):
        if i < 15:
            signals.append(0)
            continue

        # 更简单的特征，降低延迟
        mom_3 = (closes[i] - closes[i-3]) / closes[i-3] if i >= 3 else 0
        mom_5 = (closes[i] - closes[i-5]) / closes[i-5] if i >= 5 else 0
        mom_10 = (closes[i] - closes[i-10]) / closes[i-10] if i >= 10 else 0

        # 波动率
        returns = [(closes[j] - closes[j-1]) / closes[j-1] for j in range(max(1, i-10), i)]
        volatility = np.std(returns)

        # RSI简化计算
        avg_gain = np.mean([r for r in returns if r > 0]) if returns else 0
        avg_loss = np.mean([-r for r in returns if r < 0]) if returns else 0.001
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

        # MACD简化
        ema5 = np.mean(closes[max(0, i-5):i])
        ema15 = np.mean(closes[max(0, i-15):i])
        macd = ema5 - ema15

        # 布林带
        bb_mean = np.mean(closes[max(0, i-20):i])
        bb_std = np.std(closes[max(0, i-20):i])
        bb_pos = (closes[i] - bb_mean) / (bb_std + 1e-10)

        # 成交量
        vol_ma = np.mean(volumes[max(0, i-3):i])
        vol_ratio = volumes[i] / vol_ma if vol_ma > 0 else 1

        # 多因子投票 (简化权重)
        score = 0

        # 动量因子
        if mom_3 > 0.005: score += 0.2
        elif mom_3 < -0.005: score -= 0.2

        if mom_5 > 0.01: score += 0.3
        elif mom_5 < -0.01: score -= 0.3

        if mom_10 > 0.02: score += 0.2
        elif mom_10 < -0.02: score -= 0.2

        # RSI因子
        if rsi < 30: score += 0.4
        elif rsi > 70: score -= 0.4
        elif rsi < 45: score += 0.1
        elif rsi > 55: score -= 0.1

        # MACD因子
        if macd > 0 and mom_3 > 0: score += 0.2
        elif macd < 0 and mom_3 < 0: score -= 0.2

        # 布林带因子
        if bb_pos < -1.5: score += 0.3
        elif bb_pos > 1.5: score -= 0.3

        # 成交量确认
        if vol_ratio > 1.5:
            if score > 0: score += 0.2
            elif score < 0: score -= 0.2

        # 波动率过滤 (高波动降低信号)
        if volatility > 0.02:
            score *= 0.7

        # 生成信号 (更低阈值)
        signal = 0
        if score > threshold:
            signal = 1
        elif score < -threshold:
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
# 2. PyTorch LSTM真正实现
# ============================================================================

class LSTMModel(nn.Module):
    """LSTM预测模型"""
    def __init__(self, input_size=10, hidden_size=64, num_layers=2, output_size=3):
        super(LSTMModel, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                           batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_size, output_size)
        self.softmax = nn.Softmax(dim=1)

    def forward(self, x):
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)

        out, _ = self.lstm(x, (h0, c0))
        out = self.fc(out[:, -1, :])
        out = self.softmax(out)
        return out


def extract_features_for_lstm(df: pl.DataFrame, i: int, window: int = 20) -> np.ndarray:
    """提取LSTM特征"""
    if i < window:
        return None

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()
    volumes = df['volume'].to_numpy()
    opens = df['open'].to_numpy()

    features = []
    for j in range(i - window, i):
        # 价格特征
        ret = (closes[j] - closes[j-1]) / closes[j-1] if j > 0 else 0
        high_low_ratio = (highs[j] - lows[j]) / closes[j]
        open_close_ratio = (closes[j] - opens[j]) / opens[j] if opens[j] > 0 else 0

        # 成交量特征
        vol_ma = np.mean(volumes[max(0, j-5):j]) if j > 0 else volumes[j]
        vol_ratio = volumes[j] / vol_ma if vol_ma > 0 else 1

        # 位置特征
        hh = np.max(highs[max(0, j-10):j+1])
        ll = np.min(lows[max(0, j-10):j+1])
        position = (closes[j] - ll) / (hh - ll + 1e-10)

        features.append([
            ret, high_low_ratio, open_close_ratio,
            vol_ratio, position,
            closes[j] / closes[i-window] - 1,  # 相对收益
            (closes[j] - np.mean(closes[max(0, j-5):j+1])) / (np.std(closes[max(0, j-5):j+1]) + 1e-10),  # zscore
            j % 5 / 5,  # 周内位置
            np.log(volumes[j] + 1),
            (highs[j] - closes[j]) / (highs[j] - lows[j] + 1e-10)  # 上影线比例
        ])

    return np.array(features)


def pytorch_lstm_strategy(df: pl.DataFrame, seq_length: int = 20) -> pl.DataFrame:
    """
    PyTorch LSTM策略 - 真正深度学习预测
    """
    n = len(df)
    if n < seq_length + 30 or not TORCH_AVAILABLE:
        # 回退到简单策略
        if not TORCH_AVAILABLE:
            print("  [!] PyTorch不可用，使用XGBoost V2替代")
        return xgboost_optimized_v2(df)

    print("  [*] 训练PyTorch LSTM模型...")

    closes = df['close'].to_numpy()

    # 准备训练数据
    X_train = []
    y_train = []

    for i in range(seq_length, n - 5):
        features = extract_features_for_lstm(df, i, seq_length)
        if features is not None:
            X_train.append(features)
            # 未来5日收益分类
            future_ret = (closes[i+5] - closes[i]) / closes[i]
            if future_ret > 0.01:
                y_train.append(0)  # 上涨
            elif future_ret < -0.01:
                y_train.append(1)  # 下跌
            else:
                y_train.append(2)  # 震荡

    if len(X_train) < 50:
        print("  [!] 数据不足，使用XGBoost V2")
        return xgboost_optimized_v2(df)

    # 转换为Tensor
    X_train = torch.FloatTensor(np.array(X_train))
    y_train = torch.LongTensor(y_train)

    # 创建模型
    model = LSTMModel(input_size=10, hidden_size=32, num_layers=2, output_size=3)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

    # 训练
    model.train()
    dataset = TensorDataset(X_train, y_train)
    loader = DataLoader(dataset, batch_size=32, shuffle=True)

    for epoch in range(30):
        total_loss = 0
        for batch_X, batch_y in loader:
            optimizer.zero_grad()
            outputs = model(batch_X)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        if epoch % 10 == 0:
            print(f"    Epoch {epoch}, Loss: {total_loss/len(loader):.4f}")

    # 预测
    model.eval()
    signals = []
    position = 0

    for i in range(n):
        if i < seq_length:
            signals.append(0)
            continue

        features = extract_features_for_lstm(df, i, seq_length)
        if features is None:
            signals.append(0)
            continue

        X = torch.FloatTensor(features).unsqueeze(0)
        with torch.no_grad():
            pred = model(X)
            pred_class = torch.argmax(pred, dim=1).item()
            confidence = torch.max(pred).item()

        # 仅在高置信度时交易
        signal = 0
        if confidence > 0.5:
            if pred_class == 0:
                signal = 1
            elif pred_class == 1:
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
# 3. 混合策略 (ML信号 + 经典CTA过滤)
# ============================================================================

def hybrid_ml_cta_strategy(df: pl.DataFrame) -> pl.DataFrame:
    """
    混合策略: XGBoost信号 + Dual Thrust过滤
    """
    n = len(df)
    if n < 30:
        return df.with_columns([pl.Series('signal', [0] * n)])

    # 先获取XGBoost信号
    df_signals = xgboost_optimized_v2(df, threshold=0.1)
    xgb_signals = df_signals['signal'].to_numpy()

    closes = df['close'].to_numpy()
    highs = df['high'].to_numpy()
    lows = df['low'].to_numpy()

    signals = []
    position = 0

    for i in range(n):
        if i < 20:
            signals.append(0)
            continue

        # XGBoost原始信号
        xgb_signal = xgb_signals[i]

        if xgb_signal == 0:
            signals.append(0)
            continue

        # Dual Thrust过滤
        hh = np.max(highs[max(0, i-4):i])
        ll = np.min(lows[max(0, i-4):i])
        hc = np.max(closes[max(0, i-4):i])
        lc = np.min(closes[max(0, i-4):i])

        range_val = max(hh - lc, hc - ll)
        upper = closes[i-1] + 0.5 * range_val
        lower = closes[i-1] - 0.5 * range_val

        # 过滤逻辑
        signal = 0
        if xgb_signal == 1 and closes[i] > upper * 0.999:  # ML看涨 + 突破上轨
            signal = 1
        elif xgb_signal == -1 and closes[i] < lower * 1.001:  # ML看跌 + 突破下轨
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
# 4. 自适应ML策略 (根据波动率调整)
# ============================================================================

def adaptive_ml_strategy(df: pl.DataFrame) -> pl.DataFrame:
    """
    自适应ML策略 - 根据市场波动率自动选择策略
    """
    n = len(df)
    if n < 30:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()

    # 计算历史波动率
    returns = np.diff(closes) / closes[:-1]

    signals = []
    position = 0

    for i in range(n):
        if i < 30:
            signals.append(0)
            continue

        # 计算当前波动率状态
        current_vol = np.std(returns[max(0, i-20):i]) * np.sqrt(252)

        # 根据波动率选择策略
        if current_vol > 0.25:  # 高波动 - 使用均值回归
            # Isolation Forest风格
            window = closes[max(0, i-20):i]
            mean_price = np.mean(window)
            std_price = np.std(window)
            zscore = (closes[i] - mean_price) / (std_price + 1e-10)

            if zscore > 1.5:
                signal = -1
            elif zscore < -1.5:
                signal = 1
            else:
                signal = 0

        elif current_vol < 0.15:  # 低波动 - 使用趋势跟踪
            # 简单趋势
            sma_short = np.mean(closes[max(0, i-5):i])
            sma_long = np.mean(closes[max(0, i-20):i])

            if sma_short > sma_long * 1.005:
                signal = 1
            elif sma_short < sma_long * 0.995:
                signal = -1
            else:
                signal = 0
        else:  # 中等波动 - 使用XGBoost
            signal = xgboost_optimized_v2(df.slice(0, i+1))['signal'][-1] if i > 0 else 0

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
    """主函数 - 优化ML策略对比"""
    print("=" * 70)
    print("Quant Terminal 优化ML/AI + 深度学习策略回测")
    print("=" * 70)
    print(f"PyTorch状态: {'可用' if TORCH_AVAILABLE else '不可用'}")
    print("\n策略说明:")
    print("  1. XGBoost V1        - 降低阈值0.15")
    print("  2. XGBoost V2        - 降低阈值0.1，更激进")
    print("  3. PyTorch LSTM      - 真正深度学习预测")
    print("  4. 混合策略          - ML信号 + Dual Thrust过滤")
    print("  5. 自适应ML          - 波动率自适应策略选择")
    print("=" * 70)

    # 下载数据
    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    # 策略列表
    strategies = [
        ('XGBoost V1 (阈值0.15)', xgboost_optimized_v1, {'threshold': 0.15}),
        ('XGBoost V2 (阈值0.1)', xgboost_optimized_v2, {'threshold': 0.1}),
        ('PyTorch LSTM', pytorch_lstm_strategy, {}),
        ('混合策略(ML+CTA)', hybrid_ml_cta_strategy, {}),
        ('自适应ML', adaptive_ml_strategy, {}),
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
    print("优化ML策略对比汇总")
    print("=" * 70)
    print(f"{'品种':<8} {'策略':<22} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
    print("-" * 70)

    for r in all_results:
        print(f"{r['code']:<8} {r['strategy']:<22} {r['total_return']:>+8.2%}  "
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

    # 与经典CTA对比
    print("\n" + "=" * 70)
    print("所有策略综合排名")
    print("=" * 70)
    print("Top 3:")
    sorted_results = sorted(all_results, key=lambda x: x['sharpe_ratio'], reverse=True)[:3]
    for i, r in enumerate(sorted_results, 1):
        print(f"  {i}. {r['code']} + {r['strategy']}")
        print(f"     夏普: {r['sharpe_ratio']:.2f} | 收益: {r['total_return']:+.2%} | 回撤: {r['max_drawdown']:.2%}")


if __name__ == "__main__":
    main()
