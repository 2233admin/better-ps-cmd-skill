#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
三立期货实盘交易演示

演示如何使用三立期货数据进行模拟盘交易
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend/src')

import time
import random
from datetime import datetime

import polars as pl
from quant_terminal.data import SanliFuturesProvider, load_sanli_for_backtest
from quant_terminal.trade.sanli_executor import SanliPaperExecutor
from quant_terminal.strategies import DualThrustStrategy, DualThrustConfig


def demo_sanli_data_fetch():
    """演示: 获取三立期货数据"""
    print("\n" + "="*70)
    print("三立期货演示 1: 数据获取")
    print("="*70)

    provider = SanliFuturesProvider()

    # 列出支持的合约
    print("\n[1] 支持的合约列表:")
    contracts = provider.list_contracts()
    print(f"    共 {len(contracts)} 个合约")
    print(f"    股指期货: {[c for c in contracts if c.startswith('I')]}")
    print(f"    商品期货: {[c for c in contracts if not c.startswith('I')]}")

    # 获取合约信息
    print("\n[2] 合约详细信息:")
    for code in ['IF0', 'IC0', 'RB0', 'SC0']:
        info = provider.get_contract_info(code)
        print(f"    {code} -> {info['code']}")
        print(f"      交易所: {info['exchange']}")
        print(f"      合约乘数: {info['multiplier']}")
        print(f"      保证金率: {info['margin_rate']:.0%}")

    # 获取数据
    print("\n[3] 获取历史数据:")
    for code in ['IF0', 'RB0']:
        df = provider.fetch(code, '1m', start=datetime(2024, 1, 1), end=datetime(2024, 1, 10))
        print(f"    {code}: {len(df)} 条1分钟数据")
        if not df.is_empty():
            print(f"      时间范围: {df['datetime'].min()} ~ {df['datetime'].max()}")
            print(f"      价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")


def demo_sanli_paper_trading():
    """演示: 三立期货模拟盘交易"""
    print("\n" + "="*70)
    print("三立期货演示 2: 模拟盘交易")
    print("="*70)

    # 创建模拟盘 (初始资金50万)
    executor = SanliPaperExecutor(
        initial_capital=500000,
        commission_open=0.0001,
        commission_close=0.0001,
        slippage_ticks=1
    )

    print("\n[1] 账户初始化:")
    account = executor.get_account()
    print(f"    初始资金: CNY {account['total_value']:,.2f}")
    print(f"    可用资金: CNY {account['cash']:,.2f}")

    # 更新行情价格
    print("\n[2] 模拟行情推送:")
    prices = [
        ('IF0', 4000.0),
        ('IC0', 6000.0),
        ('RB0', 3500.0),
    ]

    for code, price in prices:
        executor.update_price(code, price)
        print(f"    {code}: {price:.2f}")

    # 模拟交易
    print("\n[3] 执行交易:")

    from quant_terminal.trade.executor import Order, OrderType, OrderSide

    # 买入股指期货
    order1 = Order(
        id="", code='IF0', side=OrderSide.BUY,
        type=OrderType.MARKET, volume=1, price=4000.0
    )
    executor.submit_order(order1)
    time.sleep(0.1)

    # 买入商品期货
    order2 = Order(
        id="", code='RB0', side=OrderSide.BUY,
        type=OrderType.MARKET, volume=2, price=3500.0
    )
    executor.submit_order(order2)
    time.sleep(0.1)

    # 查看持仓
    print("\n[4] 查看持仓:")
    positions = executor.get_all_positions()
    for code, pos in positions.items():
        print(f"    {code}:")
        print(f"      数量: {pos.volume}手")
        print(f"      均价: {pos.avg_price:.2f}")
        print(f"      占用保证金: CNY {pos.margin_used:,.2f}")
        print(f"      合约乘数: {pos.contract_multiplier}")
        print(f"      交易所: {pos.exchange}")

    # 查看账户
    print("\n[5] 查看账户:")
    account = executor.get_account()
    print(f"    总资产: CNY {account['total_value']:,.2f}")
    print(f"    现金: CNY {account['cash']:,.2f}")
    print(f"    占用保证金: CNY {account['frozen_margin']:,.2f}")
    print(f"    持仓市值: CNY {account['position_value']:,.2f}")
    print(f"    浮动盈亏: CNY {account['unrealized_pnl']:,.2f}")
    print(f"    收益率: {account['total_return']:+.2%}")

    # 模拟价格变动
    print("\n[6] 价格变动后:")
    executor.update_price('IF0', 4050.0)  # 上涨50点
    executor.update_price('RB0', 3450.0)  # 下跌50点

    account = executor.get_account()
    print(f"    总资产: CNY {account['total_value']:,.2f}")
    print(f"    浮动盈亏: CNY {account['unrealized_pnl']:,.2f}")

    # 平仓
    print("\n[7] 平仓操作:")
    order3 = Order(
        id="", code='IF0', side=OrderSide.SELL,
        type=OrderType.MARKET, volume=1, price=4050.0
    )
    executor.submit_order(order3)

    # 交易报告
    print("\n[8] 交易报告:")
    report = executor.get_trades_report()
    print(f"    成交笔数: {report['trades_count']}")
    print(f"    总手续费: CNY {report['total_commission']:.2f}")

    # 最终账户状态
    print("\n[9] 最终账户状态:")
    account = executor.get_account()
    print(f"    总资产: CNY {account['total_value']:,.2f}")
    print(f"    总收益: CNY {account['total_value'] - 500000:,.2f}")
    print(f"    收益率: {account['total_return']:+.2%}")


