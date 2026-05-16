#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal 智能策略系统

特性:
- Walk-Forward 滚动训练/测试
- 多架构深度学习: LSTM, GRU, Transformer
- 自动剪枝: 只保留夏普>0.5的策略
- 集成投票: 多模型动态加权
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

# PyTorch
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


# ============================================================================
# 深度学习模型架构
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
    """GRU预测模型 - 比LSTM更快"""
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


class TransformerModel(nn.Module):
    """Transformer时间序列预测"""
    def __init__(self, input_size=10, d_model=64, nhead=4, num_layers=2, output_size=3, dropout=0.2):
        super().__init__()
        self.embedding = nn.Linear(input_size, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dropout=dropout, batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc = nn.Linear(d_model, output_size)
        self.softmax = nn.Softmax(dim=1)

    def forward(self, x):
        x = self.embedding(x)
        out = self.transformer(x)
        out = self.fc(out[:, -1, :])
        return self.softmax(out)


# ============================================================================
# 特征工程
# ============================================================================

def extract_features(df: pl.DataFrame, i: int, window: int = 20) -> np.ndarray:
    """提取标准化特征序列"""
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
            ret, high_low_ratio, open_close_ratio,
            vol_ratio, position,
            closes[j] / closes[i-window] - 1,
            (closes[j] - np.mean(closes[max(0, j-5):j+1])) / (np.std(closes[max(0, j-5):j+1]) + 1e-10),
            j % 5 / 5,
            np.log(volumes[j] + 1),
            (highs[j] - closes[j]) / (highs[j] - lows[j] + 1e-10)
        ])

    return np.array(features)


def prepare_training_data(df: pl.DataFrame, start_idx: int, end_idx: int, seq_length: int = 20):
    """准备训练数据"""
    closes = df['close'].to_numpy()
    X, y = [], []

    for i in range(start_idx + seq_length, end_idx - 5):
        features = extract_features(df, i, seq_length)
        if features is not None:
            X.append(features)
            future_ret = (closes[i+5] - closes[i]) / closes[i]
            if future_ret > 0.015:
                y.append(0)  # 上涨
            elif future_ret < -0.015:
                y.append(1)  # 下跌
            else:
                y.append(2)  # 震荡

    if len(X) < 30:
        return None, None

    return torch.FloatTensor(np.array(X)), torch.LongTensor(y)


# ============================================================================
# Walk-Forward 深度学习策略
# ============================================================================

def walkforward_dl_strategy(
    df: pl.DataFrame,
    model_type: str = 'LSTM',
    train_window: int = 100,
    test_window: int = 50,
    seq_length: int = 20,
    confidence_threshold: float = 0.4
) -> pl.DataFrame:
    """
    Walk-Forward 深度学习策略

    滚动训练流程:
    1. 用 [t-train_window, t] 数据训练
    2. 在 [t, t+test_window] 预测
    3. 移动到 t+test_window，重复
    """
    n = len(df)
    if n < train_window + test_window + seq_length or not TORCH_AVAILABLE:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    signals = [0] * n
    position = 0

    # 初始化模型
    if model_type == 'LSTM':
        model_class = LSTMModel
    elif model_type == 'GRU':
        model_class = GRUModel
    else:
        model_class = TransformerModel

    # Walk-forward循环
    current_idx = train_window + seq_length

    while current_idx < n - test_window:
        train_start = max(0, current_idx - train_window)
        train_end = current_idx
        test_start = current_idx
        test_end = min(current_idx + test_window, n)

        # 准备训练数据
        X_train, y_train = prepare_training_data(df, train_start, train_end, seq_length)

        if X_train is None or len(X_train) < 30:
            current_idx += test_window
            continue

        # 训练模型
        if model_type == 'Transformer':
            model = TransformerModel(input_size=10, d_model=32, nhead=4, num_layers=2, output_size=3, dropout=0.2)
        else:
            model = model_class(input_size=10, hidden_size=32, num_layers=2, output_size=3, dropout=0.2)
        criterion = nn.CrossEntropyLoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

        model.train()
        dataset = TensorDataset(X_train, y_train)
        loader = DataLoader(dataset, batch_size=16, shuffle=True)

        for epoch in range(20):
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                outputs = model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()

        # 在测试窗口预测
        model.eval()
        for i in range(test_start, test_end):
            if i < seq_length:
                signals[i] = 0
                continue

            features = extract_features(df, i, seq_length)
            if features is None:
                signals[i] = 0
                continue

            X = torch.FloatTensor(features).unsqueeze(0)
            with torch.no_grad():
                pred = model(X)
                pred_class = torch.argmax(pred, dim=1).item()
                confidence = torch.max(pred).item()

            # 生成信号
            signal = 0
            if confidence > confidence_threshold:
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

            signals[i] = signal

        current_idx += test_window

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 多模型集成策略
# ============================================================================

