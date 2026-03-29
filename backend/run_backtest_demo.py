#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal - 回测演示

使用生产级代码执行多品种回测
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend/src')

import polars as pl
import numpy as np
from datetime import datetime, timedelta
from loguru import logger

from quant_terminal.core.backtest import BacktestEngine, BacktestConfig
from quant_terminal.strategies import DualThrustStrategy, DualThrustConfig
from quant_terminal.core.portfolio import Portfolio, PortfolioConfig, PositionDirection
from quant_terminal.core.metrics import MetricsCalculator


def generate_mock_data(code: str, n_days: int = 500, trend: float = 0.0, volatility: float = 0.02) -> pl.DataFrame:
    """生成模拟市场数据"""
    np.random.seed(hash(code) % 2**32)

    dates = [datetime(2023, 1, 1) + timedelta(days=i) for i in range(n_days)]

    # 生成价格序列
    returns = np.random.normal(trend/252, volatility/np.sqrt(252), n_days)
    prices = 4000 * np.exp(np.cumsum(returns))

    # 生成OHLC
    opens = prices * (1 + np.random.normal(0, 0.001, n_days))
    highs = np.maximum(opens, prices) * (1 + np.abs(np.random.normal(0, 0.005, n_days)))
    lows = np.minimum(opens, prices) * (1 - np.abs(np.random.normal(0, 0.005, n_days)))
    closes = prices
    volumes = np.random.randint(100000, 1000000, n_days)

    return pl.DataFrame({
        'datetime': dates,
        'code': [code] * n_days,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
    })


def run_single_backtest():
    """单品种回测"""
    print("\n" + "="*70)
    print("回测 1: 单品种回测 (IF0 - 沪深300期货)")
    print("="*70)

    # 生成模拟数据 (带轻微上涨趋势)
    data = generate_mock_data('IF0', n_days=500, trend=0.10, volatility=0.25)
    print(f"数据条数: {len(data)}")
    print(f"时间范围: {data['datetime'].min()} ~ {data['datetime'].max()}")
    print(f"价格范围: {data['close'].min():.2f} ~ {data['close'].max():.2f}")

    # 创建策略
    strategy = DualThrustStrategy(DualThrustConfig(n_periods=5, k1=0.6, k2=0.6))

    # 生成信号
    signals_df = strategy.generate_signals_vectorized(data)
    signal_count = signals_df.filter(pl.col('signal') != 0).shape[0]
    print(f"\n策略信号数: {signal_count}")

    # 执行回测
    config = BacktestConfig(
        initial_capital=1_000_000,
        commission=0.0001,  # 万分之一
        slippage=0.0002,    # 万分之二
    )
    engine = BacktestEngine(config)

    result = engine.run(
        data,
        signals_df.select(['datetime', 'signal']),
        strategy_name='DualThrust'
    )

    # 打印结果
    print("\n" + "-"*70)
    print("回测结果")
    print("-"*70)
    print(f"初始资金: CNY {config.initial_capital:,.2f}")
    print(f"最终权益: CNY {result.equity_curve['equity'].tail(1).item():,.2f}")
    print(f"总收益率: {result.total_return:+.2%}")
    print(f"年化收益: {result.annual_return:+.2%}")
    print(f"夏普比率: {result.sharpe_ratio:.2f}")
    print(f"最大回撤: {result.max_drawdown:.2%}")
    print(f"交易次数: {result.total_trades}")
    print(f"胜率: {result.win_rate:.1%}")
    print(f"盈亏比: {result.profit_factor:.2f}")

    return result


def run_multi_asset_backtest():
    """多资产组合回测"""
    print("\n" + "="*70)
    print("回测 2: 多资产组合回测")
    print("="*70)

    # 生成三种资产的数据
    assets = {
        'IF0': {'trend': 0.08, 'vol': 0.20},   # 沪深300 - 稳健上涨
        'IC0': {'trend': 0.12, 'vol': 0.25},   # 中证500 - 波动更大
        'IH0': {'trend': 0.06, 'vol': 0.18},   # 上证50 - 保守
    }

    data_dict = {}
    signals_dict = {}

    strategy = DualThrustStrategy(DualThrustConfig(n_periods=5, k1=0.5, k2=0.5))

    for code, params in assets.items():
        data = generate_mock_data(code, n_days=500, trend=params['trend'], volatility=params['vol'])
        data_dict[code] = data

        signals_df = strategy.generate_signals_vectorized(data)
        signals_dict[code] = signals_df.select(['datetime', 'signal'])

        print(f"{code}: {len(data)} 条, 趋势={params['trend']:.1%}, 波动={params['vol']:.1%}")

    # 批量回测
    config = BacktestConfig(initial_capital=1_000_000)
    engine = BacktestEngine(config)

    results = engine.run_batch(data_dict, signals_dict, strategy_name='DualThrust_Portfolio')

    # 结果汇总
    print("\n" + "-"*70)
    print("组合回测结果")
    print("-"*70)

    summary = engine.get_summary(results)
    print(summary)

    # 计算组合整体表现
    total_return = sum(r.total_return for r in results.values()) / len(results)
    avg_sharpe = sum(r.sharpe_ratio for r in results.values()) / len(results)
    total_trades = sum(r.total_trades for r in results.values())

    print("\n组合统计:")
    print(f"平均收益率: {total_return:+.2%}")
    print(f"平均夏普比: {avg_sharpe:.2f}")
    print(f"总交易次数: {total_trades}")

    return results


