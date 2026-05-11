#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quant Terminal + 文华数据融合方案

由于文华本地不存储历史K线，本方案采用：
1. 使用 AKShare 获取历史K线数据做回测
2. 可选：实时运行时从文华的内存/网络获取最新数据做补充
"""

import sys
sys.path.insert(0, "C:/Users/Administrator/quant-terminal/backend")

from app.data.futures_feed import get_futures_feed, load_futures_for_backtest
from app.data.store import get_store
from app.strategy.futures_strategies import futures_ma_cross_strategy, FuturesBacktester
from app.strategy.backtest import VectorBacktester
import polars as pl
from datetime import datetime, timedelta


class WenhuaQuantBridge:
    """
    文华-Quant 数据桥接器

    将文华的本地数据（合约列表、账户信息）与 AKShare 历史数据结合
    """

    def __init__(self):
        self.futures_feed = get_futures_feed()
        self.store = get_store()

        # 从文华提取的合约列表（如果可用）
        self.wenhua_contracts = self._load_wenhua_contracts()

    def _load_wenhua_contracts(self):
        """尝试从文华导出文件加载合约列表"""
        import json
        from pathlib import Path

        summary_file = Path("./wenhua_export/summary.json")
        if summary_file.exists():
            with open(summary_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def get_data_for_backtest(
        self,
        code: str,
        period: str = "daily",
        start_date: str = None,
        end_date: str = None,
        use_akshare: bool = True
    ) -> pl.DataFrame:
        """
        获取回测数据

        优先从本地数据库加载，如果没有则从 AKShare 下载
        """
        # 1. 尝试从本地加载
        data = load_futures_for_backtest(code, period, start_date, end_date)

        if not data.is_empty():
            print(f"[*] 从本地数据库加载了 {len(data)} 条数据")
            return data

        # 2. 如果没有，从 AKShare 下载
        if use_akshare:
            print(f"[*] 本地无数据，从 AKShare 下载 {code}...")
            from app.data.futures_feed import fetch_and_save_futures_data

            fetch_and_save_futures_data(
                code=code,
                period=period,
                start_date=start_date.replace("-", "") if start_date else None,
                end_date=end_date.replace("-", "") if end_date else None,
            )

            # 重新加载
            data = load_futures_for_backtest(code, period, start_date, end_date)

        return data

    def run_backtest_with_wenhua_info(
        self,
        code: str = "IF0",
        strategy: str = "ma_cross",
        period: str = "daily",
        days: int = 365,
    ):
        """
        运行回测，并显示文华相关的账户信息（如果有）
        """
        print("=" * 70)
        print("Quant Terminal + 文华数据融合回测")
        print("=" * 70)

        # 显示文华账户信息（如果有）
        if self.wenhua_contracts:
            print("\n[文华账户信息]")
            print(f"  数据目录: {self.wenhua_contracts.get('user_data_dir', 'N/A')}")
            print(f"  提取时间: {self.wenhua_contracts.get('extracted_at', 'N/A')}")

        # 计算日期范围
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # 获取数据
        data = self.get_data_for_backtest(
            code=code,
            period=period,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
        )

        if data.is_empty():
            print("[-] 无法获取数据")
            return

        print(f"\n[数据概览]")
        print(f"  合约: {code}")
        print(f"  周期: {period}")
        print(f"  条数: {len(data)}")
        print(f"  范围: {data[0, 'date'] if 'date' in data.columns else data[0, 'datetime']} ~ "
              f"{data[-1, 'date'] if 'date' in data.columns else data[-1, 'datetime']}")

        # 生成信号
        if strategy == "ma_cross":
            signals = futures_ma_cross_strategy(data)
        else:
            print(f"[-] 未知策略: {strategy}")
            return

        # 运行回测
        print(f"\n[回测配置]")
        print(f"  策略: {strategy}")
        print(f"  初始资金: 1,000,000")
        print(f"  保证金比例: 12%")

        backtester = FuturesBacktester(
            initial_capital=1_000_000,
            margin_ratio=0.12,
        )

        result = backtester.run(data, signals)

        # 显示结果
        print("\n" + "=" * 70)
        print("回测结果")
        print("=" * 70)
        print(f"  总收益率:     {result['total_return']:+.2%}")
        print(f"  年化收益率:   {result['annual_return']:+.2%}")
        print(f"  最大回撤:     {result['max_drawdown']:.2%}")
        print(f"  夏普比率:     {result['sharpe']:.2f}")
        print(f"  交易次数:     {result['total_trades']}")
        print(f"  最终资金:     {result['final_capital']:,.2f}")
        print("=" * 70)

        return result


def quick_backtest():
    """快速回测示例"""
    bridge = WenhuaQuantBridge()

    # 运行回测
    result = bridge.run_backtest_with_wenhua_info(
        code="IF0",        # 沪深300主力
        strategy="ma_cross",
        period="daily",    # 日线
        days=365,          # 最近一年
    )

    return result


if __name__ == "__main__":
    quick_backtest()