def demo_sanli_with_strategy():
    """演示: 三立期货 + 策略交易"""
    print("\n" + "="*70)
    print("三立期货演示 3: 策略交易")
    print("="*70)

    from quant_terminal.data import SanliFuturesProvider

    # 获取数据
    provider = SanliFuturesProvider()
    df = provider.fetch('IF0', '1m')

    print(f"\n[1] 获取数据: {len(df)} 条")

    # 策略生成信号
    strategy = DualThrustStrategy(DualThrustConfig(n_periods=10, k1=0.5, k2=0.5))
    signals_df = strategy.generate_signals_vectorized(df)

    # 统计信号
    buy_signals = signals_df.filter(pl.col('signal') == 1).shape[0]
    sell_signals = signals_df.filter(pl.col('signal') == -1).shape[0]
    print(f"\n[2] 策略信号统计:")
    print(f"    买入信号: {buy_signals}")
    print(f"    卖出信号: {sell_signals}")

    # 模拟盘执行信号
    print("\n[3] 模拟盘执行:")
    executor = SanliPaperExecutor(initial_capital=1000000)

    # 获取合约乘数
    info = provider.get_contract_info('IF0')
    multiplier = info['multiplier']
    print(f"    IF0 合约乘数: {multiplier}")

    # 逐条执行信号
    from quant_terminal.trade.executor import Order, OrderType, OrderSide

    for row in signals_df.filter(pl.col('signal') != 0).iter_rows(named=True):
        price = row['close']
        signal = row['signal']

        executor.update_price('IF0', price)

        if signal == 1:  # 买入
            order = Order(
                id="", code='IF0', side=OrderSide.BUY,
                type=OrderType.MARKET, volume=1, price=price
            )
        else:  # 卖出
            order = Order(
                id="", code='IF0', side=OrderSide.SELL,
                type=OrderType.MARKET, volume=1, price=price
            )

        executor.submit_order(order)

    # 结果
    print("\n[4] 交易结果:")
    account = executor.get_account()
    report = executor.get_trades_report()

    print(f"    成交笔数: {report['trades_count']}")
    print(f"    最终资产: CNY {account['total_value']:,.2f}")
    print(f"    总收益: {account['total_return']:+.2%}")


def demo_import_from_boyue():
    """演示: 从博易大师导入数据"""
    print("\n" + "="*70)
    print("三立期货演示 4: 从博易大师导入数据")
    print("="*70)

    print("""
    从三立期货博易大师导出数据步骤:

    1. 打开三立期货博易大师
    2. 选择要导出的合约 (如: IF0, RB0)
    3. 点击右键 -> 数据导出 -> CSV格式
    4. 保存到本地文件夹

    代码导入:

    from quant_terminal.data import SanliFuturesProvider

    provider = SanliFuturesProvider()

    # 导入CSV文件
    provider.import_from_boyue(
        csv_path='~/Downloads/IF0_1m.csv',
        code='IF0',
        timeframe='1m'
    )

    # 导入后即可使用
    df = provider.fetch('IF0', '1m')
    """)


def main():
    """主函数"""
    print("\n" + "="*70)
    print("三立期货 - 数据抓取与模拟盘交易系统")
    print("="*70)

    # 运行演示
    demo_sanli_data_fetch()
    demo_sanli_paper_trading()
    demo_sanli_with_strategy()
    demo_import_from_boyue()

    print("\n" + "="*70)
    print("三立期货演示完成!")
    print("="*70)


if __name__ == "__main__":
    main()
