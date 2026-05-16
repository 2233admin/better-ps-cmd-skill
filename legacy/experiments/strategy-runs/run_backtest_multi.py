#!/usr/bin/env python3
"""使用不同策略运行回测"""

import sys
import os
sys.path.insert(0, 'app')

from pathlib import Path
import duckdb
import polars as pl

# 使用测试数据库
DB_PATH = Path('C:/Users/Administrator/quant-terminal/data/quant_test.duckdb')
conn = duckdb.connect(str(DB_PATH))

# 从数据库读取数据
data = conn.execute("SELECT * FROM kline_daily WHERE code = '600519' ORDER BY date").pl()
print(f"从数据库读取 {len(data)} 条记录")

if data.is_empty():
    print("数据库中没有数据，请先运行 run_backtest.py 导入数据")
    sys.exit(1)

from strategy.signals import generate_signals
from strategy.backtest import VectorBacktester

# 测试多种策略
strategies = [
    ("momentum_breakout", "动量突破", {"vol_threshold": 3.0}),
    ("mean_reversion", "均值回归", {"deviation_threshold": 0.02}),
    ("mean_reversion", "均值回归(宽松)", {"deviation_threshold": 0.01}),
]

for strategy_id, strategy_name, params in strategies:
    print(f"\n{'='*60}")
    print(f"策略: {strategy_name} ({strategy_id})")
    print(f"参数: {params}")
    print('='*60)

    # 生成信号
    signal_values = []
    for row in data.iter_rows(named=True):
        quote = {
            "code": "600519",
            "price": row.get("close", 0),
            "vol": row.get("volume", 0),
            "open": row.get("open", 0),
            "high": row.get("high", 0),
            "low": row.get("low", 0),
        }
        sigs = generate_signals(strategy_id, [quote], params)
        if sigs:
            sig = sigs[0]
            signal_values.append(1 if sig.direction == "buy" else -1)
        else:
            signal_values.append(0)

    # 统计信号数量
    buy_signals = sum(1 for s in signal_values if s == 1)
    sell_signals = sum(1 for s in signal_values if s == -1)
    print(f"买入信号: {buy_signals}, 卖出信号: {sell_signals}")

    # 创建信号DataFrame
    signals_df = pl.DataFrame({
        "date": data["date"],
        "signal": signal_values,
    })

    # 运行回测
    backtester = VectorBacktester(
        initial_capital=1_000_000,
        commission=0.0005,
        slippage=0.001,
    )
    result = backtester.run(data, signals_df)

    # 输出结果
    print(f"\n回测结果:")
    print(f"  交易次数: {result.total_trades}")
    print(f"  总收益率: {result.total_return:.2%}")
    print(f"  年化收益率: {result.annual_return:.2%}")
    print(f"  夏普比率: {result.sharpe_ratio:.2f}")
    print(f"  最大回撤: {result.max_drawdown:.2%}")
    print(f"  胜率: {result.win_rate:.2%}")
    print(f"  盈利交易: {result.profit_trades}")
    print(f"  亏损交易: {result.loss_trades}")
    if result.profit_trades > 0:
        print(f"  平均盈利: {result.avg_profit:.2%}")
    if result.loss_trades > 0:
        print(f"  平均亏损: {result.avg_loss:.2%}")
    if result.profit_factor != float('inf'):
        print(f"  盈利因子: {result.profit_factor:.2f}")

conn.close()
print("\n\n完成!")
