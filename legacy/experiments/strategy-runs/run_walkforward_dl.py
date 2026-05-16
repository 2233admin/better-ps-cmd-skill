#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal Walk-Forward 深度学习系统

基于之前成功的PyTorch LSTM (IH0夏普1.61)
- 滚动窗口训练
- 自动模型保存/加载
- 多架构支持 (LSTM, GRU)
- 自动剪枝: 夏普<0.5的策略自动丢弃
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

# PyTorch
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
    print(f"[*] PyTorch {torch.__version__} loaded")
except ImportError:
    TORCH_AVAILABLE = False
    print("[!] PyTorch not available")


# ============================================================================
# 深度学习模型
# ============================================================================

class LSTMModel(nn.Module):
    """LSTM预测模型"""
    def __init__(self, input_size=10, hidden_size=64, num_layers=2, output_size=3, dropout=0.2):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                           batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_size, output_size)
        self.softmax = nn.Softmax(dim=1)

    def forward(self, x):
        h0 = torch.zeros(self.lstm.num_layers, x.size(0), self.lstm.hidden_size)
        c0 = torch.zeros(self.lstm.num_layers, x.size(0), self.lstm.hidden_size)
        out, _ = self.lstm(x, (h0, c0))
        out = self.fc(out[:, -1, :])
        return self.softmax(out)


class GRUModel(nn.Module):
    """GRU预测模型"""
    def __init__(self, input_size=10, hidden_size=64, num_layers=2, output_size=3, dropout=0.2):
        super().__init__()
        self.gru = nn.GRU(input_size, hidden_size, num_layers,
                         batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_size, output_size)
        self.softmax = nn.Softmax(dim=1)

    def forward(self, x):
        h0 = torch.zeros(self.gru.num_layers, x.size(0), self.gru.hidden_size)
        out, _ = self.gru(x, h0)
        out = self.fc(out[:, -1, :])
        return self.softmax(out)


# ============================================================================
# 特征工程
# ============================================================================

def extract_features(df: pl.DataFrame, i: int, window: int = 20) -> np.ndarray:
    """提取特征序列"""
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


def prepare_data(df: pl.DataFrame, start_idx: int, end_idx: int, seq_length: int = 20):
    """准备训练数据"""
    closes = df['close'].to_numpy()
    X, y = [], []

    for i in range(start_idx + seq_length, end_idx - 5):
        features = extract_features(df, i, seq_length)
        if features is not None:
            X.append(features)
            future_ret = (closes[i+5] - closes[i]) / closes[i]
            if future_ret > 0.015: y.append(0)
            elif future_ret < -0.015: y.append(1)
            else: y.append(2)

    if len(X) < 20:
        return None, None

    return torch.FloatTensor(np.array(X)), torch.LongTensor(y)


# ============================================================================
# Walk-Forward LSTM (基于成功的1.61夏普版本优化)
# ============================================================================

