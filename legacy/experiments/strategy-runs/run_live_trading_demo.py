#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal - 实盘交易演示

演示如何使用生产级代码进行实盘/模拟盘交易
"""

import sys
import time
import random
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend/src')

from datetime import datetime
from quant_terminal.trade import LiveEngine, PaperTradingExecutor, LiveConfig
from quant_terminal.strategies import DualThrustStrategy, DualThrustConfig


def demo_paper_trading():
    """演示: 模拟盘交易"""
    print("\n" + "="*70)
    print("实盘演示 1: 模拟盘交易 (Paper Trading)")
    print("="*70)

    # 创建模拟盘执行器
    executor = PaperTradingExecutor(
        initial_capital=1_000_000,  # 初始资金100万
        commission=0.0001,          # 手续费万分之一
        slippage=0.0002             # 滑点万分之二
    )

    # 创建策略
    strategy = DualThrustStrategy(
        DualThrustConfig(n_periods=5, k1=0.6, k2=0.6)
    )

    # 配置实盘引擎
    config = LiveConfig(
        mode="paper",
        interval=1.0,              # 1秒轮询一次
        auto_trading=True,         # 自动交易
        max_positions=5,           # 最大5个持仓
        risk_per_trade=0.02        # 单笔2%风险
    )

    # 创建实盘引擎
    engine = LiveEngine(executor, strategy, config)

    # 设置回调
    def on_signal(signal):
        print(f"[回调] 信号: {signal.signal_type.name} @ {signal.price:.2f}")

    def on_order(order):
        print(f"[回调] 订单提交: {order.code} {order.side.name} {order.volume}")

    def on_fill(order):
        print(f"[回调] 订单成交: {order.code} {order.filled_volume}@{order.avg_price:.2f} "
              f"手续费:{order.commission:.2f}")

    engine.on_signal = on_signal
    engine.on_order = on_order
    engine.on_fill = on_fill

    # 启动引擎
    print("\n[1] 启动模拟盘引擎...")
    engine.run()

    # 模拟收到行情并产生信号
    print("\n[2] 模拟行情推送...")

    prices = [
        ('IF0', 4000.0), ('IF0', 4010.0), ('IF0', 4020.0),
        ('IC0', 6000.0), ('IC0', 6010.0),
        ('IH0', 2800.0), ('IH0', 2810.0),
    ]

    for code, price in prices:
        bar = {
            'open': price - 5,
            'high': price + 5,
            'low': price - 10,
            'close': price,
            'volume': random.randint(10000, 100000)
        }

        print(f"  -> 推送 {code} 行情: 收{price}")
        engine.on_bar(code, bar)
        time.sleep(0.5)  # 模拟时间间隔

    # 查看账户状态
    print("\n[3] 查看账户状态...")
    time.sleep(1)

    account = executor.get_account()
    positions = executor.get_all_positions()

    print(f"  初始资金: CNY 1,000,000.00")
    print(f"  当前现金: CNY {account['cash']:,.2f}")
    print(f"  持仓市值: CNY {account['position_value']:,.2f}")
    print(f"  总资产:   CNY {account['total_value']:,.2f}")
    print(f"  持仓数量: {len(positions)}")

    for code, pos in positions.items():
        print(f"    - {code}: {pos.volume}手 @ 均价{pos.avg_price:.2f}")

    # 查看引擎状态
    print("\n[4] 查看引擎状态...")
    status = engine.get_status()
    print(f"  运行中: {status['is_running']}")
    print(f"  模式: {status['mode']}")
    print(f"  策略: {status['strategy']}")
    print(f"  当日成交: {status['daily_trades']}")
    print(f"  今日信号: {status['signals_today']}")

    # 手动下单演示
    print("\n[5] 手动下单演示...")
    engine.manual_order('IF0', 'buy', 1, 4000.0)
    time.sleep(0.5)

    # 停止引擎
    print("\n[6] 停止引擎...")
    engine.stop()

    print("\n模拟盘演示完成!")


def demo_okx_trading():
    """演示: OKX加密货币实盘 (需要API Key)"""
    print("\n" + "="*70)
    print("实盘演示 2: OKX加密货币交易")
    print("="*70)

    print("""
    OKX实盘交易需要配置API Key:

    from quant_terminal.trade import OKXExecutor

    executor = OKXExecutor(
        api_key='your_api_key',
        api_secret='your_api_secret',
        passphrase='your_passphrase',
        testnet=True  # 使用测试网
    )

    engine = LiveEngine(executor, strategy)
    engine.run()

    注意:
    1. 先使用 testnet=True 测试
    2. 确认无误后再切换到实盘
    3. 建议先用小资金测试
    """)


def demo_qmt_trading():
    """演示: QMT量化交易 (A股)"""
    print("\n" + "="*70)
    print("实盘演示 3: QMT量化交易 (A股)")
    print("="*70)

    print("""
    QMT (迅投) 交易需要安装MiniQMT:

    from quant_terminal.trade import QMTExecutor

    executor = QMTExecutor(
        mini_qmt_path='D:/QMT/MiniQMT',
        account_id='your_account_id'
    )

    engine = LiveEngine(executor, strategy)
    engine.run()

    注意:
    1. 需要先安装迅投QMT客户端
    2. 保持客户端登录状态
    3. 只支持Windows系统
    """)


def demo_ths_trading():
    """演示: 同花顺交易 (A股)"""
    print("\n" + "="*70)
    print("实盘演示 4: 同花顺交易 (A股)")
    print("="*70)

    print("""
    同花顺交易需要保持客户端登录:

    from quant_terminal.trade import ThsTraderExecutor

    executor = ThsTraderExecutor(account_id='your_account')

    engine = LiveEngine(executor, strategy)
    engine.run()

    注意:
    1. 需要先打开同花顺交易客户端
    2. 保持客户端处于登录状态
    3. 使用 easytrader 库实现
    """)


def show_trading_architecture():
    """展示实盘交易架构"""
    print("\n" + "="*70)
    print("实盘交易架构")
    print("="*70)

    print("""
    生产级实盘系统架构:

    ┌─────────────────────────────────────────────────────────────┐
    │                     实盘交易引擎 (LiveEngine)                │
    ├─────────────────────────────────────────────────────────────┤
    │  功能模块:                                                   │
    │  1. 实时行情订阅 → on_bar/on_tick 回调                      │
    │  2. 策略信号生成 → strategy.generate_signals               │
    │  3. 风控检查     → 日亏损限制、持仓限制                     │
    │  4. 订单管理     → 下单、撤单、状态跟踪                     │
    │  5. 持仓管理     → 实时同步持仓和资金                       │
    │  6. 实时汇报     → 日志、回调、状态查询                     │
    └─────────────────────────────────────────────────────────────┘
                              ↓
    ┌─────────────────────────────────────────────────────────────┐
    │                    交易执行器 (TradeExecutor)                │
    ├─────────────────────────────────────────────────────────────┤
    │  PaperTradingExecutor  → 模拟盘 (零风险测试)                │
    │  OKXExecutor           → OKX交易所 (加密货币)               │
    │  QMTExecutor           → 迅投QMT (A股)                      │
    │  ThsTraderExecutor     → 同花顺 (A股)                       │
    └─────────────────────────────────────────────────────────────┘

    使用流程:
    1. 选择执行器 (模拟盘 → OKX测试网 → 实盘)
    2. 配置策略参数
    3. 启动实盘引擎
    4. 监控运行状态
    5. 紧急情况手动干预
    """)


def show_risk_management():
    """展示风控系统"""
    print("\n" + "="*70)
    print("风控系统")
    print("="*70)

    print("""
    内置风控机制:

    1. 日亏损限制 (daily_loss_limit)
       - 默认5%日亏损上限
       - 触发后自动暂停交易60秒

    2. 持仓数量限制 (max_positions)
       - 限制同时持仓品种数
       - 避免过度分散

    3. 单笔风险控制 (risk_per_trade)
       - 单笔交易风险比例
       - 根据账户总资金动态计算

    4. 订单状态监控
       - 实时跟踪订单状态
       - 异常订单自动处理

    5. 紧急情况处理
       - engine.stop() 立即停止
       - 手动平仓功能
       - 一键清仓
    """)


def main():
    """主函数"""
    print("\n" + "="*70)
    print("Quant Terminal - 实盘交易系统")
    print("="*70)

    # 展示架构
    show_trading_architecture()
    show_risk_management()

    # 运行模拟盘演示
    demo_paper_trading()

    # 展示其他交易通道
    demo_okx_trading()
    demo_qmt_trading()
    demo_ths_trading()

    print("\n" + "="*70)
    print("实盘交易演示完成!")
    print("="*70)
    print("""
下一步:
1. 先用模拟盘充分测试策略
2. 小资金实盘验证
3. 逐步增加资金规模

风险提示: 实盘交易有风险，请谨慎操作！
""")


if __name__ == "__main__":
    main()
