#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""期货策略回测运行脚本

用法:
    python run_futures_backtest.py --code IF0 --strategy ma_cross --start 20240101 --end 20250328
"""

import sys
sys.path.insert(0, "C:/Users/Administrator/quant-terminal/backend")

import argparse
from datetime import datetime
from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.strategy.futures_strategies import (
    futures_ma_cross_strategy,
    futures_rsi_strategy,
    futures_breakout_strategy,
    FuturesBacktester,
)
from loguru import logger


STRATEGIES = {
    "ma_cross": futures_ma_cross_strategy,
    "rsi": futures_rsi_strategy,
    "breakout": futures_breakout_strategy,
}


def main():
    parser = argparse.ArgumentParser(description="期货策略回测")
    parser.add_argument("--code", type=str, default="IF0", help="合约代码 (如 IF0, IC0)")
    parser.add_argument("--strategy", type=str, default="ma_cross", choices=STRATEGIES.keys(), help="策略名称")
    parser.add_argument("--period", type=str, default="daily", help="数据周期 (daily, 5min, 15min)")
    parser.add_argument("--start", type=str, default="20240101", help="开始日期 (YYYYMMDD)")
    parser.add_argument("--end", type=str, default="20250328", help="结束日期 (YYYYMMDD)")
    parser.add_argument("--capital", type=float, default=1_000_000, help="初始资金")
    parser.add_argument("--margin", type=float, default=0.12, help="保证金比例")

    args = parser.parse_args()

    print("=" * 70)
    print("期货策略回测")
    print("=" * 70)
    print(f"合约: {args.code}")
    print(f"策略: {args.strategy}")
    print(f"周期: {args.period}")
    print(f"时间: {args.start} - {args.end}")
    print(f"初始资金: {args.capital:,.0f}")
    print("=" * 70)

    # 1. 获取数据
    print("\n[1/3] 获取历史数据...")
    try:
        fetch_and_save_futures_data(
            code=args.code,
            period=args.period,
            start_date=args.start,
            end_date=args.end,
        )
    except Exception as e:
        logger.warning(f"获取数据时出错 (可能已存在): {e}")

    # 2. 加载数据
    print("\n[2/3] 加载数据...")
    data = load_futures_for_backtest(
        code=args.code,
        period=args.period,
        start_date=f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:]}",
        end_date=f"{args.end[:4]}-{args.end[4:6]}-{args.end[6:]}",
    )

    if data.is_empty():
        print("错误: 未能加载数据，请先确认数据已下载")
        return

    print(f"加载了 {len(data)} 条数据")
    print(f"数据范围: {data[0, 'date'] if 'date' in data.columns else data[0, 'datetime']} ~ "
          f"{data[-1, 'date'] if 'date' in data.columns else data[-1, 'datetime']}")

    # 3. 生成信号
    print("\n[3/3] 运行回测...")
    strategy_func = STRATEGIES[args.strategy]
    signals = strategy_func(data)

    # 统计信号
    buy_signals = signals.filter(pl.col("signal") == 1).shape[0]
    sell_signals = signals.filter(pl.col("signal") == -1).shape[0]
    print(f"买入信号: {buy_signals} 次")
    print(f"卖出信号: {sell_signals} 次")

    # 4. 运行回测
    backtester = FuturesBacktester(
        initial_capital=args.capital,
        margin_ratio=args.margin,
    )

    result = backtester.run(data, signals)

    # 5. 输出结果
    print("\n" + "=" * 70)
    print("回测结果")
    print("=" * 70)
    print(f"总收益率:     {result['total_return']:.2%}")
    print(f"年化收益率:   {result['annual_return']:.2%}")
    print(f"最大回撤:     {result['max_drawdown']:.2%}")
    print(f"夏普比率:     {result['sharpe']:.2f}")
    print(f"交易次数:     {result['total_trades']}")
    print(f"最终资金:     {result['final_capital']:,.2f}")
    print("=" * 70)


if __name__ == "__main__":
    import polars as pl  # 确保导入
    main()
