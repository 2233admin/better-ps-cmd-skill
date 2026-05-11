#!/usr/bin/env python3
"""OpenClaw + OKX 实盘交易脚本

用 OpenClaw 本地 LLM 调度 quant-terminal 现有策略：
1. 拉取 OKX K线数据
2. 回测现有策略
3. 选最优策略
4. 启动实盘（paper/live）
5. 定期向 OpenClaw 汇报，接受风控建议
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import polars as pl
from loguru import logger

# 添加 app 到路径
sys.path.insert(0, str(Path(__file__).parent))

from app.data.okx_client import get_okx_client
from app.data.okx_feed import fetch_usdt_tickers, okx_to_quotes
from app.strategy.okx_t_runner import OKXTRunner, CRYPTO_T_CONFIG
from app.strategy.t_strategy import TStrategyConfig


# OpenClaw API 配置
OPENCLAW_URL = "http://localhost:3456/v1/chat/completions"
OPENCLAW_MODEL = "claude-proxy/claude-sonnet-4"

# 默认交易对
DEFAULT_PAIRS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT"]


def fetch_klines(pairs: list[str], bar: str = "4H", limit: int = 300) -> dict[str, pl.DataFrame]:
    """拉取 K 线数据"""
    client = get_okx_client()
    klines_data = {}

    for pair in pairs:
        logger.info(f"Fetching {pair} {bar} klines (limit={limit})...")
        klines = client.get_kline(pair, bar=bar, limit=limit)

        if not klines:
            logger.warning(f"No klines for {pair}")
            continue

        # OKX K线格式: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        df = pl.DataFrame({
            "timestamp": [int(k[0]) for k in klines],
            "open": [float(k[1]) for k in klines],
            "high": [float(k[2]) for k in klines],
            "low": [float(k[3]) for k in klines],
            "close": [float(k[4]) for k in klines],
            "volume": [float(k[5]) for k in klines],
            "amount": [float(k[6]) for k in klines],
        })

        # 按时间排序（OKX 返回的是倒序）
        df = df.sort("timestamp")
        klines_data[pair] = df
        logger.info(f"  {pair}: {len(df)} bars")

    return klines_data


def simple_backtest(df: pl.DataFrame, pair: str) -> dict:
    """简单回测：计算基础指标"""
    if len(df) < 20:
        return {"pair": pair, "sharpe": 0, "return": 0, "mdd": 0}

    # 计算简单移动平均
    closes = df["close"].to_list()
    sma_20 = sum(closes[-20:]) / 20
    sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma_20

    # 简单趋势判断
    trend = "UP" if sma_20 > sma_50 else "DOWN"

    # 计算收益率
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    total_return = (closes[-1] - closes[0]) / closes[0]

    # 计算 Sharpe（简化版）
    if len(returns) > 0:
        avg_return = sum(returns) / len(returns)
        std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
        sharpe = (avg_return / std_return * (252 ** 0.5)) if std_return > 0 else 0
    else:
        sharpe = 0

    # 计算最大回撤
    peak = closes[0]
    max_dd = 0
    for price in closes:
        if price > peak:
            peak = price
        dd = (peak - price) / peak
        if dd > max_dd:
            max_dd = dd

    return {
        "pair": pair,
        "sharpe": round(sharpe, 2),
        "return": round(total_return * 100, 2),
        "mdd": round(max_dd * 100, 2),
        "trend": trend,
        "sma_20": round(sma_20, 2),
        "sma_50": round(sma_50, 2),
        "last_price": round(closes[-1], 2),
    }


def run_backtests(klines_data: dict[str, pl.DataFrame]) -> list[dict]:
    """对所有交易对运行回测"""
    results = []

    for pair, df in klines_data.items():
        logger.info(f"Backtesting {pair}...")
        result = simple_backtest(df, pair)
        results.append(result)

    # 按 Sharpe 排序
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    return results


def print_backtest_report(results: list[dict]):
    """打印回测报告"""
    logger.info("\n" + "=" * 80)
    logger.info("BACKTEST REPORT")
    logger.info("=" * 80)

    for i, r in enumerate(results, 1):
        logger.info(
            f"{i}. {r['pair']:12} | Sharpe: {r['sharpe']:6.2f} | "
            f"Return: {r['return']:7.2f}% | MDD: {r['mdd']:6.2f}% | "
            f"Trend: {r['trend']:4} | Price: ${r['last_price']}"
        )

    logger.info("=" * 80 + "\n")


async def ask_openclaw(prompt: str, no_llm: bool = False) -> str:
    """向 OpenClaw 本地 LLM 请求建议"""
    if no_llm:
        return "CONTINUE"  # 不使用 LLM 时默认继续

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                OPENCLAW_URL,
                json={
                    "model": OPENCLAW_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a crypto trading risk manager. Analyze the market status and provide brief advice: CONTINUE, PAUSE, or ADJUST_PARAMS. Keep response under 100 words."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 200,
                }
            )

            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.warning(f"OpenClaw API error: {response.status_code}")
                return "CONTINUE"

    except Exception as e:
        logger.warning(f"OpenClaw request failed: {e}")
        return "CONTINUE"


async def run_live_trading(
    targets: list[dict],
    mode: str,
    interval: float,
    no_llm: bool,
    llm_check_interval: int = 300,  # 5分钟检查一次
):
    """启动实盘交易"""
    logger.info(f"\nStarting live trading: mode={mode}, interval={interval}s")
    logger.info(f"Targets: {targets}\n")

    # 创建 runner
    runner = OKXTRunner(
        targets=targets,
        mode=mode,
        config=CRYPTO_T_CONFIG,
        interval=interval,
    )

    # 启动 runner（后台任务）
    runner_task = asyncio.create_task(runner.run())

    # 定期向 OpenClaw 汇报
    last_llm_check = time.time()

    try:
        while runner._running:
            await asyncio.sleep(10)  # 每10秒检查一次

            # 定期向 LLM 汇报
            if time.time() - last_llm_check >= llm_check_interval:
                status = runner.get_status()

                prompt = f"""Market Status Report:
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Mode: {mode}
Tick Count: {status['tick_count']}
Signals Generated: {len(status['signals_log'])}

