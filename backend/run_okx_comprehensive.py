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
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import polars as pl
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))


def _bootstrap_hist_mat() -> bool:
    candidates = [
        os.getenv("HIST_MAT_HOME"),
        os.getenv("KATANA_HIST_MAT_HOME"),
        "/srv/hist-mat",
        str(Path(__file__).resolve().parents[2] / "hist-mat"),
        r"C:\Users\Administrator\projects\hist-mat",
        r"C:\Users\Administrator\hist-mat",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            sys.path.insert(0, str(candidate))
            return True
    return False


HIST_MAT_AVAILABLE = _bootstrap_hist_mat()

from app.data.crypto_market_data import get_market_data_client

if HIST_MAT_AVAILABLE:
    try:
        from core.engine import HistMatEngine
        from core.types import DomainConfig, ReductionResult
    except Exception as exc:
        HIST_MAT_AVAILABLE = False
        HIST_MAT_IMPORT_ERROR = str(exc)
else:
    HIST_MAT_IMPORT_ERROR = "hist-mat repository not found"

if not HIST_MAT_AVAILABLE:

    @dataclass(frozen=True)
    class DomainConfig:
        name: str
        sigmoid_k: float
        sigmoid_x0: float
        agency_weights: dict
        surplus_rate_range: tuple[float, float]
        v_label: str
        s_label: str
        c_label: str
        has_censorship: bool
        min_texts: int

    @dataclass(frozen=True)
    class ReductionResult:
        gap_rs: float
        sss_reduced: float
        censorship_pressure: float
        sentiment_divergence: float
        entropy: float
        ideology_penetration: float

    class HistMatEngine:
        def __init__(self, domain: DomainConfig):
            self.domain = domain

        def run(self, v_raw: float, s_raw: float, c_raw: float, reduction: ReductionResult):
            p_risk = _clip01(
                0.25 * reduction.gap_rs
                + 0.25 * reduction.censorship_pressure
                + 0.25 * reduction.sentiment_divergence
                + 0.25 * reduction.entropy
            )
            return SimpleNamespace(p_risk=p_risk)


def configure_logging(json_output: bool) -> None:
    logger.remove()
    if not json_output:
        logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))


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


