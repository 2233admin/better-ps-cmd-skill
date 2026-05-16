#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""国内期货数据获取和回测示例"""

import sys
sys.path.insert(0, "C:/Users/Administrator/quant-terminal/backend")

from app.data.futures_feed import (
    get_futures_feed,
    get_futures_store,
    fetch_and_save_futures_data,
    load_futures_for_backtest,
)
from app.strategy.backtest import VectorBacktester
from app.data.store import get_store
import polars as pl
import numpy as np
from loguru import logger


def example_1_get_realtime_quote():
    """示例1: 获取期货实时行情"""
    print("=" * 60)
    print("示例1: 获取股指期货实时行情")
    print("=" * 60)

    feed = get_futures_feed()

    # 获取主力合约行情
    codes = ["IF0", "IC0", "IH0", "IM0"]  # 0表示主力合约
    quotes = feed.get_quotes(codes)

    for q in quotes:
        print(f"\n{q['code']} - {q['name']}")
        print(f"  最新价: {q['last_price']}")
        print(f"  买一: {q['bid_price']} x {q['bid_volume']}")
        print(f"  卖一: {q['ask_price']} x {q['ask_volume']}")
        print(f"  成交量: {q['volume']}")
        print(f"  持仓量: {q.get('open_interest', 'N/A')}")


def example_2_fetch_historical_data():
    """示例2: 获取并保存历史数据"""
    print("\n" + "=" * 60)
    print("示例2: 获取股指期货历史数据")
    print("=" * 60)

    # 获取沪深300股指期货日线数据
    fetch_and_save_futures_data(
        code="IF0",           # 主力合约
        period="daily",       # 日线
        start_date="20250101",
        end_date="20250328",
        count=500
    )

    # 获取5分钟数据
    fetch_and_save_futures_data(
        code="IF0",
        period="5min",
        count=1000
    )


def example_3_load_and_backtest():
    """示例3: 加载数据并进行简单回测"""
    print("\n" + "=" * 60)
    print("示例3: 期货策略回测")
    print("=" * 60)

    # 加载数据
    data = load_futures_for_backtest(
        code="IF0",
        period="daily",
        start_date="2024-01-01",
        end_date="2025-03-28"
    )

    if data.is_empty():
        print("请先运行示例2获取数据")
        return

    print(f"加载了 {len(data)} 条数据")
    print(data.head())

    # 生成简单均线策略信号
    data = data.with_columns([
        pl.col("close").rolling_mean(window_size=5).alias("ma5"),
        pl.col("close").rolling_mean(window_size=20).alias("ma20"),
    ])

    # 生成交易信号: 金叉买入, 死叉卖出
    signals = data.with_columns([
        pl.when(
            (pl.col("ma5") > pl.col("ma20")) &
            (pl.col("ma5").shift(1) <= pl.col("ma20").shift(1))
        ).then(1)  # 买入
        .when(
            (pl.col("ma5") < pl.col("ma20")) &
            (pl.col("ma5").shift(1) >= pl.col("ma20").shift(1))
        ).then(-1)  # 卖出
        .otherwise(0)
        .alias("signal")
    ])

    # 运行回测
    backtester = VectorBacktester(
        initial_capital=1_000_000,
        commission=0.0001,  # 期货手续费万1
        slippage=0.0002     # 滑点
    )

    result = backtester.run(data, signals)

    print("\n回测结果:")
    print(f"  总收益率: {result.total_return:.2%}")
    print(f"  年化收益率: {result.annual_return:.2%}")
    print(f"  夏普比率: {result.sharpe_ratio:.2f}")
    print(f"  最大回撤: {result.max_drawdown:.2%}")
    print(f"  胜率: {result.win_rate:.2%}")
    print(f"  总交易次数: {result.total_trades}")


def example_4_direct_api_usage():
    """示例4: 直接使用API获取数据"""
    print("\n" + "=" * 60)
    print("示例4: 直接API调用")
    print("=" * 60)

    feed = get_futures_feed()

    # 获取分钟K线
    data = feed.get_kline(
        code="IF0",
        klt=5,  # 5分钟
        count=100
    )

    if not data.is_empty():
        print(f"\n获取到 {len(data)} 条5分钟K线数据")
        print(data.head(10))

        # 计算技术指标
        data = data.with_columns([
            pl.col("close").rolling_mean(window_size=5).alias("ma5"),
            (pl.col("close") - pl.col("open")).alias("change"),
        ])

        print("\n添加技术指标后:")
        print(data.select(["datetime", "open", "close", "ma5", "change"]).head(10))


def example_5_batch_download():
    """示例5: 批量下载多品种数据"""
    print("\n" + "=" * 60)
    print("示例5: 批量下载多品种数据")
    print("=" * 60)

    # 要下载的品种
    symbols = {
        "IF0": "沪深300",
        "IC0": "中证500",
        "IH0": "上证50",
        "IM0": "中证1000",
        "AU0": "黄金",
        "AG0": "白银",
        "RB0": "螺纹钢",
    }

    for code, name in symbols.items():
        print(f"\n下载 {name}({code})...")
        try:
            fetch_and_save_futures_data(
                code=code,
                period="daily",
                start_date="20240101",
                end_date="20250328"
            )
        except Exception as e:
            print(f"  失败: {e}")


def main():
    """主函数 - 选择要运行的示例"""
    examples = {
        "1": ("获取实时行情", example_1_get_realtime_quote),
        "2": ("获取历史数据", example_2_fetch_historical_data),
        "3": ("策略回测", example_3_load_and_backtest),
        "4": ("直接API调用", example_4_direct_api_usage),
        "5": ("批量下载", example_5_batch_download),
    }

    print("""
╔══════════════════════════════════════════════════════════╗
║          国内期货数据模块 - 使用示例                      ║
╚══════════════════════════════════════════════════════════╝

请选择要运行的示例:

  1. 获取股指期货实时行情
  2. 获取并保存历史数据到DuckDB
  3. 加载数据进行策略回测
  4. 直接API调用获取分钟数据
  5. 批量下载多品种数据

  0. 退出
""")

    choice = input("请输入选项 (0-5): ").strip()

    if choice in examples:
        name, func = examples[choice]
        print(f"\n运行: {name}")
        func()
    elif choice == "0":
        print("退出")
    else:
        print("无效选项")


if __name__ == "__main__":
    main()