Recent Signals:
{json.dumps(status['signals_log'][-5:], indent=2)}

Should I CONTINUE, PAUSE, or ADJUST_PARAMS?"""

                advice = await ask_openclaw(prompt, no_llm)
                logger.info(f"\n[OpenClaw Advice]\n{advice}\n")

                if "PAUSE" in advice.upper():
                    logger.warning("OpenClaw suggests PAUSE. Stopping runner...")
                    runner.stop()
                    break

                last_llm_check = time.time()

    except KeyboardInterrupt:
        logger.info("\nReceived interrupt signal. Stopping...")
        runner.stop()

    finally:
        # 等待 runner 完成
        await runner_task

        # 打印最终状态
        final_status = runner.get_status()
        logger.info("\n" + "=" * 80)
        logger.info("FINAL STATUS")
        logger.info("=" * 80)
        logger.info(f"Total Ticks: {final_status['tick_count']}")
        logger.info(f"Total Signals: {len(final_status['signals_log'])}")
        logger.info("=" * 80 + "\n")


async def main():
    parser = argparse.ArgumentParser(description="OpenClaw + OKX Trading Bot")
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS), help="Trading pairs (comma-separated)")
    parser.add_argument("--mode", default="paper", choices=["paper", "okx"], help="Trading mode")
    parser.add_argument("--interval", type=float, default=2.0, help="Quote fetch interval (seconds)")
    parser.add_argument("--backtest-only", action="store_true", help="Only run backtest, no live trading")
    parser.add_argument("--no-llm", action="store_true", help="Disable OpenClaw LLM integration")
    parser.add_argument("--bar", default="4H", help="Kline bar size for backtest")
    parser.add_argument("--limit", type=int, default=300, help="Number of klines to fetch")

    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",")]

    logger.info("=" * 80)
    logger.info("OpenClaw + OKX Trading Bot")
    logger.info("=" * 80)
    logger.info(f"Pairs: {pairs}")
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Backtest Only: {args.backtest_only}")
    logger.info(f"LLM Enabled: {not args.no_llm}")
    logger.info("=" * 80 + "\n")

    # Step 1: 拉取 K 线数据
    klines_data = fetch_klines(pairs, bar=args.bar, limit=args.limit)

    if not klines_data:
        logger.error("No klines data fetched. Exiting.")
        return

    # Step 2: 运行回测
    backtest_results = run_backtests(klines_data)
    print_backtest_report(backtest_results)

    if args.backtest_only:
        logger.info("Backtest-only mode. Exiting.")
        return

    # Step 3: 选择最优策略
    # 选 Sharpe > 0 的前3个
    top_pairs = [r for r in backtest_results if r["sharpe"] > 0][:3]

    if not top_pairs:
        logger.warning("No profitable strategies found. Using top pair anyway.")
        top_pairs = backtest_results[:1]

    # Step 4: 构建 targets
    targets = []
    for r in top_pairs:
        # 根据趋势选择模式
        mode_type = "long_t" if r["trend"] == "UP" else "short_t"
        targets.append({
            "code": r["pair"],
            "mode": mode_type,
            "volume": 1,  # 每次交易1个单位
        })

    logger.info(f"Selected targets: {targets}\n")

    # Step 5: 启动实盘
    await run_live_trading(
        targets=targets,
        mode=args.mode,
        interval=args.interval,
        no_llm=args.no_llm,
    )


if __name__ == "__main__":
    asyncio.run(main())
