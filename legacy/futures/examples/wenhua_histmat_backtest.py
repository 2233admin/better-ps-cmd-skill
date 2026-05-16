#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文华三立期货 + hist-mat 引擎回测

将 hist-mat (Historical Materialism Engine) 的跃迁风险 P_risk
映射为期货交易信号，接入文华三立实盘数据进行模拟回测。

策略逻辑 (只做多 —— 当前 WenhuaExecutor 对空仓支持不完整):
  - P_risk < 0.30  → 市场稳定，开多仓
  - P_risk > 0.55  → 市场矛盾累积，平多仓观望
  - 0.30~0.55      → 持仓不动

微观映射 (把期货 OHLCV 映射到 hist-mat 的 v/s/c):
  - v_raw (可变资本/散户投入)  = 成交量相对比 (vol_ratio)
  - s_raw (剩余价值/机构收割)  = |收益率| × vol_ratio
  - c_raw (不变资本/泡沫压制)  = 价格偏离度 × 10

用法:
    cd quant-terminal/backend
    python ../examples/wenhua_histmat_backtest.py
"""

import sys
sys.path.insert(0, r'C:\Users\Administrator\quant-terminal\backend\src')
sys.path.insert(0, r'C:\Users\Administrator\hist-mat')

from datetime import datetime
from pathlib import Path
import numpy as np
import polars as pl
from loguru import logger

from quant_terminal.data import WenhuaFuturesProvider
from quant_terminal.trade import WenhuaExecutor, Order, OrderSide, OrderType, OrderStatus

from core.engine import HistMatEngine
from core.types import DomainConfig, ReductionResult

# 关闭 hist-mat 引擎的密集日志
logger.disable("core.engine")


# 为期货市场定制的 DomainConfig
# 高敏感度：期货市场情绪波动快，需要较低的 sigmoid 阈值
DOMAIN_FUTURES = DomainConfig(
    name="futures_market",
    sigmoid_k=3.0,
    sigmoid_x0=0.55,
    agency_weights={"sss": 0.30, "gap_rs": 0.45, "censorship": 0.25},
    surplus_rate_range=(0.0, 5.0),
    v_label="散户资金投入",
    s_label="机构套利收割",
    c_label="价格泡沫偏离",
    has_censorship=True,
    min_texts=0,
)


def compute_micro_reduction(window: pl.DataFrame) -> tuple[ReductionResult, float, float, float]:
    """从期货 K 线窗口构造 ReductionResult 和 v/s/c

    核心思路：把 hist-mat 的宏观社会变量映射到期货微观波动特征上。
    需要放大 scale，让引擎的 log1p/sigmoid 能看到有效差异。
    """
    if window.height < 5:
        return (
            ReductionResult(
                gap_rs=0.0, sss_reduced=0.5, censorship_pressure=0.0,
                sentiment_divergence=0.0, entropy=0.5, ideology_penetration=0.3,
            ),
            1.0, 0.0, 0.0,
        )

    closes = window["close"].to_numpy()
    volumes = window["volume"].to_numpy()

    # 窗口收益率
    win_return = (closes[-1] - closes[0]) / max(closes[0], 1e-10)

    # 成交量相对比
    vol_mean = float(np.mean(volumes))
    vol_ratio = float(volumes[-1]) / max(vol_mean, 1.0)
    vol_ratio = min(vol_ratio, 5.0)

    # 窗口内价格波动范围
    price_range = (np.max(closes) - np.min(closes)) / max(np.min(closes), 1e-10)

    # ---- 映射到 hist-mat 变量（放大 scale）----
    # 散户活跃 = 成交量偏离
    v_raw = vol_ratio * 5.0

    # 机构收割 = 波动范围 × 杠杆放大（波动越大套利空间越大）
    s_raw = price_range * 500.0

    # 泡沫压制 = 下跌导致的资本压舱石 + 价格波动
    c_raw = max(0.0, -win_return) * 800.0 + price_range * 100.0

    # ---- 构造 ReductionResult ----
    # 叙事裂缝 = 价格波动 + 趋势背离
    gap_rs = float(np.clip(price_range * 3.0 + abs(win_return) * 2.0, 0, 1))

    # 主体意义感 = 与涨跌挂钩：大涨时散户信心高，大跌时信心崩塌
    sss_reduced = float(np.clip(0.5 + win_return * 8.0, 0.05, 1.0))

    # 审查压力 = 下跌趋势压力（期货中的"强平/监管"隐喻）
    censorship = float(np.clip(max(0.0, -win_return) / 0.02, 0, 1))

    # 情感温差 = 当根收益率
    current_return = 0.0
    if len(closes) >= 2:
        current_return = (closes[-1] - closes[-2]) / max(closes[-2], 1e-10)
    sentiment_div = float(np.clip(abs(current_return) * 20.0, 0, 1))

    # 熵 = 市场混乱度（价格波动）
    entropy = float(np.clip(price_range * 5.0 + vol_ratio / 3.0, 0.05, 1.0))

    reduction = ReductionResult(
        gap_rs=gap_rs,
        sss_reduced=sss_reduced,
        censorship_pressure=censorship,
        sentiment_divergence=sentiment_div,
        entropy=entropy,
        ideology_penetration=0.3,
    )

    return reduction, v_raw, s_raw, c_raw


def run_histmat_backtest(
    code: str = "IF0",
    timeframe: str = "1m",
    lookback: int = 120,
    initial_capital: float = 500_000,
    volume: int = 1,
):
    print("=" * 80)
    print(" 文华三立期货 + hist-mat 回测")
    print("=" * 80)
    print(f"\n合约: {code} | 周期: {timeframe} | 初始资金: {initial_capital:,.0f}")

    # 1. 获取数据
    provider = WenhuaFuturesProvider()
    print("\n[1] 获取实盘数据...")
    df = provider.fetch(code, timeframe)
    if df.is_empty():
        print("[X] 未获取到数据，请确认文华软件已启动或切换到模拟数据")
        return

    print(f"    数据条数: {df.height}")
    print(f"    时间范围: {df['datetime'].min()} ~ {df['datetime'].max()}")

    # 2. 初始化引擎和执行器
    engine = HistMatEngine(domain=DOMAIN_FUTURES)
    executor = WenhuaExecutor(initial_capital=initial_capital, mode="paper")

    p_risk_series = []
    signal_series = []
    equity_series = []

    print(f"\n[2] 回测运行中 (窗口={lookback})...")

    for i in range(lookback, df.height):
        window = df.slice(i - lookback, lookback)
        current_bar = df.row(i, named=True)
        price = float(current_bar["close"])
        dt = current_bar["datetime"]

        # 更新价格
        executor.update_price(code, price)

        # 计算 hist-mat 指标
        reduction, v_raw, s_raw, c_raw = compute_micro_reduction(window)
        output = engine.run(v_raw=v_raw, s_raw=s_raw, c_raw=c_raw, reduction=reduction)
        p_risk = output.p_risk
        p_risk_series.append(p_risk)

        # 生成信号并交易 (只做多)
        pos = executor.get_position(code)
        has_long = pos is not None and pos.volume > 0

        signal = 0  # 0=观望, 1=买入, -1=卖出
        if p_risk < 0.45 and not has_long:
            signal = 1
            order = Order(
                id="", code=code, side=OrderSide.BUY,
                type=OrderType.MARKET, volume=volume, price=price,
            )
            executor.submit_order(order)
        elif p_risk > 0.65 and has_long:
            signal = -1
            order = Order(
                id="", code=code, side=OrderSide.SELL,
                type=OrderType.MARKET, volume=pos.volume, price=price,
            )
            executor.submit_order(order)

        signal_series.append(signal)

        # 记录权益
        account = executor.get_account()
        equity_series.append(account["total_value"])

    # 3. 绩效输出
    print("\n[3] 回测结果")
    print("-" * 80)

    final_account = executor.get_account()
    total_return = final_account["total_return"]
    trades = executor.trades

    # 交易统计
    buy_count = sum(1 for t in trades if t["side"] == "BUY")
    sell_count = sum(1 for t in trades if t["side"] == "SELL")
    total_commission = sum(t["commission"] for t in trades)

    # 简单计算最大回撤
    equity_arr = np.array(equity_series)
    running_max = np.maximum.accumulate(equity_arr)
    drawdowns = (running_max - equity_arr) / running_max
    max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # Sharpe (简化版，假设无风险利率为0)
    returns = np.diff(equity_arr) / np.maximum(equity_arr[:-1], 1e-10)
    sharpe = float(np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252 * 240)) if len(returns) > 0 else 0.0

    print(f"  总收益率:        {total_return:+.2%}")
    print(f"  最大回撤:        {max_drawdown:+.2%}")
    print(f"  年化Sharpe(简化): {sharpe:.3f}")
    print(f"  交易次数:        买入 {buy_count} / 卖出 {sell_count}")
    print(f"  总手续费:        {total_commission:,.2f}")
    print(f"  最终资产:        {final_account['total_value']:,.2f}")

    # 一些 hist-mat 统计
    p_arr = np.array(p_risk_series)
    print(f"\n[4] P_risk 统计")
    print("-" * 80)
    print(f"  P_risk 均值: {np.mean(p_arr):.3f}")
    print(f"  P_risk 最大: {np.max(p_arr):.3f}")
    print(f"  P_risk 最小: {np.min(p_arr):.3f}")
    print(f"  危机信号(P_risk>0.55)次数: {np.sum(p_arr > 0.55)}")
    print(f"  稳定信号(P_risk<0.30)次数: {np.sum(p_arr < 0.30)}")

    # 保存结果
    out_dir = Path(r"C:\Users\Administrator\quant-terminal\examples\results")
    out_dir.mkdir(exist_ok=True)
    result_file = out_dir / f"histmat_backtest_{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    import json
    result_file.write_text(
        json.dumps(
            {
                "code": code,
                "timeframe": timeframe,
                "lookback": lookback,
                "initial_capital": initial_capital,
                "total_return": total_return,
                "max_drawdown": max_drawdown,
                "sharpe": sharpe,
                "trades_count": len(trades),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "commission": total_commission,
                "final_value": final_account["total_value"],
                "p_risk_mean": float(np.mean(p_arr)),
                "p_risk_max": float(np.max(p_arr)),
                "p_risk_min": float(np.min(p_arr)),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n结果已保存: {result_file}")


if __name__ == "__main__":
    run_histmat_backtest(code="IF0", timeframe="1d", lookback=60, initial_capital=500_000, volume=1)