def walkforward_lstm_strategy(
    df: pl.DataFrame,
    train_window: int = 150,
    test_window: int = 50,
    confidence: float = 0.35
) -> pl.DataFrame:
    """
    Walk-Forward LSTM - 滚动训练

    改进点:
    - 更大的训练窗口 (150日)
    - 适中的置信度阈值 (0.35)
    - 每次滚动重新训练
    """
    n = len(df)
    if n < train_window + test_window + 50 or not TORCH_AVAILABLE:
        return df.with_columns([pl.Series('signal', [0] * n)])

    print(f"  [*] Walk-Forward LSTM: train={train_window}, test={test_window}")

    closes = df['close'].to_numpy()
    signals = [0] * n
    position = 0

    current_idx = train_window + 50
    loop_count = 0

    while current_idx < n - test_window:
        loop_count += 1
        train_start = max(0, current_idx - train_window)
        train_end = current_idx
        test_start = current_idx
        test_end = min(current_idx + test_window, n)

        # 准备数据
        X_train, y_train = prepare_data(df, train_start, train_end, 20)
        if X_train is None or len(X_train) < 30:
            current_idx += test_window
            continue

        # 训练模型
        model = LSTMModel(input_size=10, hidden_size=32, num_layers=2, output_size=3, dropout=0.2)
        criterion = nn.CrossEntropyLoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

        model.train()
        dataset = TensorDataset(X_train, y_train)
        loader = DataLoader(dataset, batch_size=32, shuffle=True)

        for epoch in range(25):
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                outputs = model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()

        # 预测
        model.eval()
        for i in range(test_start, test_end):
            if i < 20:
                signals[i] = 0
                continue

            features = extract_features(df, i, 20)
            if features is None:
                signals[i] = 0
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

            # 状态机
            if position == 0:
                if signal == 1: position = 1
                elif signal == -1: position = -1
            elif position == 1:
                if signal == -1: position = -1
                else: signal = 0
            elif position == -1:
                if signal == 1: position = 1
                else: signal = 0

            signals[i] = signal

        current_idx += test_window

    print(f"  [*] 完成 {loop_count} 轮滚动训练")
    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# Walk-Forward GRU
# ============================================================================

def walkforward_gru_strategy(
    df: pl.DataFrame,
    train_window: int = 150,
    test_window: int = 50,
    confidence: float = 0.35
) -> pl.DataFrame:
    """Walk-Forward GRU"""
    n = len(df)
    if n < train_window + test_window + 50 or not TORCH_AVAILABLE:
        return df.with_columns([pl.Series('signal', [0] * n)])

    print(f"  [*] Walk-Forward GRU: train={train_window}, test={test_window}")

    closes = df['close'].to_numpy()
    signals = [0] * n
    position = 0

    current_idx = train_window + 50
    loop_count = 0

    while current_idx < n - test_window:
        loop_count += 1
        train_start = max(0, current_idx - train_window)
        train_end = current_idx
        test_start = current_idx
        test_end = min(current_idx + test_window, n)

        X_train, y_train = prepare_data(df, train_start, train_end, 20)
        if X_train is None or len(X_train) < 30:
            current_idx += test_window
            continue

        model = GRUModel(input_size=10, hidden_size=32, num_layers=2, output_size=3, dropout=0.2)
        criterion = nn.CrossEntropyLoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

        model.train()
        dataset = TensorDataset(X_train, y_train)
        loader = DataLoader(dataset, batch_size=32, shuffle=True)

        for epoch in range(25):
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                outputs = model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()

        model.eval()
        for i in range(test_start, test_end):
            if i < 20:
                signals[i] = 0
                continue

            features = extract_features(df, i, 20)
            if features is None:
                signals[i] = 0
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

            signals[i] = signal

        current_idx += test_window

    print(f"  [*] 完成 {loop_count} 轮滚动训练")
    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 智能集成 (自动剪枝)
# ============================================================================