def run_portfolio_backtest():
    """组合管理回测演示"""
    print("\n" + "="*70)
    print("回测 3: 组合管理 + 风险控制")
    print("="*70)

    # 生成数据
    data = generate_mock_data('IF0', n_days=252, trend=0.10, volatility=0.20)

    # 创建投资组合
    portfolio_config = PortfolioConfig(
        initial_capital=1_000_000,
        max_position_pct=0.8,       # 最大80%仓位
        max_single_position_pct=0.3, # 单个品种最大30%
        margin_ratio=0.15,           # 期货保证金15%
    )
    portfolio = Portfolio(portfolio_config)

    print(f"初始资金: CNY {portfolio_config.initial_capital:,.2f}")
    print(f"最大仓位限制: {portfolio_config.max_position_pct:.0%}")
    print(f"单品种限制: {portfolio_config.max_single_position_pct:.0%}")

    # 模拟交易
    trades_executed = 0
    pnl_history = []

    for row in data.iter_rows(named=True):
        price = row['close']
        date = row['datetime']

        # 简单策略: 每20天切换一次仓位
        day_idx = (date - datetime(2023, 1, 1)).days

        position = portfolio.get_position('IF0')

        if day_idx % 40 == 0 and (position is None or position.is_flat):
            # 开多
            success, msg = portfolio.open_position(
                'IF0', PositionDirection.LONG,
                volume=2, price=price, open_time=date
            )
            if success:
                trades_executed += 1

        elif day_idx % 40 == 20 and position and position.is_long:
            # 平多
            success, msg = portfolio.close_position(
                'IF0', price=price, close_time=date
            )
            if success:
                trades_executed += 1
                last_trade = portfolio.history[-1]
                pnl_history.append(last_trade.get('pnl', 0))

        # 更新价格
        portfolio.update_prices({'IF0': price})

    # 清仓
    final_price = data['close'].tail(1).item()
    portfolio.liquidate_all({'IF0': final_price})

    # 结果
    summary = portfolio.get_summary()

    print("\n" + "-"*70)
    print("组合管理结果")
    print("-"*70)
    print(f"交易次数: {trades_executed}")
    print(f"最终资产: CNY {summary['total_value']:,.2f}")
    print(f"总收益率: {summary['total_return']:+.2%}")
    print(f"浮动盈亏: CNY {summary['total_unrealized_pnl']:,.2f}")
    print(f"保证金使用: {summary['margin_ratio']:.1%}")

    if pnl_history:
        wins = [p for p in pnl_history if p > 0]
        losses = [p for p in pnl_history if p < 0]
        print(f"盈利次数: {len(wins)}")
        print(f"亏损次数: {len(losses)}")
        if losses:
            print(f"平均盈利: CNY {sum(wins)/len(wins):,.2f}")
            print(f"平均亏损: CNY {sum(losses)/len(losses):,.2f}")


def run_detailed_metrics():
    """详细绩效指标分析"""
    print("\n" + "="*70)
    print("回测 4: 详细绩效指标")
    print("="*70)

    # 生成数据并回测
    data = generate_mock_data('IC0', n_days=500, trend=0.15, volatility=0.28)
    strategy = DualThrustStrategy(DualThrustConfig(n_periods=7, k1=0.7, k2=0.6))
    signals_df = strategy.generate_signals_vectorized(data)

    engine = BacktestEngine(BacktestConfig())
    result = engine.run(data, signals_df.select(['datetime', 'signal']))

    # 使用MetricsCalculator计算详细指标
    calc = MetricsCalculator(
        equity_curve=result.equity_curve['equity'].to_numpy(),
        trades=result.trades
    )

    metrics = calc.calculate_all()

    print("\n详细绩效指标:")
    print("-"*70)
    print(f"{'总收益率:':<25} {metrics.total_return:>+15.2%}")
    print(f"{'年化收益率:':<25} {metrics.annual_return:>+15.2%}")
    print(f"{'年化波动率:':<25} {metrics.volatility:>15.2%}")
    print(f"{'夏普比率:':<25} {metrics.sharpe_ratio:>15.2f}")
    print(f"{'索提诺比率:':<25} {metrics.sortino_ratio:>15.2f}")
    print(f"{'最大回撤:':<25} {metrics.max_drawdown:>15.2%}")
    print(f"{'Calmar比率:':<25} {metrics.calmar_ratio:>15.2f}")
    print(f"{'胜率:':<25} {metrics.win_rate:>15.1%}")
    print(f"{'盈亏比:':<25} {metrics.profit_factor:>15.2f}")
    print(f"{'平均交易收益:':<25} {metrics.avg_trade:>+15.2%}")
    print(f"{'平均盈利:':<25} {metrics.avg_win:>+15.2%}")
    print(f"{'平均亏损:':<25} {metrics.avg_loss:>+15.2%}")

    # 打印格式化的摘要
    print("\n" + calc.get_summary_text())


def main():
    """主函数"""
    print("\n" + "="*70)
    print("Quant Terminal - 生产级回测系统")
    print("="*70)

    # 运行各个回测
    result1 = run_single_backtest()
    results2 = run_multi_asset_backtest()
    run_portfolio_backtest()
    run_detailed_metrics()

    print("\n" + "="*70)
    print("回测完成!")
    print("="*70)


if __name__ == "__main__":
    main()
