#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
期货策略回测报告生成器
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

from app.data.futures_feed import fetch_and_save_futures_data, load_futures_for_backtest
from app.strategy.futures_strategies import futures_ma_cross_strategy, futures_rsi_strategy, FuturesBacktester
import polars as pl
from datetime import datetime


def run_backtest_report():
    """生成回测报告"""

    # 回测配置
    configs = [
        {'code': 'IF0', 'name': '沪深300股指', 'strategy': 'ma_cross'},
        {'code': 'IC0', 'name': '中证500股指', 'strategy': 'ma_cross'},
        {'code': 'IH0', 'name': '上证50股指', 'strategy': 'ma_cross'},
    ]

    results = []

    print("=" * 80)
    print("期货策略回测报告")
    print("=" * 80)
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    for config in configs:
        print(f"\n【{config['name']}({config['code']})】")
        print("-" * 80)

        try:
            # 下载/加载数据
            fetch_and_save_futures_data(
                config['code'],
                'daily',
                start_date='20230101',
                end_date='20250328'
            )

            data = load_futures_for_backtest(config['code'], 'daily')

            if data.is_empty():
                print(f"  错误: 无法获取数据")
                continue

            print(f"  数据量: {len(data)} 个交易日")
            print(f"  时间: {data[0, 'date']} ~ {data[-1, 'date']}")

            # 策略
            if config['strategy'] == 'ma_cross':
                signals = futures_ma_cross_strategy(data, fast_period=5, slow_period=20)
                print(f"  策略: 均线交叉 (MA5/MA20)")

            # 统计信号
            buy_count = signals.filter(pl.col('signal') == 1).shape[0]
            sell_count = signals.filter(pl.col('signal') == -1).shape[0]
            print(f"  买入信号: {buy_count} 次")
            print(f"  卖出信号: {sell_count} 次")

            # 回测
            backtester = FuturesBacktester(
                initial_capital=1_000_000,
                margin_ratio=0.12,
                commission=0.0001,
                slippage=0.0002
            )

            result = backtester.run(data, signals)

            print(f"\n  回测结果:")
            print(f"    总收益率:   {result['total_return']:+.2%}")
            print(f"    年化收益:   {result['annual_return']:+.2%}")
            print(f"    最大回撤:   {result['max_drawdown']:.2%}")
            print(f"    夏普比率:   {result['sharpe']:.2f}")
            print(f"    交易次数:   {result['total_trades']}")
            print(f"    最终资金:   {result['final_capital']:,.2f}")

            results.append({
                'code': config['code'],
                'name': config['name'],
                **result
            })

        except Exception as e:
            print(f"  错误: {e}")

    # 汇总
    print("\n" + "=" * 80)
    print("汇总对比")
    print("=" * 80)
    print(f"{'品种':<15} {'总收益':<12} {'年化':<12} {'回撤':<10} {'夏普':<8} {'交易':<8}")
    print("-" * 80)

    for r in results:
        print(f"{r['name']:<15} {r['total_return']:>+10.2%}  {r['annual_return']:>+10.2%}  "
              f"{r['max_drawdown']:>8.2%}  {r['sharpe']:>6.2f}  {r['total_trades']:>6}")

    print("=" * 80)

    # 保存报告
    report_file = f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write("期货策略回测报告\n")
        f.write("=" * 80 + "\n")
        for r in results:
            f.write(f"\n{r['name']}({r['code']}):\n")
            f.write(f"  总收益率: {r['total_return']:+.2%}\n")
            f.write(f"  年化收益: {r['annual_return']:+.2%}\n")
            f.write(f"  最大回撤: {r['max_drawdown']:.2%}\n")
            f.write(f"  夏普比率: {r['sharpe']:.2f}\n")

    print(f"\n报告已保存: {report_file}")


if __name__ == "__main__":
    run_backtest_report()
