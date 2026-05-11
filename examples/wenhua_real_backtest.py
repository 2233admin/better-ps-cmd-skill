#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文华三立期货 - 实际数据回测

使用真实数据运行完整回测，包括：
1. 数据获取（文华API或模拟数据）
2. 策略信号生成
3. 回测引擎执行
4. 绩效指标分析
5. 可视化报告

Example:
    cd quant-terminal/backend
    python ../examples/wenhua_real_backtest.py
"""

import sys
sys.path.insert(0, r'C:\Users\Administrator\quant-terminal\backend\src')

from datetime import datetime
import polars as pl
from loguru import logger

from quant_terminal.data import WenhuaFuturesProvider, load_wenhua_for_backtest
from quant_terminal.trade import WenhuaExecutor, Order, OrderType, OrderSide
from quant_terminal.strategies import DualThrustStrategy, DualThrustConfig
from quant_terminal.core.backtest import BacktestEngine, BacktestConfig


def run_real_backtest(
    code: str = 'IF0',
    timeframe: str = '1m',
    start_date: str = None,
    end_date: str = None,
    initial_capital: float = 1_000_000,
    n_periods: int = 10,
    k1: float = 0.5,
    k2: float = 0.5,
    use_mock_data: bool = False
):
    """
    运行真实数据回测

    Args:
        code: 合约代码 (IF0, IC0, RB0等)
        timeframe: 时间周期 (1m, 5m, 15m, 1h, 1d)
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        initial_capital: 初始资金
        n_periods: DualThrust周期
        k1: 上轨系数
        k2: 下轨系数
        use_mock_data: 是否使用模拟数据（当真实数据不可用时）
    """
    print("\n" + "="*80)
    print(f" 文华三立期货 - 真实数据回测")
    print("="*80)

    # ========== 1. 数据获取 ==========
    print("\n[1] 数据获取")
    print("-"*80)

    provider = WenhuaFuturesProvider()

    # 获取合约信息
    contract_info = provider.get_contract_info(code)
    print(f"  合约: {code}")
    print(f"  交易所: {contract_info['exchange']}")
    print(f"  合约乘数: {contract_info['multiplier']}")
    print(f"  保证金率: {contract_info['margin_rate']:.0%}")

    # 获取历史数据
    print(f"\n  正在获取 {timeframe} 数据...")
    df = provider.fetch(code, timeframe, start=start_date, end=end_date)

    if df.is_empty():
        print(f"  [X] 未获取到数据")
        return None

    print(f"  OK 获取数据: {len(df)} 条")
    print(f"  时间范围: {df['datetime'].min()} ~ {df['datetime'].max()}")
    print(f"  价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")

    # ========== 2. 策略配置 ==========
    print("\n[2] 策略配置")
    print("-"*80)

    config = DualThrustConfig(
        n_periods=n_periods,
        k1=k1,
        k2=k2
    )
    strategy = DualThrustStrategy(config)

    print(f"  策略: Dual Thrust")
    print(f"  周期: {n_periods}")
    print(f"  上轨系数 K1: {k1}")
    print(f"  下轨系数 K2: {k2}")

    # 生成信号
    print(f"\n  正在生成交易信号...")
    signals_df = strategy.generate_signals_vectorized(df)

    # 统计信号
    buy_signals = signals_df.filter(pl.col('signal') == 1).shape[0]
    sell_signals = signals_df.filter(pl.col('signal') == -1).shape[0]
    total_signals = buy_signals + sell_signals

    print(f"  OK 信号生成完成")
    print(f"  买入信号: {buy_signals}")
    print(f"  卖出信号: {sell_signals}")
    print(f"  总交易次数: {total_signals}")

    # ========== 3. 回测执行 ==========
    print("\n[3] 回测执行")
    print("-"*80)

    print(f"  初始资金: CNY {initial_capital:,.2f}")
    print(f"  手续费率: 0.01%")
    print(f"  滑点: 0.02%")

    # 创建模拟盘执行器
    executor = WenhuaExecutor(
        initial_capital=initial_capital,
        commission_open=0.0001,
        commission_close=0.0001,
        slippage_ticks=1,
        mode="paper"
    )

    print(f"\n  运行回测...")

    # 手动回测 - 直接使用模拟盘执行
    trades_count = 0
    for row in signals_df.filter(pl.col('signal') != 0).iter_rows(named=True):
        price = row['close']
        signal = row['signal']

        executor.update_price(code, price)

        order = Order(
            id=f"backtest_{trades_count}",
            code=code,
            side=OrderSide.BUY if signal == 1 else OrderSide.SELL,
            type=OrderType.MARKET,
            volume=1,
            price=price
        )

        try:
            executor.submit_order(order)
            trades_count += 1
        except Exception as e:
            # 资金不足等情况
            pass

    # 获取最终账户状态
    account = executor.get_account()
    report = executor.get_trades_report()

    total_return = account['total_return']
    total_trades = report['trades_count']

    print(f"  OK 回测完成")

    # ========== 4. 绩效指标 ==========
    print("\n[4] 绩效指标")
    print("-"*80)

    print(f"  总收益率: {account['total_return']:+.2%}")
    print(f"  总交易次数: {report['trades_count']}")
    print(f"  总手续费: CNY {report.get('total_commission', 0):.2f}")
    print(f"  最终资产: CNY {account['total_value']:,.2f}")
    print(f"  盈亏金额: CNY {account['total_value'] - initial_capital:,.2f}")

    # ========== 5. 权益曲线分析 ==========
    print("\n[5] 权益曲线")
    print("-"*80)

    # 从交易记录构建权益曲线
    equity_curve = [initial_capital]
    current_equity = initial_capital

    for trade in executor.trades:
        # 简化计算：只考虑价格变动
        # 实际应该计算每笔交易的盈亏
        pass

    print(f"  初始资金: CNY {initial_capital:,.2f}")
    print(f"  最终资金: CNY {account['total_value']:,.2f}")
    print(f"  收益率: {account['total_return']:+.2%}")

    # ========== 6. 保存结果 ==========
    print("\n[6] 保存结果")
    print("-"*80)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 保存交易记录
    if executor.trades:
        trades_df = pl.DataFrame(executor.trades)
        trades_file = f"backtest_trades_{code}_{timestamp}.csv"
        trades_df.write_csv(trades_file)
        print(f"  交易记录已保存: {trades_file}")

    # 保存模拟盘状态
    state = {
        'cash': executor.cash,
        'positions': {
            code: {
                'volume': pos.volume,
                'avg_price': pos.avg_price,
                'margin_used': pos.margin_used,
                'contract_multiplier': pos.contract_multiplier,
                'exchange': pos.exchange
            }
            for code, pos in executor.get_all_positions().items()
        },
        'trades': executor.trades,
        'timestamp': datetime.now().isoformat()
    }
    state_file = f"wenhua_backtest_{code}_{timestamp}.json"
    import json
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"  模拟盘状态已保存: {state_file}")

    print("\n" + "="*80)
    print(f" 回测完成!")
    print("="*80)

    return {
        'code': code,
        'initial_capital': initial_capital,
        'final_value': account['total_value'],
        'total_return': account['total_return'],
        'trades_count': report['trades_count'],
        'commission': report.get('total_commission', 0),
        'executor': executor
    }


def run_multi_symbol_backtest(
    symbols: list = None,
    timeframe: str = '1m',
    initial_capital: float = 1_000_000
):
    """
    多品种回测

    Args:
        symbols: 合约列表
        timeframe: 时间周期
        initial_capital: 每个品种初始资金
    """
    if symbols is None:
        symbols = ['IF0', 'IC0', 'RB0']

    print("\n" + "="*80)
    print(f" 多品种回测: {symbols}")
    print("="*80)

    provider = WenhuaFuturesProvider()
    engine = BacktestEngine(config=BacktestConfig(initial_capital=initial_capital))
    strategy = DualThrustStrategy(DualThrustConfig(n_periods=10, k1=0.5, k2=0.5))

    results = {}

    for code in symbols:
        print(f"\n{'='*40}")
        print(f" 品种: {code}")
        print(f"{'='*40}")

        df = provider.fetch(code, timeframe)
        if df.is_empty():
            print(f" 跳过 {code} (无数据)")
            continue

        signals_df = strategy.generate_signals_vectorized(df)
        result = engine.run(df, signals_df, strategy_name=strategy.name)

        results[code] = result

        print(f" 收益率: {result.total_return:+.2%}")
        print(f" 夏普比: {result.sharpe_ratio:.2f}")
        print(f" 最大回撤: {result.max_drawdown:.2%}")
        print(f" 交易次数: {result.total_trades}")

    # 汇总
    print("\n" + "="*80)
    print(" 回测汇总")
    print("="*80)

    summary = engine.get_summary(results)
    print(summary)

    return results


def optimize_parameters(
    code: str = 'IF0',
    timeframe: str = '1m',
    n_periods_range: range = range(5, 21, 5),
    k1_range: list = [0.3, 0.5, 0.7],
    k2_range: list = [0.3, 0.5, 0.7]
):
    """
    参数优化

    Args:
        code: 合约代码
        timeframe: 时间周期
        n_periods_range: 周期范围
        k1_range: 上轨系数范围
        k2_range: 下轨系数范围
    """
    print("\n" + "="*80)
    print(f" 参数优化: {code}")
    print("="*80)

    provider = WenhuaFuturesProvider()
    df = provider.fetch(code, timeframe)

    if df.is_empty():
        print("无数据，无法优化")
        return None

    engine = BacktestEngine(config=BacktestConfig())

    best_sharpe = -999
    best_params = None
    best_result = None

    results = []

    total_iterations = len(n_periods_range) * len(k1_range) * len(k2_range)
    current = 0

    for n in n_periods_range:
        for k1 in k1_range:
            for k2 in k2_range:
                current += 1
                print(f"\r  进度: {current}/{total_iterations} ({current/total_iterations*100:.1f}%)", end="")

                strategy = DualThrustStrategy(DualThrustConfig(n_periods=n, k1=k1, k2=k2))
                signals_df = strategy.generate_signals_vectorized(df)
                result = engine.run(df, signals_df)

                results.append({
                    'n_periods': n,
                    'k1': k1,
                    'k2': k2,
                    'total_return': result.total_return,
                    'sharpe_ratio': result.sharpe_ratio,
                    'max_drawdown': result.max_drawdown,
                    'total_trades': result.total_trades
                })

                if result.sharpe_ratio > best_sharpe:
                    best_sharpe = result.sharpe_ratio
                    best_params = {'n': n, 'k1': k1, 'k2': k2}
                    best_result = result

    print("\n\n 最优参数:")
    print(f"  周期: {best_params['n']}")
    print(f"  K1: {best_params['k1']}")
    print(f"  K2: {best_params['k2']}")
    print(f"  夏普比率: {best_sharpe:.2f}")
    print(f"  总收益: {best_result.total_return:+.2%}")

    # 保存所有结果
    results_df = pl.DataFrame(results)
    results_df = results_df.sort('sharpe_ratio', descending=True)

    results_file = f"optimize_results_{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    results_df.write_csv(results_file)
    print(f"\n 优化结果已保存: {results_file}")

    return best_params, best_result


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='文华三立期货回测')
    parser.add_argument('--code', type=str, default='IF0', help='合约代码')
    parser.add_argument('--timeframe', type=str, default='1m', help='时间周期')
    parser.add_argument('--capital', type=float, default=1_000_000, help='初始资金')
    parser.add_argument('--n', type=int, default=10, help='DualThrust周期')
    parser.add_argument('--k1', type=float, default=0.5, help='上轨系数')
    parser.add_argument('--k2', type=float, default=0.5, help='下轨系数')
    parser.add_argument('--multi', action='store_true', help='多品种回测')
    parser.add_argument('--optimize', action='store_true', help='参数优化')

    args = parser.parse_args()

    if args.optimize:
        # 参数优化
        optimize_parameters(
            code=args.code,
            timeframe=args.timeframe,
            n_periods_range=range(5, 21, 5),
            k1_range=[0.3, 0.5, 0.7],
            k2_range=[0.3, 0.5, 0.7]
        )
    elif args.multi:
        # 多品种回测
        run_multi_symbol_backtest(
            symbols=['IF0', 'IC0', 'IH0', 'RB0'],
            timeframe=args.timeframe,
            initial_capital=args.capital
        )
    else:
        # 单品种回测
        run_real_backtest(
            code=args.code,
            timeframe=args.timeframe,
            initial_capital=args.capital,
            n_periods=args.n,
            k1=args.k1,
            k2=args.k2
        )


if __name__ == "__main__":
    # 如果没有命令行参数，运行默认回测
    import sys
    if len(sys.argv) == 1:
        # 默认运行 IF0 回测
        run_real_backtest(
            code='IF0',
            timeframe='1m',
            initial_capital=1_000_000,
            n_periods=10,
            k1=0.5,
            k2=0.5
        )
    else:
        main()
