"""信号生成模块 - 根据策略和因子产生交易信号"""

from dataclasses import dataclass
from loguru import logger


@dataclass
class Signal:
    code: str
    direction: str  # "buy" or "sell"
    price: float
    volume: int
    strategy: str
    reason: str
    confidence: float = 0.0


def generate_signals(strategy_name: str, quotes: list[dict], params: dict) -> list[Signal]:
    """根据策略名和行情数据生成信号"""
    generators = {
        "momentum_breakout": _momentum_breakout,
        "mean_reversion": _mean_reversion,
        "premium_arbitrage": _premium_arbitrage,
        "pair_trading": _pair_trading,
    }

    gen = generators.get(strategy_name)
    if gen is None:
        logger.warning(f"Unknown strategy: {strategy_name}")
        return []

    return gen(quotes, params)


def _momentum_breakout(quotes: list[dict], params: dict) -> list[Signal]:
    """动量突破策略

    逻辑:
    - 开盘30分钟内
    - 成交量 > 前5日平均量的 N 倍
    - 价格突破当日高点
    → 买入信号
    """
    signals = []
    vol_threshold = params.get("vol_threshold", 3.0)

    for q in quotes:
        code = q.get("code", "")
        price = q.get("price", 0)
        vol = q.get("vol", 0)
        open_price = q.get("open", 0)
        high = q.get("high", 0)

        if not all([code, price, vol, open_price]):
            continue

        # 简化逻辑: 价格突破开盘价 + 量能放大
        price_breakout = price > open_price * 1.01  # 涨超1%
        # 实际应与历史量比较，这里简化
        if price_breakout and vol > 0:
            signals.append(Signal(
                code=code,
                direction="buy",
                price=price,
                volume=10,  # 10张可转债
                strategy="momentum_breakout",
                reason=f"价格突破 {price:.3f} > 开盘 {open_price:.3f}",
                confidence=0.6,
            ))

    return signals


def _mean_reversion(quotes: list[dict], params: dict) -> list[Signal]:
    """均值回归策略

    逻辑:
    - 当前价偏离 VWAP 超过 N 个标准差 → 反向操作
    - 价格远低于 VWAP → 买入 (超卖反弹)
    - 价格远高于 VWAP → 卖出 (超买回落)
    """
    signals = []
    deviation_threshold = params.get("deviation_threshold", 0.02)  # 偏离2%

    for q in quotes:
        code = q.get("code", "")
        price = q.get("price", 0)
        vol = q.get("vol", 0)
        open_price = q.get("open", 0)
        high = q.get("high", 0)
        low = q.get("low", 0)

        if not all([code, price, open_price]):
            continue

        # 简化 VWAP: 用 (high + low + close) / 3 近似当前 VWAP
        vwap = (high + low + price) / 3 if high and low else open_price
        if vwap <= 0:
            continue

        deviation = (price - vwap) / vwap

        if deviation < -deviation_threshold:
            # 价格低于 VWAP 超过阈值 → 买入
            confidence = min(0.9, 0.5 + abs(deviation) * 10)
            signals.append(Signal(
                code=code,
                direction="buy",
                price=price,
                volume=10,
                strategy="mean_reversion",
                reason=f"超卖反弹 偏离VWAP {deviation:.2%}",
                confidence=confidence,
            ))
        elif deviation > deviation_threshold:
            # 价格高于 VWAP 超过阈值 → 卖出
            confidence = min(0.9, 0.5 + abs(deviation) * 10)
            signals.append(Signal(
                code=code,
                direction="sell",
                price=price,
                volume=10,
                strategy="mean_reversion",
                reason=f"超买回落 偏离VWAP {deviation:+.2%}",
                confidence=confidence,
            ))

    return signals


def _premium_arbitrage(quotes: list[dict], params: dict) -> list[Signal]:
    """转股溢价率套利

    逻辑:
    - 转股溢价率 < 阈值 → 买入低溢价可转债
    - 转股溢价率 < 0 (折价) → 高置信度买入
    - 需要 quotes 中包含 premium_rate 字段 (由行情层计算)
    """
    signals = []
    premium_threshold = params.get("premium_threshold", 0.05)
    min_price = params.get("min_price", 90)
    max_price = params.get("max_price", 130)

    for q in quotes:
        code = q.get("code", "")
        price = q.get("price", 0)
        premium_rate = q.get("premium_rate")  # 转股溢价率，由行情层注入

        if not all([code, price]):
            continue

        # 过滤价格区间 (避免高价债和低价问题债)
        if price < min_price or price > max_price:
            continue

        if premium_rate is None:
            # 无溢价率数据时用涨跌幅近似: 价格接近面值100且微跌 → 可能低溢价
            open_price = q.get("open", 0)
            if open_price and price < open_price * 0.995 and 95 <= price <= 105:
                signals.append(Signal(
                    code=code,
                    direction="buy",
                    price=price,
                    volume=10,
                    strategy="premium_arbitrage",
                    reason=f"面值附近低价债 {price:.3f}",
                    confidence=0.45,
                ))
            continue

        if premium_rate < 0:
            # 折价 → 高置信度
            signals.append(Signal(
                code=code,
                direction="buy",
                price=price,
                volume=20,
                strategy="premium_arbitrage",
                reason=f"折价套利 溢价率{premium_rate:.2%}",
                confidence=min(0.95, 0.7 + abs(premium_rate) * 5),
            ))
        elif premium_rate < premium_threshold:
            # 低溢价 → 中等置信度
            signals.append(Signal(
                code=code,
                direction="buy",
                price=price,
                volume=10,
                strategy="premium_arbitrage",
                reason=f"低溢价 {premium_rate:.2%} < {premium_threshold:.0%}",
                confidence=0.55,
            ))

    return signals


def _pair_trading(quotes: list[dict], params: dict) -> list[Signal]:
    """配对交易

    逻辑:
    - 同时接收正股和可转债行情
    - 计算可转债涨跌幅 vs 正股涨跌幅的偏差
    - 偏差过大 → 做多落后方 / 做空领先方
    - 需要 quotes 中包含 stock_change 字段 (正股涨跌幅)
    """
    signals = []
    spread_threshold = params.get("spread_threshold", 0.015)  # 价差偏离1.5%

    for q in quotes:
        code = q.get("code", "")
        price = q.get("price", 0)
        open_price = q.get("open", 0)
        stock_change = q.get("stock_change")  # 对应正股涨跌幅

        if not all([code, price, open_price]) or open_price <= 0:
            continue

        bond_change = (price - open_price) / open_price

        if stock_change is None:
            continue

        # 价差 = 可转债涨跌幅 - 正股涨跌幅
        spread = bond_change - stock_change

        if spread < -spread_threshold:
            # 可转债涨幅落后正股 → 买入可转债 (补涨预期)
            confidence = min(0.85, 0.5 + abs(spread) * 10)
            signals.append(Signal(
                code=code,
                direction="buy",
                price=price,
                volume=10,
                strategy="pair_trading",
                reason=f"债涨幅{bond_change:+.2%}落后股{stock_change:+.2%} 价差{spread:+.2%}",
                confidence=confidence,
            ))
        elif spread > spread_threshold:
            # 可转债涨幅领先正股 → 卖出可转债 (回落预期)
            confidence = min(0.85, 0.5 + abs(spread) * 10)
            signals.append(Signal(
                code=code,
                direction="sell",
                price=price,
                volume=10,
                strategy="pair_trading",
                reason=f"债涨幅{bond_change:+.2%}领先股{stock_change:+.2%} 价差{spread:+.2%}",
                confidence=confidence,
            ))

    return signals