def ensemble_dl_strategy(df: pl.DataFrame, min_sharpe: float = 0.5) -> pl.DataFrame:
    """
    多模型集成策略 - 自动剪枝

    1. 训练 LSTM, GRU, Transformer
    2. 验证表现，剔除夏普<min_sharpe的模型
    3. 剩余模型动态加权投票
    """
    n = len(df)
    if n < 200 or not TORCH_AVAILABLE:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()
    models = {
        'LSTM': LSTMModel(input_size=10, hidden_size=32, num_layers=2, output_size=3),
        'GRU': GRUModel(input_size=10, hidden_size=32, num_layers=2, output_size=3),
        'Transformer': TransformerModel(input_size=10, d_model=32, nhead=4, num_layers=2, output_size=3)
    }

    # Walk-forward集成
    signals = [0] * n
    position = 0

    model_weights = {name: 1.0 for name in models}
    model_returns = {name: [] for name in models}

    current_idx = 150

    while current_idx < n - 50:
        # 训练所有模型
        train_start = max(0, current_idx - 100)
        train_end = current_idx
        test_start = current_idx
        test_end = min(current_idx + 50, n)

        X_train, y_train = prepare_training_data(df, train_start, train_end, 20)
        if X_train is None:
            current_idx += 50
            continue

        trained_models = {}
        for name, model in models.items():
            if name == 'Transformer':
                model_instance = TransformerModel(input_size=10, d_model=32, nhead=4, num_layers=2, output_size=3)
            else:
                model_instance = type(model)(input_size=10, hidden_size=32, num_layers=2, output_size=3)
            criterion = nn.CrossEntropyLoss()
            optimizer = torch.optim.Adam(model_instance.parameters(), lr=0.001)

            model_instance.train()
            dataset = TensorDataset(X_train, y_train)
            loader = DataLoader(dataset, batch_size=16, shuffle=True)

            for _ in range(15):
                for batch_X, batch_y in loader:
                    optimizer.zero_grad()
                    outputs = model_instance(batch_X)
                    loss = criterion(outputs, batch_y)
                    loss.backward()
                    optimizer.step()

            trained_models[name] = model_instance

        # 在验证期评估并更新权重
        for name, model in trained_models.items():
            model.eval()
            preds = []
            for i in range(test_start, min(test_start + 20, n)):
                features = extract_features(df, i, 20)
                if features is not None:
                    X = torch.FloatTensor(features).unsqueeze(0)
                    with torch.no_grad():
                        pred = model(X)
                        pred_class = torch.argmax(pred, dim=1).item()
                        confidence = torch.max(pred).item()

                        if confidence > 0.4:
                            if pred_class == 0:
                                preds.append(1)
                            elif pred_class == 1:
                                preds.append(-1)
                            else:
                                preds.append(0)
                        else:
                            preds.append(0)

            # 计算该模型在该窗口的收益率
            if len(preds) > 5:
                rets = [(closes[i+1] - closes[i]) / closes[i] for i in range(test_start, test_start + len(preds) - 1)]
                model_ret = sum([r * p for r, p in zip(rets, preds[:-1]) if p != 0])
                model_returns[name].append(model_ret)

        # 动态调整权重 (最近3个窗口)
        for name in model_weights:
            if len(model_returns[name]) >= 3:
                recent_rets = model_returns[name][-3:]
                sharpe = np.mean(recent_rets) / (np.std(recent_rets) + 1e-10)

                # 自动剪枝: 夏普<0.5的模型降低权重
                if sharpe < min_sharpe:
                    model_weights[name] = max(0.1, model_weights[name] * 0.7)
                else:
                    model_weights[name] = min(2.0, model_weights[name] * 1.1)

        # 归一化权重
        total_weight = sum(model_weights.values())
        if total_weight > 0:
            model_weights = {k: v / total_weight for k, v in model_weights.items()}

        # 使用加权投票预测下一窗口
        for i in range(test_start, test_end):
            if i < 20:
                signals[i] = 0
                continue

            features = extract_features(df, i, 20)
            if features is None:
                signals[i] = 0
                continue

            votes = []
            for name, model in trained_models.items():
                X = torch.FloatTensor(features).unsqueeze(0)
                with torch.no_grad():
                    pred = model(X)
                    pred_class = torch.argmax(pred, dim=1).item()
                    confidence = torch.max(pred).item()

                    if confidence > 0.35:
                        if pred_class == 0:
                            votes.append(1 * model_weights[name])
                        elif pred_class == 1:
                            votes.append(-1 * model_weights[name])

            final_score = sum(votes)

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

            signals[i] = signal

        current_idx += 50

    return df.with_columns([pl.Series('signal', signals)])


