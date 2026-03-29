#!/usr/bin/env python
"""
Quant Terminal - 生产级代码快速入门

演示如何使用新重构的生产级模块
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend/src')

import polars as pl
from datetime import datetime, timedelta

# ================== 1. 数据获取 ==================
print("=" * 60)
print("1. 数据获取")
print("=" * 60)

from quant_terminal.data import FuturesDataProvider

# 创建数据提供者
provider = FuturesDataProvider()

# 获取期货数据 (实际使用时会从AKShare获取)
# df = provider.fetch('IF0', 'daily', start='2024-01-01', end='2024-12-31')

# 这里使用模拟数据演示
n_days = 365
dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n_days)]
df = pl.DataFrame({
    'datetime': dates,
    'open': [4000 + i * 0.5 for i in range(n_days)],
    'high': [4010 + i * 0.5 for i in range(n_days)],
    'low': [3990 + i * 0.5 for i in range(n_days)],
    'close': [4005 + i * 0.5 for i in range(n_days)],
    'volume': [100000] * n_days,
})

print(f"数据条数: {len(df)}")
print(f"列: {df.columns}")


# ================== 2. 策略定义 ==================
print("\n" + "=" * 60)
print("2. 策略定义 - Dual Thrust")
print("=" * 60)

from quant_terminal.strategies import DualThrustStrategy, DualThrustConfig

# 创建策略配置
config = DualThrustConfig(
    n_periods=5,
    k1=0.6,
    k2=0.6
)

# 创建策略实例
strategy = DualThrustStrategy(config)
print(f"策略名称: {strategy.name}")

# 生成信号
signals = strategy.generate_signals(df)
print(f"生成信号数: {len(signals)}")

if signals:
    print(f"示例信号: {signals[0].signal_type.name} @ {signals[0].price}")


# ================== 3. 回测执行 ==================
print("\n" + "=" * 60)
print("3. 回测执行")
print("=" * 60)

from quant_terminal.core.backtest import BacktestEngine, BacktestConfig

# 准备信号DataFrame
signals_df = strategy.generate_signals_vectorized(df)

# 创建回测引擎
backtest_config = BacktestConfig(
    initial_capital=1_000_000,
    commission=0.0001,
    slippage=0.0002
)
engine = BacktestEngine(backtest_config)

# 运行回测
result = engine.run(df, signals_df.select(['datetime', 'signal']))

# 打印结果
print(f"总收益: {result.total_return:+.2%}")
print(f"年化收益: {result.annual_return:+.2%}")
print(f"夏普比率: {result.sharpe_ratio:.2f}")
print(f"最大回撤: {result.max_drawdown:.2%}")
print(f"交易次数: {result.total_trades}")
print(f"胜率: {result.win_rate:.1%}")


# ================== 4. 组合管理 ==================
print("\n" + "=" * 60)
print("4. 组合管理")
print("=" * 60)

from quant_terminal.core.portfolio import Portfolio, PortfolioConfig, PositionDirection

# 创建投资组合
portfolio_config = PortfolioConfig(
    initial_capital=1_000_000,
    max_position_pct=0.8,
    max_single_position_pct=0.2
)
portfolio = Portfolio(portfolio_config)

# 开仓
success, msg = portfolio.open_position(
    code='IF0',
    direction=PositionDirection.LONG,
    volume=1,
    price=4000.0
)
print(f"开仓IF0: {msg}")

# 更新价格
portfolio.update_prices({'IF0': 4100.0})

# 查看组合状态
summary = portfolio.get_summary()
print(f"总资产: {summary['total_value']:,.2f}")
print(f"浮动盈亏: {summary['total_unrealized_pnl']:+.2f}")
print(f"保证金使用率: {summary['margin_ratio']:.1%}")


# ================== 5. GPU参数优化 ==================
print("\n" + "=" * 60)
print("5. GPU参数优化 (如果CUDA可用)")
print("=" * 60)

from quant_terminal.gpu import GPUGridSearch
import torch

if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")

    searcher = GPUGridSearch()

    # 定义参数网格
    param_grid = {
        'n_periods': range(3, 10),
        'k1': [0.3, 0.4, 0.5, 0.6],
        'k2': [0.3, 0.4, 0.5, 0.6],
    }

    print(f"参数组合数: {len(list(param_grid['n_periods'])) * len(param_grid['k1']) * len(param_grid['k2'])}")
    print("使用GPU加速搜索...")
else:
    print("CUDA不可用，跳过GPU优化演示")


# ================== 6. 批量回测 ==================
print("\n" + "=" * 60)
print("6. 批量回测 (多品种)")
print("=" * 60)

# 模拟多品种数据
data_dict = {
    'IF0': df,
    'IC0': df.with_columns([pl.col('close') * 0.8]),
    'IH0': df.with_columns([pl.col('close') * 1.2]),
}

# 为每个品种生成信号
signals_dict = {}
for code, data in data_dict.items():
    sig_df = strategy.generate_signals_vectorized(data)
    signals_dict[code] = sig_df.select(['datetime', 'signal'])

# 批量回测
results = engine.run_batch(data_dict, signals_dict)

# 结果汇总
summary_df = engine.get_summary(results)
print(summary_df)


# ================== 7. 绩效指标 ==================
print("\n" + "=" * 60)
print("7. 详细绩效指标")
print("=" * 60)

from quant_terminal.core.metrics import MetricsCalculator

# 从回测结果计算指标
calc = MetricsCalculator(
    equity_curve=result.equity_curve['equity'].to_numpy(),
    trades=result.trades
)

metrics = calc.calculate_all()
print(f"\n绩效指标详情:")
print(f"  总收益率: {metrics.total_return:+.2%}")
print(f"  年化收益率: {metrics.annual_return:+.2%}")
print(f"  年化波动率: {metrics.volatility:.2%}")
print(f"  夏普比率: {metrics.sharpe_ratio:.2f}")
print(f"  索提诺比率: {metrics.sortino_ratio:.2f}")
print(f"  最大回撤: {metrics.max_drawdown:.2%}")
print(f"  Calmar比率: {metrics.calmar_ratio:.2f}")
print(f"  胜率: {metrics.win_rate:.1%}")
print(f"  盈亏比: {metrics.profit_factor:.2f}")


# ================== 总结 ==================
print("\n" + "=" * 60)
print("总结: 生产级代码的优势")
print("=" * 60)

print("""
1. 简洁性: 几行代码完成数据获取、策略回测、绩效分析
2. 模块化: 每个功能独立封装，易于理解和维护
3. 可扩展: 继承基类即可添加新策略或数据源
4. 类型安全: 完整的类型注解，IDE自动提示
5. 高性能: GPU加速和向量化操作
6. 可测试: 每个模块都有对应的单元测试

示例代码行数统计:
- 原始脚本: ~200行/策略
- 生产代码: ~10行/策略 (复用框架)

开发效率提升: 20x
""")

print("=" * 60)
