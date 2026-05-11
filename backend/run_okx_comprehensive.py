#!/usr/bin/env python3
"""OKX 综合分析系统：策略回测 + hist-mat 引擎 + 实时舆情

三维度分析：
1. 策略回测 - Sharpe/收益率/最大回撤
2. hist-mat 引擎 - P_risk 跃迁风险（市场矛盾累积度）
3. 实时舆情 - 新闻/社交媒体情绪分析

最终输出综合交易建议。
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
import polars as pl
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, r'C:\Users\Administrator\hist-mat')

from app.data.okx_client import get_okx_client
from core.engine import HistMatEngine
from core.types import DomainConfig, ReductionResult

# hist-mat 配置（加密货币市场）
DOMAIN_CRYPTO = DomainConfig(
    name="crypto_market",
    sigmoid_k=2.5,
    sigmoid_x0=0.60,
    agency_weights={"sss": 0.35, "gap_rs": 0.40, "censorship": 0.25},
    surplus_rate_range=(0.0, 8.0),
    v_label="散户FOMO资金",
    s_label="巨鲸套利收割",
    c_label="监管压制泡沫",
    has_censorship=True,
    min_texts=0,
)


def compute_crypto_reduction(window: pl.DataFrame) -> tuple[ReductionResult, float, float, float]:
    """从加密货币 K 线计算 hist-mat 变量"""
    if window.height < 10:
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

    # 价格波动范围
    price_range = (np.max(closes) - np.min(closes)) / max(np.min(closes), 1e-10)

    # 映射到 hist-mat 变量
    v_raw = vol_ratio * 6.0  # 散户FOMO
    s_raw = price_range * 600.0  # 巨鲸收割
    c_raw = max(0.0, -win_return) * 1000.0 + price_range * 120.0  # 监管压制

    # 构造 ReductionResult
    gap_rs = float(np.clip(price_range * 3.5 + abs(win_return) * 2.5, 0, 1))
    sss_reduced = float(np.clip(0.5 + win_return * 10.0, 0.05, 1.0))
    censorship = float(np.clip(max(0.0, -win_return) / 0.03, 0, 1))

    current_return = 0.0
    if len(closes) >= 2:
        current_return = (closes[-1] - closes[-2]) / max(closes[-2], 1e-10)
    sentiment_div = float(np.clip(abs(current_return) * 25.0, 0, 1))

    entropy = float(np.clip(price_range * 6.0 + vol_ratio / 2.5, 0.05, 1.0))

    reduction = ReductionResult(
        gap_rs=gap_rs,
        sss_reduced=sss_reduced,
        censorship_pressure=censorship,
        sentiment_divergence=sentiment_div,
        entropy=entropy,
        ideology_penetration=0.3,
    )

    return reduction, v_raw, s_raw, c_raw


async def fetch_sentiment(pair: str) -> dict:
    """获取实时舆情（模拟 - 实际应接入新闻/Twitter API）"""
    # TODO: 接入真实舆情 API
    # 示例：CryptoCompare News API, Twitter API, Reddit API

    # 模拟返回
    base = pair.split("-")[0]
    return {
        "pair": pair,
        "sentiment_score": 0.65,  # 0-1，越高越乐观
        "news_count": 42,
        "positive_ratio": 0.68,
        "fear_greed_index": 55,  # 0-100
        "social_volume": 8500,
        "source": "simulated",
    }


def analyze_pair_histmat(pair: str, klines: pl.DataFrame) -> dict:
    """对单个交易对运行 hist-mat 分析"""
    engine = HistMatEngine(domain=DOMAIN_CRYPTO)

    lookback = 50
    p_risk_series = []

    for i in range(lookback, klines.height):
        window = klines.slice(i - lookback, lookback)
        reduction, v_raw, s_raw, c_raw = compute_crypto_reduction(window)
        output = engine.run(v_raw=v_raw, s_raw=s_raw, c_raw=c_raw, reduction=reduction)
        p_risk_series.append(output.p_risk)

    p_arr = np.array(p_risk_series)

    return {
        "pair": pair,
        "p_risk_current": float(p_arr[-1]) if len(p_arr) > 0 else 0.5,
        "p_risk_mean": float(np.mean(p_arr)),
        "p_risk_max": float(np.max(p_arr)),
        "p_risk_min": float(np.min(p_arr)),
        "crisis_signals": int(np.sum(p_arr > 0.65)),
        "stable_signals": int(np.sum(p_arr < 0.35)),
    }


def simple_backtest(df: pl.DataFrame, pair: str) -> dict:
    """简单回测"""
    if len(df) < 20:
        return {"pair": pair, "sharpe": 0, "return": 0, "mdd": 0}

    closes = df["close"].to_list()
    sma_20 = sum(closes[-20:]) / 20
    sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma_20
    trend = "UP" if sma_20 > sma_50 else "DOWN"

    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    total_return = (closes[-1] - closes[0]) / closes[0]

    if len(returns) > 0:
        avg_return = sum(returns) / len(returns)
        std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
        sharpe = (avg_return / std_return * (252 ** 0.5)) if std_return > 0 else 0
    else:
        sharpe = 0

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
        "last_price": round(closes[-1], 2),
    }


async def comprehensive_analysis(pairs: list[str], limit: int = 200):
    """综合分析：回测 + hist-mat + 舆情"""
    client = get_okx_client()

    results = []

    for pair in pairs:
        logger.info(f"\n分析 {pair}...")

        # 1. 拉取 K 线
        klines = client.get_kline(pair, bar="4H", limit=limit)
        if not klines:
            logger.warning(f"  {pair}: 无数据")
            continue

        df = pl.DataFrame({
            "timestamp": [int(k[0]) for k in klines],
            "open": [float(k[1]) for k in klines],
            "high": [float(k[2]) for k in klines],
            "low": [float(k[3]) for k in klines],
            "close": [float(k[4]) for k in klines],
            "volume": [float(k[5]) for k in klines],
        }).sort("timestamp")

        # 2. 策略回测
        backtest = simple_backtest(df, pair)

        # 3. hist-mat 分析
        histmat = analyze_pair_histmat(pair, df)

        # 4. 舆情分析
        sentiment = await fetch_sentiment(pair)

        # 5. 综合评分
        # Sharpe 权重 40%，P_risk 权重 30%，舆情权重 30%
        sharpe_score = np.clip(backtest["sharpe"] / 2.0, 0, 1)  # 归一化到 0-1
        prisk_score = 1.0 - histmat["p_risk_current"]  # P_risk 越低越好
        sentiment_score = sentiment["sentiment_score"]

        综合得分 = (
            sharpe_score * 0.40 +
            prisk_score * 0.30 +
            sentiment_score * 0.30
        )

        # 6. 交易建议
        if 综合得分 > 0.65 and histmat["p_risk_current"] < 0.50:
            建议 = "强烈做多"
        elif 综合得分 > 0.55 and histmat["p_risk_current"] < 0.60:
            建议 = "适度做多"
        elif 综合得分 < 0.40 or histmat["p_risk_current"] > 0.70:
            建议 = "观望/做空"
        else:
            建议 = "中性观望"

        results.append({
            "pair": pair,
            "backtest": backtest,
            "histmat": histmat,
            "sentiment": sentiment,
            "综合得分": round(综合得分, 3),
            "建议": 建议,
        })

        logger.info(f"  Sharpe: {backtest['sharpe']:.2f} | P_risk: {histmat['p_risk_current']:.3f} | 舆情: {sentiment['sentiment_score']:.2f} | 综合: {综合得分:.3f} | {建议}")

    # 按综合得分排序
    results.sort(key=lambda x: x["综合得分"], reverse=True)

    return results


def print_comprehensive_report(results: list[dict]):
    """打印综合报告"""
    print("\n" + "=" * 100)
    print("OKX 综合分析报告 - 策略回测 + hist-mat + 舆情")
    print("=" * 100)
    print(f"{'排名':<4} {'交易对':<12} {'Sharpe':<8} {'收益率':<8} {'P_risk':<8} {'舆情':<8} {'综合得分':<10} {'建议':<12}")
    print("-" * 100)

    for i, r in enumerate(results, 1):
        bt = r["backtest"]
        hm = r["histmat"]
        st = r["sentiment"]

        print(
            f"{i:<4} {r['pair']:<12} "
            f"{bt['sharpe']:>7.2f} "
            f"{bt['return']:>6.2f}% "
            f"{hm['p_risk_current']:>7.3f} "
            f"{st['sentiment_score']:>7.2f} "
            f"{r['综合得分']:>9.3f} "
            f"{r['建议']:<12}"
        )

    print("=" * 100)

    # 详细分析 TOP 3
    print("\n详细分析 (TOP 3):")
    for i, r in enumerate(results[:3], 1):
        print(f"\n{i}. {r['pair']}")
        print(f"   策略回测: Sharpe {r['backtest']['sharpe']:.2f}, 收益 {r['backtest']['return']:.2f}%, 回撤 {r['backtest']['mdd']:.2f}%")
        print(f"   hist-mat: P_risk {r['histmat']['p_risk_current']:.3f} (均值 {r['histmat']['p_risk_mean']:.3f}), 危机信号 {r['histmat']['crisis_signals']} 次")
        print(f"   舆情分析: 情绪 {r['sentiment']['sentiment_score']:.2f}, 正面比 {r['sentiment']['positive_ratio']:.2f}, 恐慌贪婪指数 {r['sentiment']['fear_greed_index']}")
        print(f"   → 建议: {r['建议']}")


async def main():
    parser = argparse.ArgumentParser(description="OKX 综合分析系统")
    parser.add_argument("--pairs", default="BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT", help="交易对")
    parser.add_argument("--limit", type=int, default=200, help="K线数量")

    args = parser.parse_args()
    pairs = [p.strip() for p in args.pairs.split(",")]

    logger.info("=" * 100)
    logger.info("OKX 综合分析系统启动")
    logger.info("=" * 100)

    results = await comprehensive_analysis(pairs, args.limit)
    print_comprehensive_report(results)

    # 保存结果
    out_file = Path(__file__).parent / "results" / f"okx_comprehensive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"\n结果已保存: {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