# ============================================================================
# 智能策略选择器 (自动剪枝)
# ============================================================================

def smart_strategy_selector(df: pl.DataFrame, min_sharpe: float = 0.5) -> pl.DataFrame:
    """
    智能策略选择器 - 自动测试并选择最优策略
    """
    n = len(df)
    if n < 200:
        return df.with_columns([pl.Series('signal', [0] * n)])

    closes = df['close'].to_numpy()

    # 测试不同策略配置
    strategies_to_test = [
        ('LSTM_walk', lambda d: walkforward_dl_strategy(d, 'LSTM', 100, 50)),
        ('GRU_walk', lambda d: walkforward_dl_strategy(d, 'GRU', 100, 50)),
        ('Transformer_walk', lambda d: walkforward_dl_strategy(d, 'Transformer', 100, 50)),
    ]

    if TORCH_AVAILABLE:
        strategies_to_test.append(('Ensemble', ensemble_dl_strategy))

    # Walk-forward测试每种策略
    strategy_scores = {}

    for name, strategy_func in strategies_to_test:
        returns = []
        # 使用最近200日做快速回测
        test_df = df.slice(n-200, 200)

        try:
            signals_df = strategy_func(test_df)
            if 'signal' in signals_df.columns:
                signals = signals_df['signal'].to_numpy()
                for i in range(len(signals) - 1):
                    if signals[i] != 0:
                        ret = (closes[n-200+i+1] - closes[n-200+i]) / closes[n-200+i] * signals[i]
                        returns.append(ret)

                if len(returns) > 10:
                    sharpe = np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252)
                    strategy_scores[name] = sharpe
                else:
                    strategy_scores[name] = -999
            else:
                strategy_scores[name] = -999
        except Exception as e:
            strategy_scores[name] = -999

    # 选择最优策略
    if not strategy_scores:
        return df.with_columns([pl.Series('signal', [0] * n)])

    best_strategy = max(strategy_scores, key=strategy_scores.get)
    best_sharpe = strategy_scores[best_strategy]

    print(f"  策略选择: {best_strategy} (验证夏普: {best_sharpe:.2f})")

    # 如果最优策略夏普<min_sharpe，返回空仓
    if best_sharpe < min_sharpe:
        print(f"  [!] 所有策略夏普<{min_sharpe}，使用默认趋势策略")
        return default_trend_strategy(df)

    # 使用最优策略全量预测
    best_func = [f for n, f in strategies_to_test if n == best_strategy][0]
    return best_func(df)