def _clip01(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def _score_from_signed(value: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return _clip01(0.5 + value / scale)


def _series_returns(closes: np.ndarray) -> np.ndarray:
    if len(closes) < 2:
        return np.array([])
    prev = np.maximum(closes[:-1], 1e-10)
    return (closes[1:] - closes[:-1]) / prev


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
    if len(p_arr) == 0:
        return {
            "pair": pair,
            "p_risk_current": 0.5,
            "p_risk_mean": 0.5,
            "p_risk_max": 0.5,
            "p_risk_min": 0.5,
            "crisis_signals": 0,
            "stable_signals": 0,
            "bars": klines.height,
            "warning": f"insufficient bars for hist-mat lookback={lookback}",
        }

    return {
        "pair": pair,
        "p_risk_current": float(p_arr[-1]),
        "p_risk_mean": float(np.mean(p_arr)),
        "p_risk_max": float(np.max(p_arr)),
        "p_risk_min": float(np.min(p_arr)),
        "crisis_signals": int(np.sum(p_arr > 0.65)),
        "stable_signals": int(np.sum(p_arr < 0.35)),
        "bars": klines.height,
        "source": "hist-mat" if HIST_MAT_AVAILABLE else "fallback",
        "warning": None if HIST_MAT_AVAILABLE else HIST_MAT_IMPORT_ERROR,
    }


def compute_price_volume_factor_groups(df: pl.DataFrame, pair: str) -> dict:
    """Compute transparent price/volume factor groups from OHLCV bars."""
    if df.height < 20:
        neutral = {
            "score": 0.5,
            "direction": "neutral",
            "confidence": 0.0,
            "metrics": {"reason": "insufficient bars"},
        }
        return {
            "trend": neutral,
            "momentum": neutral,
            "volatility": neutral,
            "volume": neutral,
            "mean_reversion": neutral,
        }

    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    volumes = df["volume"].to_numpy()
    returns = _series_returns(closes)

    sma_20 = float(np.mean(closes[-20:]))
    sma_50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else sma_20
    last_close = float(closes[-1])
    trend_spread = (sma_20 - sma_50) / max(sma_50, 1e-10)
    trend_score = _score_from_signed(trend_spread, 0.08)

    ret_12 = (closes[-1] - closes[-13]) / max(closes[-13], 1e-10) if len(closes) >= 13 else 0.0
    ret_24 = (closes[-1] - closes[-25]) / max(closes[-25], 1e-10) if len(closes) >= 25 else ret_12
    momentum_score = _score_from_signed(0.6 * ret_12 + 0.4 * ret_24, 0.12)

    vol_24 = float(np.std(returns[-24:])) if len(returns) >= 24 else float(np.std(returns))
    vol_72 = float(np.std(returns[-72:])) if len(returns) >= 72 else max(vol_24, 1e-10)
    vol_ratio = vol_24 / max(vol_72, 1e-10)
    range_pct = (float(np.max(highs[-20:])) - float(np.min(lows[-20:]))) / max(last_close, 1e-10)
    volatility_score = _clip01(1.0 - (vol_ratio - 0.8) / 1.8)

    vol_mean_20 = float(np.mean(volumes[-20:]))
    vol_mean_60 = float(np.mean(volumes[-60:])) if len(volumes) >= 60 else max(vol_mean_20, 1e-10)
    volume_ratio = vol_mean_20 / max(vol_mean_60, 1e-10)
    last_return = float(returns[-1]) if len(returns) else 0.0
    volume_score = _clip01(0.5 + np.sign(last_return) * min(abs(volume_ratio - 1.0), 1.0) * 0.25)

    rolling_mean = sma_20
    rolling_std = float(np.std(closes[-20:]))
    zscore = (last_close - rolling_mean) / max(rolling_std, 1e-10)
    mean_reversion_score = _clip01(0.5 - zscore / 6.0)

    return {
        "trend": {
            "score": round(trend_score, 4),
            "direction": "bullish" if trend_score > 0.55 else "bearish" if trend_score < 0.45 else "neutral",
            "confidence": round(min(abs(trend_score - 0.5) * 2.0, 1.0), 4),
            "metrics": {
                "sma_20": round(sma_20, 6),
                "sma_50": round(sma_50, 6),
                "trend_spread_pct": round(trend_spread * 100.0, 4),
            },
        },
        "momentum": {
            "score": round(momentum_score, 4),
            "direction": "bullish" if momentum_score > 0.55 else "bearish" if momentum_score < 0.45 else "neutral",
            "confidence": round(min(abs(momentum_score - 0.5) * 2.0, 1.0), 4),
            "metrics": {
                "return_12_bars_pct": round(ret_12 * 100.0, 4),
                "return_24_bars_pct": round(ret_24 * 100.0, 4),
            },
        },
        "volatility": {
            "score": round(volatility_score, 4),
            "direction": "risk_off" if volatility_score < 0.45 else "risk_on" if volatility_score > 0.55 else "neutral",
            "confidence": round(min(abs(volatility_score - 0.5) * 2.0, 1.0), 4),
            "metrics": {
                "vol_24": round(vol_24, 8),
                "vol_72": round(vol_72, 8),
                "vol_ratio": round(vol_ratio, 4),
                "range_20_pct": round(range_pct * 100.0, 4),
            },
        },
        "volume": {
            "score": round(volume_score, 4),
            "direction": "confirming_up" if volume_score > 0.55 else "confirming_down" if volume_score < 0.45 else "neutral",
            "confidence": round(min(abs(volume_score - 0.5) * 2.0, 1.0), 4),
            "metrics": {
                "volume_mean_20": round(vol_mean_20, 6),
                "volume_mean_60": round(vol_mean_60, 6),
                "volume_ratio": round(volume_ratio, 4),
                "last_return_pct": round(last_return * 100.0, 4),
            },
        },
        "mean_reversion": {
            "score": round(mean_reversion_score, 4),
            "direction": "oversold_bounce" if mean_reversion_score > 0.55 else "overbought_pullback" if mean_reversion_score < 0.45 else "neutral",
            "confidence": round(min(abs(mean_reversion_score - 0.5) * 2.0, 1.0), 4),
            "metrics": {
                "zscore_20": round(float(zscore), 4),
                "last_close": round(last_close, 6),
            },
        },
    }


def build_factor_view(backtest: dict, histmat: dict, sentiment: dict, df: pl.DataFrame, pair: str) -> dict:
    price_volume_groups = compute_price_volume_factor_groups(df, pair)
    hmc_score = _clip01(1.0 - histmat["p_risk_current"])
    sentiment_score = _clip01(float(sentiment.get("sentiment_score", 0.5)))
    backtest_score = _clip01(float(backtest.get("sharpe", 0.0)) / 2.0)

    factor_groups = {
        **price_volume_groups,
        "hmc": {
            "score": round(hmc_score, 4),
            "direction": "risk_off" if hmc_score < 0.45 else "risk_on" if hmc_score > 0.55 else "neutral",
            "confidence": round(min(abs(hmc_score - 0.5) * 2.0, 1.0), 4),
            "metrics": histmat,
        },
        "sentiment": {
            "score": round(sentiment_score, 4),
            "direction": "positive" if sentiment_score > 0.55 else "negative" if sentiment_score < 0.45 else "neutral",
            "confidence": round(min(abs(sentiment_score - 0.5) * 2.0, 1.0), 4),
            "metrics": sentiment,
        },
        "replay_quality": {
            "score": round(backtest_score, 4),
            "direction": "positive" if backtest_score > 0.55 else "negative" if backtest_score < 0.45 else "neutral",
            "confidence": round(min(abs(backtest_score - 0.5) * 2.0, 1.0), 4),
            "metrics": backtest,
        },
    }

    weights = {
        "trend": 0.18,
        "momentum": 0.16,
        "volatility": 0.12,
        "volume": 0.10,
        "mean_reversion": 0.10,
        "hmc": 0.14,
        "sentiment": 0.08,
        "replay_quality": 0.12,
    }

    weighted_score = 0.0
    group_scores = {}
    for name, weight in weights.items():
        score = float(factor_groups[name]["score"])
        weighted_score += score * weight
        group_scores[name] = {
            "score": round(score, 4),
            "weight": weight,
            "contribution": round(score * weight, 4),
        }

    risk_flags = []
    if histmat["p_risk_current"] > 0.70:
        risk_flags.append("hmc_high_transition_risk")
    if float(factor_groups["volatility"]["metrics"]["vol_ratio"]) > 1.8:
        risk_flags.append("volatility_expansion")
    if backtest.get("mdd", 0) > 20:
        risk_flags.append("large_recent_drawdown")

    if weighted_score > 0.65 and not risk_flags:
        decision = "strong_long"
        advice = "强烈做多"
    elif weighted_score > 0.55 and histmat["p_risk_current"] < 0.65:
        decision = "moderate_long"
        advice = "适度做多"
    elif weighted_score < 0.40 or histmat["p_risk_current"] > 0.70:
        decision = "avoid_or_short"
        advice = "观望/做空"
    else:
        decision = "neutral"
        advice = "中性观望"

    return {
        "factor_groups": factor_groups,
        "group_scores": group_scores,
        "final_score": round(weighted_score, 4),
        "decision": decision,
        "advice": advice,
        "risk_flags": risk_flags,
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
    market_data = get_market_data_client()

    results = []

    for pair in pairs:
        logger.info(f"\n分析 {pair}...")

        # 1. 拉取 K 线
        klines, market_data_source = market_data.get_kline(pair, bar="4H", limit=limit)
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

        factor_view = build_factor_view(backtest, histmat, sentiment, df, pair)
        综合得分 = factor_view["final_score"]
        建议 = factor_view["advice"]

        results.append({
            "pair": pair,
            "market_data_source": market_data_source,
            "backtest": backtest,
            "histmat": histmat,
            "sentiment": sentiment,
            "factor_groups": factor_view["factor_groups"],
            "group_scores": factor_view["group_scores"],
            "final_score": factor_view["final_score"],
            "decision": factor_view["decision"],
            "risk_flags": factor_view["risk_flags"],
            "综合得分": round(综合得分, 3),
            "建议": 建议,
        })

        logger.info(f"  source={market_data_source} | score={综合得分:.3f} | decision={factor_view['decision']} | {建议}")

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
    parser.add_argument("--json", action="store_true", help="只输出 JSON 结果")

    args = parser.parse_args()
    configure_logging(args.json)
    pairs = [p.strip() for p in args.pairs.split(",")]

    logger.info("=" * 100)
    logger.info("OKX 综合分析系统启动")
    logger.info("=" * 100)

    results = await comprehensive_analysis(pairs, args.limit)
    payload = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "provider": "katana",
        "exchange": "okx",
        "requested_pairs": pairs,
        "result_count": len(results),
        "results": results,
    }

    if not args.json:
        print_comprehensive_report(results)

    # 保存结果
    out_file = Path(__file__).parent / "results" / f"okx_comprehensive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"\n结果已保存: {out_file}")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
