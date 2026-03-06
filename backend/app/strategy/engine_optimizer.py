"""唯物引擎参数优化器 — 让数据决定参数，不再拍脑袋

思路:
  1. 拉 OKX 历史 K 线 + 资金费率等数据
  2. 在历史数据上模拟引擎决策
  3. 用 Optuna 搜索最优参数组合
  4. 输出验证后的参数 → 替换引擎硬编码值

优化目标: 最大化 Sharpe Ratio（收益/风险比）
"""

import time
import json
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import requests
from loguru import logger


@dataclass
class EngineParams:
    """引擎可调参数 — 从唯心到唯物"""
    # 趋势因子
    trend_weight: float = 2.0

    # 量价因子
    taker_long_threshold: float = 1.5    # 主买比 > X → 做多
    taker_short_threshold: float = 0.7   # 主买比 < X → 做空
    taker_weight: float = 3.0

    # 资金费率因子
    funding_long_threshold: float = -0.01   # 费率 < X → 做多
    funding_short_threshold: float = 0.03   # 费率 > X → 做空
    funding_weight: float = 2.0

    # 位置因子
    pos_long_threshold: float = 0.2     # 区间位置 < X → 做多
    pos_short_threshold: float = 0.8    # 区间位置 > X → 做空
    pos_weight: float = 2.0

    # 矛盾因子
    contradiction_weight: float = 3.0

    # 信号门槛
    signal_threshold: int = 5

    # 止损参数
    sl_amplitude_ratio: float = 0.3     # 止损 = 振幅 × X
    sl_min_pct: float = 0.01            # 最小止损 1%
    tp_ratio: float = 1.5               # 盈亏比

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "EngineParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def fetch_historical_klines(pair: str, bar: str = "4H", limit: int = 300) -> list[dict]:
    """拉 OKX 历史 K 线"""
    swap_id = pair + "-SWAP" if not pair.endswith("-SWAP") else pair
    all_data = []
    after = ""

    while len(all_data) < limit:
        params = {"instId": swap_id, "bar": bar, "limit": "100"}
        if after:
            params["after"] = after

        r = requests.get("https://www.okx.com/api/v5/market/candles",
                         params=params, timeout=10)
        data = r.json().get("data", [])
        if not data:
            break

        all_data.extend(data)
        after = data[-1][0]  # 最后一条的时间戳
        time.sleep(0.2)

    # 反转为时间正序，解析
    bars = []
    for d in reversed(all_data[:limit]):
        bars.append({
            "ts": int(d[0]),
            "open": float(d[1]),
            "high": float(d[2]),
            "low": float(d[3]),
            "close": float(d[4]),
            "volume": float(d[5]),
        })
    return bars


def fetch_historical_funding(pair: str, limit: int = 300) -> list[dict]:
    """拉 OKX 历史资金费率"""
    swap_id = pair + "-SWAP" if not pair.endswith("-SWAP") else pair
    all_data = []
    after = ""

    while len(all_data) < limit:
        params = {"instId": swap_id, "limit": "100"}
        if after:
            params["after"] = after

        r = requests.get("https://www.okx.com/api/v5/public/funding-rate-history",
                         params=params, timeout=10)
        data = r.json().get("data", [])
        if not data:
            break

        all_data.extend(data)
        after = data[-1].get("fundingTime", "")
        time.sleep(0.2)

    result = []
    for d in reversed(all_data[:limit]):
        result.append({
            "ts": int(d.get("fundingTime", 0)),
            "rate": float(d.get("realizedRate", d.get("fundingRate", 0))),
        })
    return result


