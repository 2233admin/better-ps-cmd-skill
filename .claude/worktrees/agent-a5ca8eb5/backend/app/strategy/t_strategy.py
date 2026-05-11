"""做T策略引擎 - 基于分时均价线的日内高抛低吸

三种模式:
- 正T: 持仓股回踩均价线下方买入，拉升到均价线上方卖出
- 反T: 持仓股冲高到均价线上方先卖，回落到均价线下方接回
- 半路T: 无底仓，急杀缩量企稳时抄底，反弹出局

核心指标:
- VWAP (成交量加权均价) 作为多空分界线
- 量比 (当前成交量 / 近N个tick平均量) 判断放量缩量
- 价格偏离率 (price / vwap - 1) 判断超买超卖
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger

from .signals import Signal


@dataclass
class TickData:
    """单个tick快照"""
    ts: float  # 时间戳
    price: float
    vol: int  # 累计成交量
    amount: float  # 累计成交额
    cur_vol: int  # 当笔成交量
    bid1: float = 0.0
    ask1: float = 0.0


@dataclass
class IntraDayState:
    """单只股票的日内状态"""
    code: str
    ticks: list[TickData] = field(default_factory=list)
    vwap: float = 0.0
    last_price: float = 0.0
    open_price: float = 0.0
    high: float = 0.0
    low: float = 999999.0
    last_close: float = 0.0

    # 最近tick的盘口
    bid1: float = 0.0
    ask1: float = 0.0

    # 做T状态
    t_buy_done: bool = False  # 今天已做过正T买入
    t_sell_done: bool = False  # 今天已做过反T卖出
    last_signal_ts: float = 0.0  # 上次信号时间，防连续触发
    t_buy_price: float = 0.0  # 正T买入价，用于计算卖出目标

    def update_vwap(self):
        """用累计成交额/成交量计算真实VWAP"""
        if not self.ticks:
            return
        latest = self.ticks[-1]
        if latest.vol > 0:
            self.vwap = latest.amount / latest.vol
        self.last_price = latest.price
        self.high = max(self.high, latest.price)
        self.low = min(self.low, latest.price)
        if not self.open_price and latest.price > 0:
            self.open_price = latest.price

    @property
    def deviation(self) -> float:
        """价格偏离VWAP的比率"""
        if self.vwap <= 0:
            return 0.0
        return (self.last_price - self.vwap) / self.vwap

    @property
    def vol_ratio(self) -> float:
        """最近tick的量比 (最近1笔 vs 最近20笔平均)"""
        if len(self.ticks) < 5:
            return 1.0
        window = self.ticks[-20:] if len(self.ticks) >= 20 else self.ticks
        avg_cur_vol = sum(t.cur_vol for t in window) / len(window)
        if avg_cur_vol <= 0:
            return 1.0
        return self.ticks[-1].cur_vol / avg_cur_vol

    @property
    def is_shrinking(self) -> bool:
        """量能萎缩 (量比 < 0.5)"""
        return self.vol_ratio < 0.5

    @property
    def is_surging(self) -> bool:
        """量能放大 (量比 > 2.0)"""
        return self.vol_ratio > 2.0

    @property
    def price_change(self) -> float:
        """涨跌幅"""
        if self.last_close <= 0:
            return 0.0
        return (self.last_price - self.last_close) / self.last_close

    @property
    def trend_strength(self) -> float:
        """趋势强度: 最近10个tick的价格斜率"""
        if len(self.ticks) < 10:
            return 0.0
        recent = self.ticks[-10:]
        prices = [t.price for t in recent]
        # 简单线性回归斜率
        n = len(prices)
        x_mean = (n - 1) / 2
        y_mean = sum(prices) / n
        numerator = sum((i - x_mean) * (p - y_mean) for i, p in enumerate(prices))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0 or y_mean == 0:
            return 0.0
        slope = numerator / denominator
        return slope / y_mean  # 归一化为百分比斜率


@dataclass
class TStrategyConfig:
    """做T策略参数"""
    # 正T参数
    long_t_buy_deviation: float = -0.012  # 跌破VWAP 1.2% 触发买入
    long_t_sell_deviation: float = 0.008  # 回到VWAP上方 0.8% 触发卖出
    long_t_vol_confirm: bool = True  # 买入时要求缩量确认

    # 反T参数
    short_t_sell_deviation: float = 0.015  # 冲高VWAP上方 1.5% 触发卖出
    short_t_buy_deviation: float = 0.003  # 回落到VWAP附近 0.3% 接回

    # 半路T参数
    scalp_drop_threshold: float = -0.03  # 急杀3%触发关注
    scalp_reversal_confirm: float = 0.005  # 反弹0.5%确认企稳
    scalp_target: float = 0.015  # 目标利润1.5%

    # 通用参数
    signal_cooldown: float = 120.0  # 同一只票信号冷却时间(秒)
    min_ticks: int = 30  # 至少积累30个tick才开始产生信号
    min_amount: float = 1e8  # 最低成交额1亿(过滤僵尸股)
    volume_per_signal: int = 100  # 每次信号的委托股数
    max_daily_t_count: int = 2  # 每只票每天最多做T次数


class TStrategy:
    """做T策略主类"""

    def __init__(self, config: TStrategyConfig | None = None):
        self.config = config or TStrategyConfig()
        self.states: dict[str, IntraDayState] = {}
        # 持仓信息 {code: volume}，由外部注入
        self.positions: dict[str, int] = {}
        # 做T模式 {code: "long_t" | "short_t" | "scalp"}
        self.modes: dict[str, str] = {}
        # 每日做T计数
        self.daily_t_count: dict[str, int] = defaultdict(int)
        # 半路T的关注列表 {code: 急杀最低价}
        self._scalp_watch: dict[str, float] = {}

    def set_positions(self, positions: dict[str, int]):
        """设置当前持仓"""
        self.positions = positions

    def set_targets(self, targets: list[dict]):
        """设置做T目标

        Args:
            targets: [{"code": "300502", "mode": "long_t", "volume": 100}, ...]
                mode: "long_t" (正T) | "short_t" (反T) | "scalp" (半路T)
        """
        for t in targets:
            code = t["code"]
            self.modes[code] = t.get("mode", "long_t")
            if "volume" in t:
                # 每只票可以单独设置委托量
                pass
        logger.info(f"T-strategy targets: {len(targets)} stocks, modes={self.modes}")

    def reset_daily(self):
        """每日重置"""
        self.states.clear()
        self.daily_t_count.clear()
        self._scalp_watch.clear()
        for code in self.modes:
            if code in self.states:
                self.states[code].t_buy_done = False
                self.states[code].t_sell_done = False
        logger.info("T-strategy daily reset")

    def on_quotes(self, quotes: list[dict]) -> list[Signal]:
        """处理行情推送，返回交易信号"""
        now = time.time()
        signals = []

        for q in quotes:
            code = q.get("code", "")
            if not code:
                continue

            # 只处理在做T目标列表中的票，或半路T模式下的全市场扫描
            is_target = code in self.modes
            has_scalp_mode = "scalp" in self.modes.values()

            if not is_target and not has_scalp_mode:
                continue

            # 更新tick数据
            state = self._update_state(code, q, now)
            if not state:
                continue

            # tick数不够，跳过
            if len(state.ticks) < self.config.min_ticks:
                continue

            # 信号冷却检查 (配对回程信号不受冷却限制)
            code_mode = self.modes.get(code)
            is_pending_return = (
                (state.t_buy_done and code_mode == "long_t")
                or (state.t_sell_done and code_mode == "short_t")
            )
            if not is_pending_return and now - state.last_signal_ts < self.config.signal_cooldown:
                continue

            # 日度做T次数限制
            if self.daily_t_count[code] >= self.config.max_daily_t_count:
                continue

            # 成交额过滤
            latest = state.ticks[-1]
            if latest.amount < self.config.min_amount:
                continue

            mode = self.modes.get(code)

            if mode == "long_t" and code in self.positions:
                sig = self._check_long_t(state, now)
                if sig:
                    signals.append(sig)

            elif mode == "short_t" and code in self.positions:
                sig = self._check_short_t(state, now)
                if sig:
                    signals.append(sig)

            elif mode == "scalp" or (has_scalp_mode and code not in self.modes):
                sig = self._check_scalp(state, now)
                if sig:
                    signals.append(sig)

        return signals

    def _update_state(self, code: str, q: dict, now: float) -> IntraDayState | None:
        """更新个股日内状态"""
        price = q.get("price", 0)
        if price <= 0:
            return None

        if code not in self.states:
            self.states[code] = IntraDayState(code=code)

        state = self.states[code]
        state.last_close = q.get("last_close", 0)

        tick = TickData(
            ts=now,
            price=price,
            vol=q.get("vol", 0),
            amount=q.get("amount", 0),
            cur_vol=q.get("cur_vol", 0),
            bid1=q.get("bid1", 0),
            ask1=q.get("ask1", 0),
        )
        state.ticks.append(tick)

        # 只保留最近600个tick (约5分钟 @500ms间隔)
        if len(state.ticks) > 600:
            state.ticks = state.ticks[-600:]

        state.bid1 = tick.bid1
        state.ask1 = tick.ask1
        state.update_vwap()
        return state

    def _check_long_t(self, state: IntraDayState, now: float) -> Signal | None:
        """正T检查: 跌破均价线买入，回升卖出

        流程:
        1. 价格跌到VWAP下方N% + 缩量 → 买入 (加仓)
        2. 价格回到VWAP上方M% → 卖出 (原仓位)
        """
        cfg = self.config
        code = state.code
        dev = state.deviation

        if not state.t_buy_done:
            # 阶段1: 寻找买点
            if dev <= cfg.long_t_buy_deviation:
                # 价格在均价线下方
                vol_ok = (not cfg.long_t_vol_confirm) or state.is_shrinking
                # 趋势开始走平或反转 (不追下跌中的刀)
                # REST 轮询场景 trend 接近0，放宽到 -0.002
                trend_ok = state.trend_strength > -0.002

                if vol_ok and trend_ok:
                    state.t_buy_done = True
                    state.t_buy_price = state.last_price
                    state.last_signal_ts = now
                    self.daily_t_count[code] += 1

                    logger.info(
                        f"[正T买入] {code} 价:{state.last_price:.2f} "
                        f"VWAP:{state.vwap:.2f} 偏离:{dev:.2%} "
                        f"量比:{state.vol_ratio:.1f} 趋势:{state.trend_strength:.4f}"
                    )
                    return Signal(
                        code=code,
                        direction="buy",
                        price=state.ask1 if state.ask1 > 0 else state.last_price,
                        volume=cfg.volume_per_signal,
                        strategy="t_long",
                        reason=f"正T买入 偏离VWAP {dev:.2%} 缩量企稳",
                        confidence=min(0.85, 0.6 + abs(dev) * 10),
                    )
        else:
            # 阶段2: 寻找卖点
            if dev >= cfg.long_t_sell_deviation:
                profit = 0.0
                if state.t_buy_price > 0:
                    profit = (state.last_price - state.t_buy_price) / state.t_buy_price

                state.t_buy_done = False
                state.last_signal_ts = now

                logger.info(
                    f"[正T卖出] {code} 价:{state.last_price:.2f} "
                    f"VWAP:{state.vwap:.2f} 偏离:{dev:.2%} "
                    f"T利润:{profit:.2%}"
                )
                return Signal(
                    code=code,
                    direction="sell",
                    price=state.bid1 if state.bid1 > 0 else state.last_price,
                    volume=cfg.volume_per_signal,
                    strategy="t_long",
                    reason=f"正T卖出 回升VWAP上方 {dev:.2%} 利润{profit:.2%}",
                    confidence=0.8,
                )

        return None

    def _check_short_t(self, state: IntraDayState, now: float) -> Signal | None:
        """反T检查: 冲高卖出，回落接回

        流程:
        1. 价格冲到VWAP上方N% + 放量 → 卖出 (减仓)
        2. 价格回到VWAP附近 → 买回
        """
        cfg = self.config
        code = state.code
        dev = state.deviation

        if not state.t_sell_done:
            # 阶段1: 寻找卖点
            if dev >= cfg.short_t_sell_deviation:
                # 放量冲高更可靠
                vol_ok = state.is_surging
                if vol_ok:
                    state.t_sell_done = True
                    state.t_buy_price = state.last_price  # 记录卖出价
                    state.last_signal_ts = now
                    self.daily_t_count[code] += 1

                    logger.info(
                        f"[反T卖出] {code} 价:{state.last_price:.2f} "
                        f"VWAP:{state.vwap:.2f} 偏离:{dev:.2%} "
                        f"量比:{state.vol_ratio:.1f}"
                    )
                    return Signal(
                        code=code,
                        direction="sell",
                        price=state.bid1 if state.bid1 > 0 else state.last_price,
                        volume=cfg.volume_per_signal,
                        strategy="t_short",
                        reason=f"反T卖出 冲高VWAP上方 {dev:.2%} 放量",
                        confidence=min(0.85, 0.6 + dev * 10),
                    )
        else:
            # 阶段2: 寻找买回点
            if dev <= cfg.short_t_buy_deviation:
                profit = 0.0
                if state.t_buy_price > 0:
                    profit = (state.t_buy_price - state.last_price) / state.t_buy_price

                state.t_sell_done = False
                state.last_signal_ts = now

                logger.info(
                    f"[反T买回] {code} 价:{state.last_price:.2f} "
                    f"VWAP:{state.vwap:.2f} 偏离:{dev:.2%} "
                    f"T利润:{profit:.2%}"
                )
                return Signal(
                    code=code,
                    direction="buy",
                    price=state.ask1 if state.ask1 > 0 else state.last_price,
                    volume=cfg.volume_per_signal,
                    strategy="t_short",
                    reason=f"反T买回 回落VWAP附近 {dev:.2%} 利润{profit:.2%}",
                    confidence=0.8,
                )

        return None

    def _check_scalp(self, state: IntraDayState, now: float) -> Signal | None:
        """半路T检查: 急杀缩量企稳抄底，反弹出局

        流程:
        1. 全市场扫描，发现急杀超过N% → 加入关注
        2. 关注标的缩量 + 价格止跌 → 买入
        3. 反弹到目标位 → 卖出
        """
        cfg = self.config
        code = state.code
        chg = state.price_change

        if code not in self._scalp_watch:
            # 扫描: 跌幅超过阈值 + 有足够成交额
            if chg <= cfg.scalp_drop_threshold:
                self._scalp_watch[code] = state.last_price
                logger.info(
                    f"[半路T关注] {code} 急杀 {chg:.2%} "
                    f"价:{state.last_price:.2f}"
                )
            return None

        watch_low = self._scalp_watch[code]

        # 更新最低价
        if state.last_price < watch_low:
            self._scalp_watch[code] = state.last_price
            return None

        # 检查反弹确认: 从最低点反弹超过阈值 + 缩量
        bounce = (state.last_price - watch_low) / watch_low if watch_low > 0 else 0

        if bounce >= cfg.scalp_reversal_confirm and state.is_shrinking:
            # 趋势走平确认
            if state.trend_strength > -0.0005:
                del self._scalp_watch[code]
                state.last_signal_ts = now
                self.daily_t_count[code] += 1

                logger.info(
                    f"[半路T买入] {code} 价:{state.last_price:.2f} "
                    f"日跌:{chg:.2%} 反弹:{bounce:.2%} "
                    f"量比:{state.vol_ratio:.1f}"
                )
                return Signal(
                    code=code,
                    direction="buy",
                    price=state.ask1 if state.ask1 > 0 else state.last_price,
                    volume=cfg.volume_per_signal,
                    strategy="t_scalp",
                    reason=f"半路T 急杀{chg:.2%}后缩量反弹{bounce:.2%}",
                    confidence=min(0.75, 0.5 + bounce * 10),
                )

        return None

    def get_status(self) -> dict:
        """获取策略状态"""
        active = {}
        for code, state in self.states.items():
            if code not in self.modes:
                continue
            active[code] = {
                "mode": self.modes.get(code, "unknown"),
                "price": state.last_price,
                "vwap": round(state.vwap, 2),
                "deviation": f"{state.deviation:.2%}",
                "vol_ratio": round(state.vol_ratio, 1),
                "trend": round(state.trend_strength, 4),
                "ticks": len(state.ticks),
                "t_buy_done": state.t_buy_done,
                "t_sell_done": state.t_sell_done,
                "daily_count": self.daily_t_count.get(code, 0),
            }
        return {
            "targets": len(self.modes),
            "watching_scalp": len(self._scalp_watch),
            "stocks": active,
        }


# 全局单例
_t_strategy: TStrategy | None = None


def get_t_strategy() -> TStrategy:
    global _t_strategy
    if _t_strategy is None:
        _t_strategy = TStrategy()
    return _t_strategy