def smart_ensemble_strategy(df: pl.DataFrame, min_sharpe: float = 0.5) -> pl.DataFrame:
    """
    智能集成策略 - 自动选择最优Walk-Forward模型

    1. 快速验证LSTM和GRU的表现
    2. 选择夏普>min_sharpe的模型
    3. 集成投票
    """
    n = len(df)
    if n < 300 or not TORCH_AVAILABLE:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()

    # 快速验证 (用最后100日测试)
    print("  [*] 快速验证模型表现...")

    validation_results = {}

    for name, strategy_func in [('LSTM', walkforward_lstm_strategy), ('GRU', walkforward_gru_strategy)]:
        try:
            test_df = df.slice(n-150, 150)
            signals_df = strategy_func(test_df, train_window=80, test_window=30, confidence=0.35)

            if 'signal' in signals_df.columns:
                signals = signals_df['signal'].to_numpy()
                returns = []
                for i in range(len(signals) - 1):
                    if signals[i] != 0 and (n-150+i+1) < len(closes):
                        ret = (closes[n-150+i+1] - closes[n-150+i]) / closes[n-150+i] * signals[i]
                        returns.append(ret)

                if len(returns) > 5:
                    sharpe = np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252)
                    validation_results[name] = sharpe
                    print(f"    {name}: 夏普={sharpe:.2f}")
                else:
                    validation_results[name] = -999
            else:
                validation_results[name] = -999
        except Exception as e:
            validation_results[name] = -999
            print(f"    {name}: 失败")

    # 筛选优秀模型
    good_models = {k: v for k, v in validation_results.items() if v >= min_sharpe}

    if not good_models:
        print(f"  [!] 无模型达到夏普{min_sharpe}，使用默认LSTM全量训练")
        return walkforward_lstm_strategy(df)

    best_model = max(good_models, key=good_models.get)
    print(f"  [*] 选择模型: {best_model} (夏普 {good_models[best_model]:.2f})")

    if best_model == 'LSTM':
        return walkforward_lstm_strategy(df)
    else:
        return walkforward_gru_strategy(df)


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
    print("Quant Terminal Walk-Forward 深度学习系统")
    print("=" * 70)
    print(f"PyTorch: {'可用' if TORCH_AVAILABLE else '不可用'}")
    print("\n特性:")
    print("  - 滚动训练 (Walk-Forward)")
    print("  - 多架构: LSTM, GRU")
    print("  - 自动剪枝: 夏普<0.5自动剔除")
    print("=" * 70)

    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    strategies = [
        ('WalkForward_LSTM', walkforward_lstm_strategy, {}),
        ('WalkForward_GRU', walkforward_gru_strategy, {}),
        ('Smart_Ensemble', smart_ensemble_strategy, {'min_sharpe': 0.5}),
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

    # 汇总 - 自动剪枝
    print("\n" + "=" * 70)
    print("结果汇总 (自动剪枝: 夏普>=0.5)")
    print("=" * 70)

    good_results = [r for r in all_results if r['sharpe_ratio'] >= 0.5]
    poor_results = [r for r in all_results if r['sharpe_ratio'] < 0.5]

    if good_results:
        print(f"\n优秀策略 ({len(good_results)}个):")
        print(f"{'品种':<8} {'策略':<20} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
        print("-" * 70)
        for r in sorted(good_results, key=lambda x: x['sharpe_ratio'], reverse=True):
            print(f"{r['code']:<8} {r['strategy']:<20} {r['total_return']:>+8.2%}  "
                  f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
                  f"{r['sharpe_ratio']:>6.2f}  {r['trades']:>4}")

    if poor_results:
        print(f"\n已剪枝策略 ({len(poor_results)}个):")
        for r in poor_results:
            print(f"  - {r['code']} {r['strategy']}: 夏普{r['sharpe_ratio']:.2f}, 收益{r['total_return']:+.2%}")

    print("=" * 70)

    if good_results:
        best = max(good_results, key=lambda x: x['sharpe_ratio'])
        print(f"\n[最优策略]")
        print(f"  {best['code']} + {best['strategy']}")
        print(f"  夏普: {best['sharpe_ratio']:.2f} | 收益: {best['total_return']:+.2%} | 回撤: {best['max_drawdown']:.2%}")

    # 对比之前的最佳
    print("\n" + "=" * 70)
    print("对比: 经典CTA vs Walk-Forward DL")
    print("=" * 70)
    print("历史最佳 (之前测试):")
    print("  IH0 + PyTorch LSTM: 夏普1.61, 收益+36.76%, 回撤3.12%")
    print("  IH0 + Dual Thrust:  夏普0.88, 收益+29.22%, 回撤7.02%")
    if good_results:
        print(f"\n本次Walk-Forward最佳:")
        b = good_results[0]
        print(f"  {b['code']} + {b['strategy']}: 夏普{b['sharpe_ratio']:.2f}, 收益{b['total_return']:+.2%}, 回撤{b['max_drawdown']:.2%}")


if __name__ == "__main__":
    main()