def simulate_engine(bars: list[dict], funding: list[dict], params: EngineParams) -> dict:
    """在历史数据上模拟引擎决策，计算收益

    简化模拟：每根 4H K 线判断一次，持有到下根 K 线
    """
    if len(bars) < 20:
        return {"sharpe": 0, "total_return": 0, "trades": 0, "win_rate": 0}

    # 预处理：资金费率对齐到 K 线
    fr_map = {}
    for f in funding:
        fr_map[f["ts"] // (4 * 3600 * 1000)] = f["rate"]

    returns = []
    trades = []
    pos = 0  # 0=空仓, 1=多, -1=空
    entry_price = 0

    for i in range(12, len(bars) - 1):
        bar = bars[i]
        price = bar["close"]

        # 计算简化版因子
        highs = [b["high"] for b in bars[i-11:i+1]]
        lows = [b["low"] for b in bars[i-11:i+1]]
        closes = [b["close"] for b in bars[i-11:i+1]]
        vols = [b["volume"] for b in bars[i-11:i+1]]

        # 趋势
        rh = highs[-4:]
        rl = lows[-4:]
        hh = all(rh[j] >= rh[j-1] for j in range(1, len(rh)))
        hl = all(rl[j] >= rl[j-1] for j in range(1, len(rl)))
        lh = all(rh[j] <= rh[j-1] for j in range(1, len(rh)))
        ll = all(rl[j] <= rl[j-1] for j in range(1, len(rl)))

        trend = "UP" if (hh and hl) else "DOWN" if (lh and ll) else "RANGE"

        # 振幅
        h24 = max(highs[-6:])
        l24 = min(lows[-6:])
        sod = closes[-7] if len(closes) > 6 else closes[0]
        amplitude = (h24 - l24) / sod if sod > 0 else 0
        change = (price - sod) / sod if sod > 0 else 0

        # 量能
        vol_r = sum(vols[-3:]) / 3
        vol_p = sum(vols[-6:-3]) / 3 if len(vols) >= 6 else vol_r
        vol_trend = "INC" if vol_r > vol_p * 1.2 else "DEC" if vol_r < vol_p * 0.8 else "STABLE"

        # 位置
        support = min(lows[-6:])
        resistance = max(highs[-6:])
        pos_in_range = (price - support) / (resistance - support) if resistance > support else 0.5

        # 资金费率
        ts_key = bar["ts"] // (4 * 3600 * 1000)
        fr = fr_map.get(ts_key, 0)

        # 简化 taker_ratio (用量价关系近似)
        buy_pressure = sum(1 for j in range(-3, 0) if closes[j] > closes[j-1])
        taker_ratio = 0.5 + buy_pressure * 0.3  # 简化近似

        # === 引擎决策（使用参数化阈值）===
        long_score = 0
        short_score = 0

        if trend == "UP":
            long_score += params.trend_weight
        elif trend == "DOWN":
            short_score += params.trend_weight

        if taker_ratio > params.taker_long_threshold:
            long_score += params.taker_weight
        elif taker_ratio < params.taker_short_threshold:
            short_score += params.taker_weight

        if fr < params.funding_long_threshold:
            long_score += params.funding_weight
        elif fr > params.funding_short_threshold:
            short_score += params.funding_weight

        if pos_in_range < params.pos_long_threshold:
            long_score += params.pos_weight
        elif pos_in_range > params.pos_short_threshold:
            short_score += params.pos_weight

        # 矛盾
        if change < -0.02 and taker_ratio > 1.3:
            long_score += params.contradiction_weight
        if change > 0.02 and vol_trend == "INC":
            long_score += params.contradiction_weight * 0.67
        if change < -0.03 and vol_trend == "DEC":
            long_score += params.contradiction_weight
        if pos_in_range > 0.85 and taker_ratio < 0.8:
            short_score += params.contradiction_weight

        # 信号
        signal = "WAIT"
        if long_score > short_score and long_score >= params.signal_threshold:
            signal = "LONG"
        elif short_score > long_score and short_score >= params.signal_threshold:
            signal = "SHORT"

        # === 执行 ===
        next_bar = bars[i + 1]
        next_close = next_bar["close"]

        # 平仓
        if pos == 1 and signal != "LONG":
            pnl = (next_close - entry_price) / entry_price
            sl_pct = max(amplitude * params.sl_amplitude_ratio, params.sl_min_pct)
            pnl = max(pnl, -sl_pct)  # 止损
            pnl = min(pnl, sl_pct * params.tp_ratio)  # 止盈
            returns.append(pnl)
            trades.append(pnl)
            pos = 0
        elif pos == -1 and signal != "SHORT":
            pnl = (entry_price - next_close) / entry_price
            sl_pct = max(amplitude * params.sl_amplitude_ratio, params.sl_min_pct)
            pnl = max(pnl, -sl_pct)
            pnl = min(pnl, sl_pct * params.tp_ratio)
            returns.append(pnl)
            trades.append(pnl)
            pos = 0

        # 开仓
        if pos == 0 and signal == "LONG":
            entry_price = next_close
            pos = 1
        elif pos == 0 and signal == "SHORT":
            entry_price = next_close
            pos = -1

        if pos == 0:
            returns.append(0)

    # 统计
    returns = np.array(returns) if returns else np.array([0])
    n_trades = len(trades)
    wins = sum(1 for t in trades if t > 0)

    total_return = float(np.prod(1 + returns) - 1)
    sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(365 * 6)) if np.std(returns) > 0 else 0
    win_rate = wins / n_trades if n_trades > 0 else 0
    avg_pnl = float(np.mean(trades)) if trades else 0

    return {
        "sharpe": round(sharpe, 3),
        "total_return": round(total_return * 100, 2),
        "trades": n_trades,
        "win_rate": round(win_rate, 3),
        "avg_pnl_pct": round(avg_pnl * 100, 3),
    }


