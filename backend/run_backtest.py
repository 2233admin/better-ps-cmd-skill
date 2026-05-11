#!/usr/bin/env python3
"""导入历史数据到 DuckDB 并运行回测"""

import sys
import os
sys.path.insert(0, 'app')

from pathlib import Path
import duckdb
import polars as pl
from datetime import datetime

# 使用不同的数据库路径
DB_PATH = Path('C:/Users/Administrator/quant-terminal/data/quant_test.duckdb')
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

conn = duckdb.connect(str(DB_PATH))

# 初始化表
conn.execute("""
    CREATE TABLE IF NOT EXISTS kline_daily (
        code VARCHAR,
        market INTEGER,
        date DATE,
        open DOUBLE,
        high DOUBLE,
        low DOUBLE,
        close DOUBLE,
        volume BIGINT,
        amount DOUBLE,
        PRIMARY KEY (code, market, date)
    )
""")

print("正在获取历史数据...")
from data.http_feed import AdataFeed
feed = AdataFeed()

# 获取贵州茅台(600519)的日线数据
kline_data = feed.get_kline("600519", klt=101, count=500)

if not kline_data:
    print("获取数据失败")
    sys.exit(1)

print(f"获取到 {len(kline_data)} 条K线数据")

# 转换数据格式并保存
df_data = []
for item in kline_data:
    df_data.append({
        "date": item["datetime"],
        "open": item["open"],
        "high": item["high"],
        "low": item["low"],
        "close": item["close"],
        "volume": int(item["vol"]),
        "amount": item.get("amount", 0),
    })

df = pl.DataFrame(df_data)
df = df.with_columns([
    pl.lit("600519").alias("code"),
    pl.lit(1).alias("market"),
])

conn.execute("""
    INSERT OR REPLACE INTO kline_daily
    SELECT code, market, date::DATE, open, high, low, close, volume::BIGINT, amount
    FROM df
""")
print(f"已保存 {len(df_data)} 条记录到 DuckDB")

# 从数据库读取数据
data = conn.execute("SELECT * FROM kline_daily WHERE code = '600519' ORDER BY date").pl()
print(f"从数据库读取 {len(data)} 条记录")

if data.is_empty():
    print("数据库中没有数据")
    sys.exit(1)

# 生成信号
print("生成交易信号...")
from strategy.signals import generate_signals

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
    sigs = generate_signals("momentum_breakout", [quote], {"vol_threshold": 3.0})
    if sigs:
        sig = sigs[0]
        signal_values.append(1 if sig.direction == "buy" else -1)
    else:
        signal_values.append(0)

# 创建信号DataFrame
time_col = "date"
signals_df = pl.DataFrame({
    time_col: data[time_col],
    "signal": signal_values,
})

# 运行回测
print("运行回测...")
from strategy.backtest import VectorBacktester

backtester = VectorBacktester(
    initial_capital=1_000_000,
    commission=0.0005,
    slippage=0.001,
)
result = backtester.run(data, signals_df)

# 输出结果
print("\n" + "="*50)
print("回测结果 - 贵州茅台(600519)")
print("="*50)
print(f"策略: 动量突破")
print(f"数据条数: {len(data)}")
print(f"交易次数: {result.total_trades}")
print(f"总收益率: {result.total_return:.2%}")
print(f"年化收益率: {result.annual_return:.2%}")
print(f"夏普比率: {result.sharpe_ratio:.2f}")
print(f"最大回撤: {result.max_drawdown:.2%}")
print(f"胜率: {result.win_rate:.2%}")
print(f"盈利交易: {result.profit_trades}")
print(f"亏损交易: {result.loss_trades}")
print(f"平均盈利: {result.avg_profit:.2%}")
print(f"平均亏损: {result.avg_loss:.2%}")
print(f"盈利因子: {result.profit_factor:.2f}")

print(f"\n权益曲线 (最近10个点):")
for i, eq in enumerate(result.equity_curve[-10:], 1):
    print(f"  点{i}: {eq:,.0f}")

conn.close()
print("\n完成!")