def default_trend_strategy(df: pl.DataFrame) -> pl.DataFrame:
    """默认趋势跟踪策略"""
    n = len(df)
    closes = df['close'].to_numpy()
    signals = []
    position = 0

    for i in range(n):
        if i < 20:
            signals.append(0)
            continue

        sma5 = np.mean(closes[max(0, i-5):i])
        sma20 = np.mean(closes[max(0, i-20):i])

        signal = 0
        if sma5 > sma20 * 1.005:
            signal = 1
        elif sma5 < sma20 * 0.995:
            signal = -1

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
        'code': code,
        'strategy': strategy_name,
        'total_return': result.total_return,
        'annual_return': result.annual_return,
        'max_drawdown': result.max_drawdown,
        'sharpe_ratio': result.sharpe_ratio,
        'trades': result.total_trades,
    }


def main():
    """主函数"""
    print("=" * 70)
    print("Quant Terminal 智能策略系统")
    print("=" * 70)
    print(f"PyTorch: {'可用' if TORCH_AVAILABLE else '不可用'}")
    print("\n特性:")
    print("  - Walk-Forward 滚动训练")
    print("  - 多架构: LSTM, GRU, Transformer")
    print("  - 自动剪枝: 只保留夏普>0.5的策略")
    print("  - 动态集成投票")
    print("=" * 70)

    codes = ['IF0', 'IC0', 'IH0']
    for code in codes:
        print(f"\n[*] 下载 {code} 数据...")
        fetch_and_save_futures_data(code, 'daily', start_date='20230101', end_date='20250328')

    strategies = [
        ('LSTM_WalkForward', walkforward_dl_strategy, {'model_type': 'LSTM'}),
        ('GRU_WalkForward', walkforward_dl_strategy, {'model_type': 'GRU'}),
        ('Transformer_WalkForward', walkforward_dl_strategy, {'model_type': 'Transformer'}),
    ]

    if TORCH_AVAILABLE:
        strategies.append(('Ensemble_AutoPrune', ensemble_dl_strategy, {'min_sharpe': 0.5}))
        strategies.append(('Smart_Selector', smart_strategy_selector, {'min_sharpe': 0.5}))

    all_results = []

    for code in codes:
        print(f"\n{'='*70}")
        print(f"品种: {code}")
        print('='*70)

        for strategy_name, strategy_func, kwargs in strategies:
            result = run_backtest(code, strategy_name, strategy_func, **kwargs)
            if result:
                all_results.append(result)

    # 剪枝: 只显示夏普>0.5的策略
    print("\n" + "=" * 70)
    print("结果汇总 (自动剪枝: 夏普>0.5)")
    print("=" * 70)

    good_results = [r for r in all_results if r['sharpe_ratio'] > 0.5]
    poor_results = [r for r in all_results if r['sharpe_ratio'] <= 0.5]

    if good_results:
        print(f"\n优秀策略 ({len(good_results)}个):")
        print(f"{'品种':<8} {'策略':<25} {'总收益':<10} {'年化':<10} {'回撤':<8} {'夏普':<8} {'交易':<6}")
        print("-" * 70)
        for r in sorted(good_results, key=lambda x: x['sharpe_ratio'], reverse=True):
            print(f"{r['code']:<8} {r['strategy']:<25} {r['total_return']:>+8.2%}  "
                  f"{r['annual_return']:>+8.2%}  {r['max_drawdown']:>6.2%}  "
                  f"{r['sharpe_ratio']:>6.2f}  {r['trades']:>4}")

    if poor_results:
        print(f"\n已剪枝策略 (夏普<=0.5, 共{len(poor_results)}个):")
        for r in poor_results:
            print(f"  - {r['code']} {r['strategy']}: 夏普{r['sharpe_ratio']:.2f}")

    print("=" * 70)

    if good_results:
        best = max(good_results, key=lambda x: x['sharpe_ratio'])
        print(f"\n[最优策略]")
        print(f"  {best['code']} + {best['strategy']}")
        print(f"  夏普: {best['sharpe_ratio']:.2f} | 收益: {best['total_return']:+.2%} | 回撤: {best['max_drawdown']:.2%}")


if __name__ == "__main__":
    main()