def optimize(pairs: list[str] | None = None, n_trials: int = 200) -> EngineParams:
    """用 Optuna 搜索最优引擎参数"""
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        logger.error("pip install optuna")
        return EngineParams()

    pairs = pairs or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

    # 拉数据
    logger.info(f"Fetching historical data for {pairs}...")
    all_bars = {}
    all_funding = {}
    for pair in pairs:
        logger.info(f"  {pair}...")
        all_bars[pair] = fetch_historical_klines(pair, "4H", 300)
        all_funding[pair] = fetch_historical_funding(pair, 300)
        time.sleep(0.5)

    logger.info(f"Data loaded. Starting optimization ({n_trials} trials)...")

    def objective(trial):
        p = EngineParams(
            trend_weight=trial.suggest_float("trend_weight", 0, 5),
            taker_long_threshold=trial.suggest_float("taker_long", 1.1, 2.0),
            taker_short_threshold=trial.suggest_float("taker_short", 0.4, 0.9),
            taker_weight=trial.suggest_float("taker_weight", 0, 5),
            funding_long_threshold=trial.suggest_float("funding_long", -0.03, 0),
            funding_short_threshold=trial.suggest_float("funding_short", 0.01, 0.05),
            funding_weight=trial.suggest_float("funding_weight", 0, 5),
            pos_long_threshold=trial.suggest_float("pos_long", 0.05, 0.35),
            pos_short_threshold=trial.suggest_float("pos_short", 0.65, 0.95),
            pos_weight=trial.suggest_float("pos_weight", 0, 5),
            contradiction_weight=trial.suggest_float("contradiction_weight", 0, 5),
            signal_threshold=trial.suggest_int("signal_threshold", 3, 8),
            sl_amplitude_ratio=trial.suggest_float("sl_amp_ratio", 0.1, 0.6),
            sl_min_pct=trial.suggest_float("sl_min_pct", 0.005, 0.03),
            tp_ratio=trial.suggest_float("tp_ratio", 1.0, 3.0),
        )

        total_sharpe = 0
        for pair in pairs:
            result = simulate_engine(all_bars[pair], all_funding[pair], p)
            total_sharpe += result["sharpe"]

        return total_sharpe / len(pairs)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials)

    # 最优参数
    best = study.best_params
    best_params = EngineParams(
        trend_weight=best["trend_weight"],
        taker_long_threshold=best["taker_long"],
        taker_short_threshold=best["taker_short"],
        taker_weight=best["taker_weight"],
        funding_long_threshold=best["funding_long"],
        funding_short_threshold=best["funding_short"],
        funding_weight=best["funding_weight"],
        pos_long_threshold=best["pos_long"],
        pos_short_threshold=best["pos_short"],
        pos_weight=best["pos_weight"],
        contradiction_weight=best["contradiction_weight"],
        signal_threshold=best["signal_threshold"],
        sl_amplitude_ratio=best["sl_amp_ratio"],
        sl_min_pct=best["sl_min_pct"],
        tp_ratio=best["tp_ratio"],
    )

    # 验证
    logger.info(f"\n{'='*60}")
    logger.info(f"Best Sharpe: {study.best_value:.3f}")
    logger.info(f"{'='*60}")
    for pair in pairs:
        result = simulate_engine(all_bars[pair], all_funding[pair], best_params)
        logger.info(f"  {pair}: sharpe={result['sharpe']}, return={result['total_return']}%, "
                     f"trades={result['trades']}, win={result['win_rate']:.0%}")
    logger.info(f"\nOptimized params:\n{json.dumps(best_params.to_dict(), indent=2)}")

    # 保存
    save_path = Path("D:/projects/quant-terminal/data/engine_params.json")
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(json.dumps(best_params.to_dict(), indent=2))
    logger.info(f"Saved to {save_path}")

    return best_params


if __name__ == "__main__":
    optimize(n_trials=200)
