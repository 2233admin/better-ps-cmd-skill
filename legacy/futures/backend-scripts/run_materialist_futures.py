#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
唯物动力学期货引擎 (Materialist Futures Engine)

核心哲学应用到期货市场:
1. 物质决定意识 → 持仓量+成交量+价格=市场本质，不预测只跟随
2. 矛盾运动 → 多空增仓对峙产生波动，减仓释放能量
3. 量变到质变 → 持仓累积到临界点后趋势爆发
4. 否定之否定 → 趋势中的回调是健康的，不是反转

期货市场特有因子:
- 持仓量变化 (Open Interest Delta)
- 仓差分析 (多空增减仓)
- 基差/价差 (期货vs现货)
- 主力合约换月规律
"""

import sys
sys.path.insert(0, 'C:/Users/Administrator/quant-terminal/backend')

import polars as pl
import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional
from datetime import datetime

from app.data.futures_feed import load_futures_for_backtest, fetch_and_save_futures_data
from app.strategy.factors import calc_all_factors


class MarketRegime(str, Enum):
    """市场状态 - 唯物判断"""
    ACCUMULATION = "accumulation"      # 吸筹: 缩量震荡+持仓增加
    MARKUP = "markup"                  # 拉升: 量价齐升+持仓增加
    DISTRIBUTION = "distribution"      # 派发: 放量滞涨+持仓减少
    MARKDOWN = "markdown"              # 下跌: 量价齐跌+持仓减少
    REACCUMULATION = "reaccumulation"  # 再吸筹: 回调缩量+持仓企稳


@dataclass
class FuturesMaterialCondition:
    """期货合约的客观物质条件"""
    contract: str
    timestamp: datetime

    # 价格物质
    open: float = 0
    high: float = 0
    low: float = 0
    close: float = 0
    vwap: float = 0           # 成交量加权均价
    vwap_deviation: float = 0  # 偏离VWAP程度

    # 成交量物质
    volume: int = 0
    volume_ma: float = 0      # 均量
    volume_ratio: float = 0   # 量比

    # 持仓量物质 (期货特有！)
    open_interest: int = 0
    oi_change: int = 0        # 持仓变化
    oi_ma: float = 0          # 持仓均值
    oi_velocity: float = 0    # 持仓变化速度

    # 多空力量物质
    bid_volume: int = 0       # 买盘量
    ask_volume: int = 0       # 卖盘量
    bid_ask_ratio: float = 1.0  # 买卖比

    # 趋势物质
    trend_short: str = ""     # 短期趋势 (5日)
    trend_medium: str = ""    # 中期趋势 (20日)
    trend_long: str = ""      # 长期趋势 (60日)

    # 位置物质
    support_level: float = 0
    resistance_level: float = 0
    position_in_range: float = 0.5  # 0=底部, 1=顶部

    # 矛盾分析
    contradictions: List[str] = field(default_factory=list)
    dominant_force: str = ""  # BULL/BEAR/BALANCE

    # 决策输出
    regime: MarketRegime = MarketRegime.ACCUMULATION
    signal: str = "WAIT"      # LONG/SHORT/WAIT
    confidence: float = 0
    reason: str = ""


class MaterialistFuturesEngine:
    """
    唯物动力学期货引擎

    分析框架:
    1. 量-价-仓三维分析
    2. 矛盾识别 (8种典型矛盾)
    3. 阶段判断 (威科夫理论本土化)
    4. 信号生成 (多因子加权)
    """

    def __init__(self, params: dict = None):
        self.params = params or self._default_params()

    def _default_params(self) -> dict:
        """默认参数 - 经过回测优化"""
        return {
            # 趋势权重
            "trend_short_weight": 1,
            "trend_medium_weight": 2,
            "trend_long_weight": 1,

            # 成交量权重
            "volume_breakout_threshold": 1.5,  # 量比突破阈值
            "volume_weight": 2,

            # 持仓量权重 (期货特有！)
            "oi_increase_weight": 3,      # 增仓加分
            "oi_decrease_weight": 1,      # 减仓加分
            "oi_velocity_threshold": 0.05,  # 持仓变化速度阈值

            # 位置权重
            "support_distance_threshold": 0.02,  # 距离支撑2%
            "resistance_distance_threshold": 0.02,
            "position_weight": 2,

            # 矛盾权重
            "contradiction_weight": 3,

            # 信号阈值
            "signal_threshold": 5,

            # 风控参数
            "stop_loss_atr_ratio": 2.0,
            "take_profit_atr_ratio": 3.0,
        }

    def analyze_contract(self, df: pl.DataFrame, contract: str) -> FuturesMaterialCondition:
        """分析单个合约的物质条件"""
        if df.is_empty():
            return FuturesMaterialCondition(contract=contract, timestamp=datetime.now())

        # 获取最后一行的数据（转为numpy标量）
        last_idx = len(df) - 1

        def get_value(col, default=0):
            if col in df.columns:
                val = df[last_idx, col]
                # 转换为标量
                if val is None:
                    return default
                if hasattr(val, 'item'):
                    return val.item()
                return val
            return default

        timestamp = get_value('date') or get_value('datetime') or datetime.now()
        if not isinstance(timestamp, datetime):
            timestamp = datetime.now()

        mc = FuturesMaterialCondition(
            contract=contract,
            timestamp=timestamp,
            open=float(get_value('open', 0)),
            high=float(get_value('high', 0)),
            low=float(get_value('low', 0)),
            close=float(get_value('close', 0)),
            volume=int(get_value('volume', 0)),
            open_interest=int(get_value('open_interest', 0)),
        )

        # 计算衍生指标
        self._calculate_indicators(df, mc)

        # 趋势分析
        self._analyze_trend(df, mc)

        # 位置分析
        self._analyze_position(df, mc)

        # 矛盾分析 (唯物核心！)
        self._analyze_contradictions(df, mc)

        # 阶段判断 (威科夫)
        self._determine_regime(df, mc)

        # 生成信号
        self._generate_signal(mc)

        return mc

    def _calculate_indicators(self, df: pl.DataFrame, mc: FuturesMaterialCondition):
        """计算技术指标"""
        closes = df['close'].to_numpy()
        volumes = df['volume'].to_numpy()

        # VWAP
        typical = ((df['high'] + df['low'] + df['close']) / 3).to_numpy()
        vwap = np.cumsum(typical * volumes) / np.cumsum(volumes)
        mc.vwap = float(vwap[-1]) if len(vwap) > 0 else float(mc.close)
        mc.vwap_deviation = (float(mc.close) - mc.vwap) / mc.vwap if mc.vwap > 0 else 0

        # 成交量分析
        mc.volume_ma = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))
        mc.volume_ratio = float(mc.volume) / mc.volume_ma if mc.volume_ma > 0 else 1.0

        # 持仓量分析 (期货特有)
        if 'open_interest' in df.columns:
            oi = df['open_interest'].to_numpy()
            mc.oi_ma = float(np.mean(oi[-20:])) if len(oi) >= 20 else float(np.mean(oi))
            mc.oi_change = float(oi[-1] - oi[-2]) if len(oi) >= 2 else 0
            mc.oi_velocity = mc.oi_change / mc.oi_ma if mc.oi_ma > 0 else 0

    def _analyze_trend(self, df: pl.DataFrame, mc: FuturesMaterialCondition):
        """趋势分析 - 多时间框架"""
        closes = df['close'].to_numpy()

        # 短期趋势 (5周期)
        if len(closes) >= 5:
            ma5 = float(np.mean(closes[-5:]))
            mc.trend_short = "UP" if float(closes[-1]) > ma5 else "DOWN"

        # 中期趋势 (20周期)
        if len(closes) >= 20:
            ma20 = float(np.mean(closes[-20:]))
            mc.trend_medium = "UP" if float(closes[-1]) > ma20 else "DOWN"

            # 判断是否多头排列
            if len(closes) >= 60:
                ma60 = float(np.mean(closes[-60:]))
                mc.trend_long = "UP" if float(closes[-1]) > ma60 else "DOWN"

    def _analyze_position(self, df: pl.DataFrame, mc: FuturesMaterialCondition):
        """位置分析 - 支撑阻力"""
        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()

        # 近期高低点作为支撑阻力
        lookback = min(20, len(highs))
        mc.resistance_level = float(np.max(highs[-lookback:]))
        mc.support_level = float(np.min(lows[-lookback:]))

        # 在区间中的位置
        range_size = mc.resistance_level - mc.support_level
        if range_size > 0:
            mc.position_in_range = (float(mc.close) - mc.support_level) / range_size
        else:
            mc.position_in_range = 0.5

    def _analyze_contradictions(self, df: pl.DataFrame, mc: FuturesMaterialCondition):
        """
        矛盾分析 - 唯物核心

        期货市场8大矛盾:
        """
        mc.contradictions = []

        # 矛盾1: 价格上涨但持仓减少 (多头出货)
        if mc.close > mc.vwap and mc.oi_change < 0:
            mc.contradictions.append("PRICE_UP_OI_DOWN")

        # 矛盾2: 价格下跌但持仓增加 (空头加仓/多头抵抗)
        if mc.close < mc.vwap and mc.oi_change > 0:
            mc.contradictions.append("PRICE_DOWN_OI_UP")

        # 矛盾3: 放量滞涨 (派发信号)
        if mc.volume_ratio > 1.5 and abs(mc.close - mc.open) / mc.open < 0.005:
            mc.contradictions.append("VOLUME_UP_PRICE_STALL")

        # 矛盾4: 缩量下跌 (抛压衰竭)
        if mc.volume_ratio < 0.8 and mc.close < mc.open:
            mc.contradictions.append("VOLUME_DOWN_PRICE_FALL")

        # 矛盾5: 持仓大增但价格不动 (对峙激烈，即将爆发)
        if abs(mc.oi_velocity) > 0.05 and abs(mc.vwap_deviation) < 0.01:
            mc.contradictions.append("OI_SURGE_PRICE_STABLE")

        # 矛盾6: 突破阻力但缩量 (假突破)
        if mc.close > mc.resistance_level * 0.99 and mc.volume_ratio < 1.0:
            mc.contradictions.append("BREAKOUT_LOW_VOLUME")

        # 矛盾7: 接近支撑且放量 (支撑有效)
        if mc.position_in_range < 0.1 and mc.volume_ratio > 1.2:
            mc.contradictions.append("AT_SUPPORT_WITH_VOLUME")

        # 矛盾8: 高位增仓 (顶部对峙)
        if mc.position_in_range > 0.8 and mc.oi_change > 0:
            mc.contradictions.append("HIGH_POSITION_OI_UP")

    def _determine_regime(self, df: pl.DataFrame, mc: FuturesMaterialCondition):
        """判断市场阶段 (威科夫理论)"""

        # 吸筹阶段: 低位+缩量+持仓增加
        if mc.position_in_range < 0.3 and mc.volume_ratio < 1.0 and mc.oi_change > 0:
            mc.regime = MarketRegime.ACCUMULATION

        # 拉升阶段: 上涨+增仓+量价齐升
        elif mc.trend_medium == "UP" and mc.oi_change > 0 and mc.volume_ratio > 1.0:
            mc.regime = MarketRegime.MARKUP

        # 派发阶段: 高位+滞涨+持仓减少
        elif mc.position_in_range > 0.7 and mc.volume_ratio > 1.2 and mc.oi_change < 0:
            mc.regime = MarketRegime.DISTRIBUTION

        # 下跌阶段: 下跌+减仓
        elif mc.trend_medium == "DOWN" and mc.oi_change < 0:
            mc.regime = MarketRegime.MARKDOWN

        # 再吸筹: 回调+缩量+持仓企稳
        elif mc.trend_short == "DOWN" and mc.volume_ratio < 0.8 and abs(mc.oi_change) < mc.oi_ma * 0.01:
            mc.regime = MarketRegime.REACCUMULATION

        else:
            mc.regime = MarketRegime.ACCUMULATION

    def _generate_signal(self, mc: FuturesMaterialCondition):
        """生成交易信号"""
        p = self.params
        long_score = 0
        short_score = 0
        reasons = []

        # === 趋势因子 ===
        if mc.trend_medium == "UP":
            long_score += p["trend_medium_weight"]
            reasons.append("中期上升")
        elif mc.trend_medium == "DOWN":
            short_score += p["trend_medium_weight"]
            reasons.append("中期下降")

        # === 成交量因子 ===
        if mc.volume_ratio > p["volume_breakout_threshold"]:
            if mc.close > mc.open:  # 阳线放量
                long_score += p["volume_weight"]
                reasons.append(f"放量上涨({mc.volume_ratio:.1f})")
            else:  # 阴线放量
                short_score += p["volume_weight"]
                reasons.append(f"放量下跌({mc.volume_ratio:.1f})")

        # === 持仓量因子 (期货核心！) ===
        if mc.oi_change > 0:  # 增仓
            if mc.close > mc.open:  # 增仓上涨 = 多头主动
                long_score += p["oi_increase_weight"]
                reasons.append("增仓上行")
            else:  # 增仓下跌 = 空头主动
                short_score += p["oi_increase_weight"]
                reasons.append("增仓下行")
        else:  # 减仓
            if mc.close > mc.open:  # 减仓上涨 = 空头回补
                long_score += p["oi_decrease_weight"] * 0.5
                reasons.append("减仓反弹")
            else:  # 减仓下跌 = 多头止损
                short_score += p["oi_decrease_weight"] * 0.5
                reasons.append("减仓回落")

        # === 位置因子 ===
        if mc.position_in_range < 0.2:  # 接近支撑
            long_score += p["position_weight"]
            reasons.append("区间底部")
        elif mc.position_in_range > 0.8:  # 接近阻力
            short_score += p["position_weight"]
            reasons.append("区间顶部")

        # === 矛盾因子 ===
        cw = p["contradiction_weight"]
        for c in mc.contradictions:
            if c in ["PRICE_DOWN_OI_UP", "VOLUME_DOWN_PRICE_FALL", "AT_SUPPORT_WITH_VOLUME"]:
                long_score += cw
                reasons.append(c)
            elif c in ["PRICE_UP_OI_DOWN", "VOLUME_UP_PRICE_STALL", "HIGH_POSITION_OI_UP"]:
                short_score += cw
                reasons.append(c)

        # === VWAP偏离 ===
        if mc.vwap_deviation < -0.02:  # 超卖
            long_score += 1
            reasons.append("超卖")
        elif mc.vwap_deviation > 0.02:  # 超买
            short_score += 1
            reasons.append("超买")

        # === 决策 ===
        threshold = p["signal_threshold"]

        if long_score > short_score and long_score >= threshold:
            mc.signal = "LONG"
            mc.confidence = min(0.95, long_score / 10)
            mc.reason = " | ".join(reasons[:5])  # 取前5个原因
            mc.dominant_force = "BULL"
        elif short_score > long_score and short_score >= threshold:
            mc.signal = "SHORT"
            mc.confidence = min(0.95, short_score / 10)
            mc.reason = " | ".join(reasons[:5])
            mc.dominant_force = "BEAR"
        else:
            mc.signal = "WAIT"
            mc.confidence = 0
            mc.reason = f"多空均衡 L={long_score:.1f} S={short_score:.1f}"
            mc.dominant_force = "BALANCE"

    def generate_signals_for_backtest(self, df: pl.DataFrame) -> pl.DataFrame:
        """为回测生成信号序列"""
        signals = []

        # 滚动窗口分析
        for i in range(60, len(df)):
            window = df.slice(0, i+1)
            contract = df[0, 'code'] if 'code' in df.columns else "UNKNOWN"

            mc = self.analyze_contract(window, contract)
            row = df.slice(i, 1)
            signals.append({
                'date': row[0, 'date'] if 'date' in row.columns else row[0, 'datetime'],
                'signal': 1 if mc.signal == "LONG" else -1 if mc.signal == "SHORT" else 0,
                'confidence': mc.confidence,
                'regime': mc.regime.value,
                'reason': mc.reason,
            })

        # 前60天无信号
        for i in range(60):
            row = df.slice(i, 1)
            signals.insert(0, {
                'date': row[0, 'date'] if 'date' in row.columns else row[0, 'datetime'],
                'signal': 0,
                'confidence': 0,
                'regime': 'unknown',
                'reason': 'warmup',
            })

        return pl.DataFrame(signals)

    def print_analysis(self, mc: FuturesMaterialCondition):
        """打印分析结果"""
        print(f"\n【{mc.contract} 唯物分析】")
        print(f"时间: {mc.timestamp}")
        print(f"价格: O={mc.open} H={mc.high} L={mc.low} C={mc.close}")
        print(f"VWAP: {mc.vwap:.2f} (偏离: {mc.vwap_deviation*100:+.2f}%)")
        print(f"成交: {mc.volume:,} (量比: {mc.volume_ratio:.2f})")
        print(f"持仓: {mc.open_interest:,} (变化: {mc.oi_change:+,})")
        print(f"趋势: 短={mc.trend_short} 中={mc.trend_medium} 长={mc.trend_long}")
        print(f"位置: {mc.position_in_range*100:.0f}% (支撑: {mc.support_level} 阻力: {mc.resistance_level})")
        print(f"阶段: {mc.regime.value}")
        print(f"矛盾: {', '.join(mc.contradictions) if mc.contradictions else '无'}")
        print(f"信号: [{mc.signal}] 信心={mc.confidence*100:.0f}% {mc.reason}")


def run_materialist_backtest(code: str = "IF0"):
    """运行唯物动力学回测"""
    print("=" * 70)
    print("唯物动力学期货回测")
    print("=" * 70)

    # 加载数据
    print(f"\n[*] 加载 {code} 数据...")
    fetch_and_save_futures_data(code, 'daily', start_date='20220101', end_date='20250328')
    data = load_futures_for_backtest(code, 'daily')

    if data.is_empty():
        print("[-] 无数据")
        return

    print(f"[*] 数据: {len(data)} 条")

    # 创建引擎
    engine = MaterialistFuturesEngine()

    # 分析最新数据
    latest_mc = engine.analyze_contract(data, code)
    engine.print_analysis(latest_mc)

    # 生成回测信号
    print("\n[*] 生成回测信号...")
    signals_df = engine.generate_signals_for_backtest(data)

    # 合并信号和数据
    data_with_signals = data.join(signals_df, on='date', how='left')

    # 统计
    long_count = signals_df.filter(pl.col('signal') == 1).shape[0]
    short_count = signals_df.filter(pl.col('signal') == -1).shape[0]
    print(f"[*] 做多信号: {long_count} 次")
    print(f"[*] 做空信号: {short_count} 次")

    # 运行回测
    from app.strategy.backtest import VectorBacktester

    backtester = VectorBacktester(
        initial_capital=1_000_000,
        commission=0.0001,
        slippage=0.0002
    )

    result = backtester.run(data, data_with_signals)

    print("\n" + "=" * 70)
    print("回测结果")
    print("=" * 70)
    print(f"总收益率:   {result.total_return:+.2%}")
    print(f"年化收益:   {result.annual_return:+.2%}")
    print(f"最大回撤:   {result.max_drawdown:.2%}")
    print(f"夏普比率:   {result.sharpe_ratio:.2f}")
    print(f"交易次数:   {result.total_trades}")
    print(f"胜率:       {result.win_rate:.2%}")
    print(f"最终资金:   {result.total_return*1000000+1000000:,.2f}")
    print("=" * 70)

    return result


if __name__ == "__main__":
    # 测试多个品种
    for code in ['IF0', 'IC0', 'IH0']:
        run_materialist_backtest(code)
        print("\n")
