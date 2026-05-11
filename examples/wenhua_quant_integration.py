#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文华三立期货 + Quant Terminal 整合示例

演示如何将wenhuasanli项目与quant-terminal整合使用

功能:
1. 使用文华数据提供者获取行情数据
2. 使用文华交易执行器进行模拟/实盘交易
3. 运行策略回测
4. 实盘信号监控

Example:
    python wenhua_quant_integration.py
"""

import sys
sys.path.insert(0, r'C:\Users\Administrator\quant-terminal\backend\src')

from datetime import datetime
import polars as pl

from quant_terminal.data import WenhuaFuturesProvider, load_wenhua_for_backtest
from quant_terminal.trade import WenhuaExecutor, Order, OrderType, OrderSide
from quant_terminal.strategies import DualThrustStrategy, DualThrustConfig


def demo_wenhua_data_fetch():
    """演示: 获取文华期货数据"""
    print("\n" + "="*70)
    print("文华三立期货 + Quant Terminal 整合演示")
    print("="*70)
    print("\n1. 数据获取演示")
    print("-"*70)

    # 创建数据提供者
    provider = WenhuaFuturesProvider()

    # 列出支持的合约
    print("\n[1.1] 支持的合约列表:")
    contracts = provider.list_contracts()
    print(f"      共 {len(contracts)} 个合约")

    # 按交易所分类
    exchanges = {}
    for c in contracts:
        exchange = provider._get_exchange(c)
        exchanges.setdefault(exchange, []).append(c)

    for exchange, codes in exchanges.items():
        print(f"      {exchange}: {codes[:5]}...")

    # 获取合约信息
    print("\n[1.2] 合约详细信息:")
    for code in ['IF0', 'IC0', 'RB0', 'SC0']:
        info = provider.get_contract_info(code)
        print(f"      {code}:")
        print(f"        交易所: {info['exchange']}")
        print(f"        合约乘数: {info['multiplier']}")
        print(f"        保证金率: {info['margin_rate']:.0%}")

    # 获取数据
    print("\n[1.3] 获取历史数据:")
    for code in ['IF0', 'RB0']:
        df = provider.fetch(code, '1m')
        print(f"      {code}: {len(df)} 条1分钟数据")
        if not df.is_empty():
            print(f"        时间范围: {df['datetime'].min()} ~ {df['datetime'].max()}")
            print(f"        价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")

    return provider


def demo_wenhua_paper_trading():
    """演示: 文华期货模拟盘交易"""
    print("\n" + "="*70)
    print("2. 模拟盘交易演示")
    print("-"*70)

    # 创建模拟盘 (初始资金50万)
    executor = WenhuaExecutor(
        initial_capital=500000,
        commission_open=0.0001,
        commission_close=0.0001,
        slippage_ticks=1,
        mode="paper"
    )

    print("\n[2.1] 账户初始化:")
    account = executor.get_account()
    print(f"      初始资金: CNY {account['total_value']:,.2f}")
    print(f"      可用资金: CNY {account['cash']:,.2f}")

    # 更新行情价格
    print("\n[2.2] 模拟行情推送:")
    prices = [
        ('IF0', 4000.0),
        ('IC0', 6000.0),
        ('RB0', 3500.0),
    ]

    for code, price in prices:
        executor.update_price(code, price)
        print(f"      {code}: {price:.2f}")

    # 模拟交易
    print("\n[2.3] 执行交易:")

    # 买入股指期货
    order1 = Order(
        id="", code='IF0', side=OrderSide.BUY,
        type=OrderType.MARKET, volume=1, price=4000.0
    )
    executor.submit_order(order1)

    # 买入商品期货
    order2 = Order(
        id="", code='RB0', side=OrderSide.BUY,
        type=OrderType.MARKET, volume=2, price=3500.0
    )
    executor.submit_order(order2)

    # 查看持仓
    print("\n[2.4] 查看持仓:")
    positions = executor.get_all_positions()
    for code, pos in positions.items():
        print(f"      {code}:")
        print(f"        数量: {pos.volume}手")
        print(f"        均价: {pos.avg_price:.2f}")
        print(f"        占用保证金: CNY {pos.margin_used:,.2f}")
        print(f"        合约乘数: {pos.contract_multiplier}")
        print(f"        交易所: {pos.exchange}")

    # 查看账户
    print("\n[2.5] 查看账户:")
    account = executor.get_account()
    print(f"      总资产: CNY {account['total_value']:,.2f}")
    print(f"      现金: CNY {account['cash']:,.2f}")
    print(f"      占用保证金: CNY {account['frozen_margin']:,.2f}")
    print(f"      持仓市值: CNY {account['position_value']:,.2f}")
    print(f"      浮动盈亏: CNY {account['unrealized_pnl']:,.2f}")
    print(f"      收益率: {account['total_return']:+.2%}")

    # 模拟价格变动
    print("\n[2.6] 价格变动后:")
    executor.update_price('IF0', 4050.0)  # 上涨50点
    executor.update_price('RB0', 3450.0)  # 下跌50点

    account = executor.get_account()
    print(f"      总资产: CNY {account['total_value']:,.2f}")
    print(f"      浮动盈亏: CNY {account['unrealized_pnl']:,.2f}")

    # 平仓
    print("\n[2.7] 平仓操作:")
    order3 = Order(
        id="", code='IF0', side=OrderSide.SELL,
        type=OrderType.MARKET, volume=1, price=4050.0
    )
    executor.submit_order(order3)

    # 交易报告
    print("\n[2.8] 交易报告:")
    report = executor.get_trades_report()
    print(f"      成交笔数: {report['trades_count']}")
    print(f"      总手续费: CNY {report['total_commission']:.2f}")

    # 最终账户状态
    print("\n[2.9] 最终账户状态:")
    account = executor.get_account()
    print(f"      总资产: CNY {account['total_value']:,.2f}")
    print(f"      总收益: CNY {account['total_value'] - 500000:,.2f}")
    print(f"      收益率: {account['total_return']:+.2%}")

    return executor


def demo_wenhua_strategy_backtest():
    """演示: 文华期货 + 策略回测"""
    print("\n" + "="*70)
    print("3. 策略回测演示")
    print("-"*70)

    from quant_terminal.data import WenhuaFuturesProvider

    # 获取数据
    provider = WenhuaFuturesProvider()
    df = provider.fetch('IF0', '1m')

    print(f"\n[3.1] 获取数据: {len(df)} 条")

    # 策略生成信号
    strategy = DualThrustStrategy(DualThrustConfig(n_periods=10, k1=0.5, k2=0.5))
    signals_df = strategy.generate_signals_vectorized(df)

    # 统计信号
    buy_signals = signals_df.filter(pl.col('signal') == 1).shape[0]
    sell_signals = signals_df.filter(pl.col('signal') == -1).shape[0]
    print(f"\n[3.2] 策略信号统计:")
    print(f"      买入信号: {buy_signals}")
    print(f"      卖出信号: {sell_signals}")

    # 模拟盘执行信号
    print("\n[3.3] 模拟盘执行:")
    executor = WenhuaExecutor(initial_capital=1000000)

    # 获取合约乘数
    info = provider.get_contract_info('IF0')
    multiplier = info['multiplier']
    print(f"      IF0 合约乘数: {multiplier}")

    # 逐条执行信号
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
    print("\n[3.4] 交易结果:")
    account = executor.get_account()
    report = executor.get_trades_report()

    print(f"      成交笔数: {report['trades_count']}")
    print(f"      最终资产: CNY {account['total_value']:,.2f}")
    print(f"      总收益: {account['total_return']:+.2%}")

    return executor


def demo_wenhua_account_sync():
    """演示: 从文华软件同步账户信息"""
    print("\n" + "="*70)
    print("4. 账户信息同步演示")
    print("-"*70)

    # 创建执行器
    executor = WenhuaExecutor(mode="paper")

    # 尝试同步文华账户
    print("\n[4.1] 尝试同步文华账户:")
    result = executor.sync_from_wenhua()

    if result:
        print("      同步成功!")
    else:
        print("      同步失败 (文华软件可能未运行或API未加载)")
        print("      提示: 请确保文华三立期货软件已安装并运行")

    # 获取账户信息
    print("\n[4.2] 当前账户状态:")
    account = executor.get_account()
    print(f"      总资产: CNY {account['total_value']:,.2f}")
    print(f"      持仓数: {account['position_count']}")


def demo_import_wenhua_data():
    """演示: 从文华导出文件导入数据"""
    print("\n" + "="*70)
    print("5. 数据导入演示")
    print("-"*70)

    provider = WenhuaFuturesProvider()

    print(r"""
    从文华三立期货软件导出数据步骤:

    1. 打开文华三立期货软件 (mytrader_spqh.exe)
    2. 选择要导出的合约 (如: IF0, RB0)
    3. 点击右键 -> 导出数据 -> CSV格式
    4. 保存到本地文件夹

    代码导入示例:

    from quant_terminal.data import WenhuaFuturesProvider

    provider = WenhuaFuturesProvider()

    # 导入CSV文件
    provider.import_from_wenhua(
        csv_path=r'C:\Users\Administrator\Documents\IF0_1m.csv',
        code='IF0',
        timeframe='1m'
    )

    # 导入后即可使用
    df = provider.fetch('IF0', '1m')
    """)


def demo_wenhua_vs_sanli():
    """演示: 文华 vs 三立 数据对比"""
    print("\n" + "="*70)
    print("6. 文华 vs 三立 数据对比")
    print("-"*70)

    from quant_terminal.data import SanliFuturesProvider, WenhuaFuturesProvider

    # 创建两个提供者
    sanli = SanliFuturesProvider()
    wenhua = WenhuaFuturesProvider()

    print("\n[6.1] 合约列表对比:")
    print(f"      三立期货: {len(sanli.list_contracts())} 个合约")
    print(f"      文华期货: {len(wenhua.list_contracts())} 个合约")

    print("\n[6.2] 数据获取对比 (IF0):")

    # 三立数据
    df_sanli = sanli.fetch('IF0', '1m')
    print(f"      三立数据: {len(df_sanli)} 条")

    # 文华数据
    df_wenhua = wenhua.fetch('IF0', '1m')
    print(f"      文华数据: {len(df_wenhua)} 条")

    print("\n[6.3] 合约信息对比:")
    info_sanli = sanli.get_contract_info('IF0')
    info_wenhua = wenhua.get_contract_info('IF0')

    print(f"      三立 - 乘数: {info_sanli['multiplier']}, 保证金: {info_sanli['margin_rate']:.0%}")
    print(f"      文华 - 乘数: {info_wenhua['multiplier']}, 保证金: {info_wenhua['margin_rate']:.0%}")


def main():
    """主函数"""
    print("\n" + "="*70)
    print("文华三立期货 + Quant Terminal 整合演示")
    print("="*70)

    # 运行演示
    try:
        demo_wenhua_data_fetch()
    except Exception as e:
        print(f"\n数据获取演示失败: {e}")

    try:
        demo_wenhua_paper_trading()
    except Exception as e:
        print(f"\n模拟盘交易演示失败: {e}")

    try:
        demo_wenhua_strategy_backtest()
    except Exception as e:
        print(f"\n策略回测演示失败: {e}")

    try:
        demo_wenhua_account_sync()
    except Exception as e:
        print(f"\n账户同步演示失败: {e}")

    try:
        demo_import_wenhua_data()
    except Exception as e:
        print(f"\n数据导入演示失败: {e}")

    try:
        demo_wenhua_vs_sanli()
    except Exception as e:
        print(f"\n数据对比演示失败: {e}")

    print("\n" + "="*70)
    print("文华三立期货 + Quant Terminal 整合演示完成!")
    print("="*70)


if __name__ == "__main__":
    main()
