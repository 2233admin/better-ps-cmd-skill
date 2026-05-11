"""唯物主义交易引擎 - 从客观物质条件推导交易决策

核心哲学:
1. 物质决定意识 → 量价关系决定趋势，不猜方向
2. 矛盾运动 → 多空力量的对立统一产生交易机会
3. 量变到质变 → 成交量累积到临界点引发价格突破
4. 否定之否定 → 趋势 → 超买 → 回调 → 新趋势

策略矩阵 (根据物质条件自动选择):
- MOMENTUM: 量增价涨，顺势做多/做空
- REVERSAL: 极端偏离 + 量能衰竭 = 反转
- BREAKOUT: 缩量盘整后放量突破
- FUNDING_ARB: 资金费率极端时的均值回归
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum

import requests
from loguru import logger

from ..data.okx_client import get_okx_client
from ..data.okx_feed import fetch_usdt_tickers, okx_to_quotes


class Regime(str, Enum):
    """市场状态 (唯物判断，不含主观臆断)"""
    TREND_UP = "trend_up"        # 高点抬高 + 低点抬高
    TREND_DOWN = "trend_down"    # 高点降低 + 低点降低
    RANGE = "range"              # 震荡
    BREAKOUT = "breakout"        # 突破
    CAPITULATION = "capitulation"  # 恐慌抛售


@dataclass
class MarketCondition:
    """单品种的客观物质条件"""
    pair: str
    price: float = 0
    vwap_24h: float = 0
    vwap_dev: float = 0       # 偏离VWAP
    amplitude: float = 0      # 24h振幅
    change: float = 0         # 24h涨跌幅
    volume_usd: float = 0     # 24h成交额
    funding_rate: float = 0   # 资金费率
    basis: float = 0          # 期现价差
    taker_ratio: float = 1.0  # 主动买/卖比
    ls_ratio: float = 1.0     # 多空账户比
    regime: Regime = Regime.RANGE
    support: float = 0
    resistance: float = 0
    pos_in_range: float = 0.5  # 在区间中的位置 0=底 1=顶

    # K线结构
    trend_4h: str = ""   # UPTREND/DOWNTREND/RANGE
    vol_trend: str = ""  # INCREASING/DECREASING/STABLE

    # Kronos 预测 (第5因子)
    kronos_direction: str = ""    # up/down/neutral
    kronos_confidence: float = 0
    kronos_pred_change: float = 0

    # 矛盾分析
    contradictions: list[str] = field(default_factory=list)
    signal: str = ""      # LONG/SHORT/WAIT
    confidence: float = 0
    reason: str = ""


class MaterialistEngine:
    """唯物主义交易引擎 — 参数可从优化器加载"""

    def __init__(self, capital: float = 50, leverage: int = 20, risk_per_trade: float = 0.03):
        self.capital = capital
        self.leverage = leverage
        self.risk_per_trade = risk_per_trade
        self.client = get_okx_client()

        self.pairs = [
            'ETH-USDT', 'SOL-USDT', 'DOGE-USDT', 'SUI-USDT',
            'PEPE-USDT', 'HYPE-USDT', 'AVAX-USDT', 'LINK-USDT',
        ]
        self.conditions: dict[str, MarketCondition] = {}
        self._tick = 0
        self.params = self._load_params()
        self._kronos = None  # 延迟加载

    def _load_params(self) -> dict:
        """加载优化后的参数，无则返回默认值"""
        import json
        from pathlib import Path
        params_file = Path("D:/projects/quant-terminal/data/engine_params.json")
        defaults = {
            "trend_weight": 2, "taker_long_threshold": 1.5,
            "taker_short_threshold": 0.7, "taker_weight": 3,
            "funding_long_threshold": -0.01, "funding_short_threshold": 0.03,
            "funding_weight": 2, "pos_long_threshold": 0.2,
            "pos_short_threshold": 0.8, "pos_weight": 2,
            "contradiction_weight": 3, "signal_threshold": 5,
            "sl_amplitude_ratio": 0.3, "sl_min_pct": 0.01, "tp_ratio": 1.5,
        }
        if params_file.exists():
            try:
                loaded = json.loads(params_file.read_text())
                defaults.update(loaded)
                logger.info(f"[Engine] Loaded optimized params from {params_file}")
            except Exception:
                pass
        return defaults

    def _fetch_pair_data(self, pair: str, ticker: dict) -> MarketCondition | None:
        """获取单个交易对的全部数据（用于并行执行）"""
        mc = MarketCondition(pair=pair)
        try:
            mc.price = float(ticker.get('last', 0))
            sod = float(ticker.get('sodUtc8', 0)) or mc.price
            mc.vwap_24h = sod
            mc.vwap_dev = (mc.price - sod) / sod if sod > 0 else 0
            mc.amplitude = (float(ticker.get('high24h', 0)) - float(ticker.get('low24h', 0))) / sod if sod > 0 else 0
            mc.change = (mc.price - sod) / sod if sod > 0 else 0
            mc.volume_usd = float(ticker.get('volCcy24h', 0))
        except (ValueError, KeyError):
            return None

        swap_id = pair + '-SWAP'
        mc.funding_rate = self._get_funding_rate(swap_id)
        mc.basis = self._get_basis(pair, swap_id)
        mc.taker_ratio = self._get_taker_ratio(swap_id)
        mc.ls_ratio = self._get_ls_ratio(swap_id)
        self._analyze_kline_structure(mc)
        self._fetch_kronos_prediction(mc)
        self._analyze_contradictions(mc)
        self._make_decision(mc)
        return mc

    def scan_market(self) -> list[MarketCondition]:
        """全市场扫描，收集物质条件（并行）"""
        conditions = []

        # 1. 现货行情（批量获取）
        tickers = fetch_usdt_tickers(self.pairs)
        ticker_map = {t['instId']: t for t in tickers}

        # 2. 并行获取每个pair的详细数据
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {}
            for pair in self.pairs:
                t = ticker_map.get(pair)
                if not t:
                    continue
                futures[pool.submit(self._fetch_pair_data, pair, t)] = pair

            for fut in as_completed(futures):
                try:
                    mc = fut.result()
                    if mc:
                        conditions.append(mc)
                        self.conditions[mc.pair] = mc
                except Exception as e:
                    logger.warning(f"Scan {futures[fut]} failed: {e}")

        return conditions

    def _get_funding_rate(self, inst_id: str) -> float:
        try:
            r = requests.get('https://www.okx.com/api/v5/public/funding-rate',
                             params={'instId': inst_id}, timeout=5)
            d = r.json().get('data', [{}])[0]
            fr = d.get('fundingRate', '0')
            return float(fr) if fr else 0
        except Exception:
            return 0

    def _get_basis(self, spot_id: str, swap_id: str) -> float:
        try:
            r1 = requests.get('https://www.okx.com/api/v5/market/ticker',
                              params={'instId': spot_id}, timeout=5)
            r2 = requests.get('https://www.okx.com/api/v5/market/ticker',
                              params={'instId': swap_id}, timeout=5)
            sp = float(r1.json()['data'][0]['last'])
            fp = float(r2.json()['data'][0]['last'])
            return (fp - sp) / sp if sp > 0 else 0
        except Exception:
            return 0

    def _get_taker_ratio(self, inst_id: str) -> float:
        try:
            r = requests.get('https://www.okx.com/api/v5/rubik/stat/taker-volume-contract',
                             params={'instId': inst_id, 'period': '5m'}, timeout=5)
            d = r.json().get('data', [])
            if d:
                sell_vol, buy_vol = float(d[0][1]), float(d[0][2])
                return buy_vol / sell_vol if sell_vol > 0 else 1.0
            return 1.0
        except Exception:
            return 1.0

    def _get_ls_ratio(self, inst_id: str) -> float:
        try:
            r = requests.get(
                'https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader',
                params={'instId': inst_id, 'period': '5m'}, timeout=5)
            d = r.json().get('data', [])
            if d:
                return float(d[0][1])
            return 1.0
        except Exception:
            return 1.0

    def _analyze_kline_structure(self, mc: MarketCondition):
        """分析K线结构 - 4H级别的趋势判断"""
        try:
            r = requests.get('https://www.okx.com/api/v5/market/candles',
                             params={'instId': mc.pair, 'bar': '4H', 'limit': '12'}, timeout=5)
            data = r.json().get('data', [])
            if len(data) < 6:
                return

            closes = [float(d[4]) for d in reversed(data)]
            highs = [float(d[2]) for d in reversed(data)]
            lows = [float(d[3]) for d in reversed(data)]
            vols = [float(d[5]) for d in reversed(data)]

            # 趋势判断
            rh = highs[-4:]
            rl = lows[-4:]
            hh = all(rh[i] >= rh[i-1] for i in range(1, len(rh)))
            hl = all(rl[i] >= rl[i-1] for i in range(1, len(rl)))
            lh = all(rh[i] <= rh[i-1] for i in range(1, len(rh)))
            ll = all(rl[i] <= rl[i-1] for i in range(1, len(rl)))

            if hh and hl:
                mc.trend_4h = "UPTREND"
            elif lh and ll:
                mc.trend_4h = "DOWNTREND"
            else:
                mc.trend_4h = "RANGE"

            # 量能趋势
            vol_r = sum(vols[-3:]) / 3
            vol_p = sum(vols[-6:-3]) / 3
            mc.vol_trend = "INCREASING" if vol_r > vol_p * 1.2 else "DECREASING" if vol_r < vol_p * 0.8 else "STABLE"

            # 支撑阻力
            mc.support = min(lows[-6:])
            mc.resistance = max(highs[-6:])
            if mc.resistance > mc.support:
                mc.pos_in_range = (mc.price - mc.support) / (mc.resistance - mc.support)
        except Exception:
            pass

    def _fetch_kronos_prediction(self, mc: MarketCondition):
        """获取 Kronos K线预测作为第5因子"""
        try:
            if self._kronos is None:
                from ..ai.kronos_predictor import get_kronos
                self._kronos = get_kronos()

            if self._kronos.predictor is None:
                return

            pred = self._kronos.predict_pair(mc.pair, bar="4H", lookback=400, pred_len=12)
            mc.kronos_direction = pred.get("direction", "")
            mc.kronos_confidence = pred.get("confidence", 0)
            mc.kronos_pred_change = pred.get("predicted_change", 0)
        except Exception as e:
            logger.debug(f"Kronos predict {mc.pair} skipped: {e}")

    def _analyze_contradictions(self, mc: MarketCondition):
        """矛盾分析 - 找到对立统一中的主要矛盾"""
        mc.contradictions = []

        # 矛盾1: 价格下跌但主动买入增加 (底部吸筹)
        if mc.change < -0.02 and mc.taker_ratio > 1.3:
            mc.contradictions.append("PRICE_DOWN_BUT_BUYING")

        # 矛盾2: 价格上涨但空头资金费率 (逆势做空加仓)
        if mc.change > 0.02 and mc.funding_rate < -0.005:
            mc.contradictions.append("PRICE_UP_BUT_SHORTS_PAY")

        # 矛盾3: 多头拥挤但资金费率极低 (多头没加杠杆 = 现货买入)
        if mc.ls_ratio > 1.5 and abs(mc.funding_rate) < 0.001:
            mc.contradictions.append("LONGS_CROWDED_NO_LEVERAGE")

        # 矛盾4: 缩量下跌 (抛压衰竭)
        if mc.change < -0.03 and mc.vol_trend == "DECREASING":
            mc.contradictions.append("FALLING_ON_LOW_VOLUME")

        # 矛盾5: 放量上涨 (趋势确认)
        if mc.change > 0.02 and mc.vol_trend == "INCREASING":
            mc.contradictions.append("RISING_ON_VOLUME")

        # 矛盾6: 永续贴水 + 多头比例低 (空头过度)
        if mc.basis < -0.05 and mc.ls_ratio < 0.8:
            mc.contradictions.append("PERP_DISCOUNT_SHORTS_CROWDED")

        # 矛盾7: 接近支撑 + 主动买入
        if mc.pos_in_range < 0.15 and mc.taker_ratio > 1.2:
            mc.contradictions.append("AT_SUPPORT_WITH_BUYING")

        # 矛盾8: 接近阻力 + 主动卖出
        if mc.pos_in_range > 0.85 and mc.taker_ratio < 0.8:
            mc.contradictions.append("AT_RESISTANCE_WITH_SELLING")

    def _make_decision(self, mc: MarketCondition):
        """综合决策 - 参数化权重和阈值"""
        p = self.params
        long_score = 0
        short_score = 0
        reasons = []

        # === 趋势因子 ===
        if mc.trend_4h == "UPTREND":
            long_score += p["trend_weight"]
            reasons.append("4H上升趋势")
        elif mc.trend_4h == "DOWNTREND":
            short_score += p["trend_weight"]
            reasons.append("4H下降趋势")

        # === 量价因子 ===
        if mc.taker_ratio > p["taker_long_threshold"]:
            long_score += p["taker_weight"]
            reasons.append(f"主买压力{mc.taker_ratio:.1f}")
        elif mc.taker_ratio < p["taker_short_threshold"]:
            short_score += p["taker_weight"]
            reasons.append(f"主卖压力{mc.taker_ratio:.1f}")

        # === 资金费率因子 ===
        if mc.funding_rate < p["funding_long_threshold"]:
            long_score += p["funding_weight"]
            reasons.append(f"空头付费{mc.funding_rate*100:.3f}%")
        elif mc.funding_rate > p["funding_short_threshold"]:
            short_score += p["funding_weight"]
            reasons.append(f"多头付费{mc.funding_rate*100:.3f}%")

        # === 位置因子 ===
        if mc.pos_in_range < p["pos_long_threshold"]:
            long_score += p["pos_weight"]
            reasons.append(f"区间底部{mc.pos_in_range:.0%}")
        elif mc.pos_in_range > p["pos_short_threshold"]:
            short_score += p["pos_weight"]
            reasons.append(f"区间顶部{mc.pos_in_range:.0%}")

        # === 矛盾因子 ===
        cw = p["contradiction_weight"]
        for c in mc.contradictions:
            if c in ("PRICE_DOWN_BUT_BUYING", "FALLING_ON_LOW_VOLUME",
                      "AT_SUPPORT_WITH_BUYING", "PERP_DISCOUNT_SHORTS_CROWDED"):
                long_score += cw
                reasons.append(c)
            elif c in ("AT_RESISTANCE_WITH_SELLING", "PRICE_UP_BUT_SHORTS_PAY"):
                short_score += cw
                reasons.append(c)
            elif c == "RISING_ON_VOLUME":
                long_score += cw * 0.67
                reasons.append(c)

        # === Kronos 预测因子 (第5因子) ===
        kronos_weight = p.get("kronos_weight", 2.5)
        if mc.kronos_direction == "up" and mc.kronos_confidence > 0.5:
            long_score += kronos_weight * mc.kronos_confidence
            reasons.append(f"Kronos看涨{mc.kronos_confidence:.0%}")
        elif mc.kronos_direction == "down" and mc.kronos_confidence > 0.5:
            short_score += kronos_weight * mc.kronos_confidence
            reasons.append(f"Kronos看跌{mc.kronos_confidence:.0%}")

        # Kronos 与趋势矛盾检测
        if mc.kronos_direction == "up" and mc.trend_4h == "DOWNTREND":
            mc.contradictions.append("KRONOS_BULLISH_VS_DOWNTREND")
        elif mc.kronos_direction == "down" and mc.trend_4h == "UPTREND":
            mc.contradictions.append("KRONOS_BEARISH_VS_UPTREND")

        # === 决策 ===
        total = long_score + short_score
        threshold = p["signal_threshold"]
        if total == 0:
            mc.signal = "WAIT"
            mc.confidence = 0
            mc.reason = "无明显信号"
            return

        if long_score > short_score and long_score >= threshold:
            mc.signal = "LONG"
            mc.confidence = min(0.9, long_score / (total + 5))
            mc.reason = " | ".join(reasons)
        elif short_score > long_score and short_score >= threshold:
            mc.signal = "SHORT"
            mc.confidence = min(0.9, short_score / (total + 5))
            mc.reason = " | ".join(reasons)
        else:
            mc.signal = "WAIT"
            mc.confidence = 0
            mc.reason = f"多空均衡 L={long_score:.1f} S={short_score:.1f}"

    def calc_position_size(self, mc: MarketCondition) -> dict:
        """计算仓位大小 (风险固定法，参数化)"""
        p = self.params
        risk_amount = self.capital * self.risk_per_trade
        stop_loss_pct = mc.amplitude * p["sl_amplitude_ratio"]
        stop_loss_pct = max(stop_loss_pct, p["sl_min_pct"])

        notional = risk_amount / stop_loss_pct
        margin = notional / self.leverage
        margin = min(margin, self.capital * 0.2)
        notional = margin * self.leverage

        return {
            "notional": round(notional, 2),
            "margin": round(margin, 2),
            "stop_loss_pct": round(stop_loss_pct * 100, 2),
            "take_profit_pct": round(stop_loss_pct * p["tp_ratio"] * 100, 2),
        }

    def get_trading_plan(self) -> list[dict]:
        """生成交易计划"""
        conditions = self.scan_market()
        plans = []

        for mc in conditions:
            if mc.signal == "WAIT":
                continue

            sizing = self.calc_position_size(mc)

            plan = {
                "pair": mc.pair,
                "signal": mc.signal,
                "confidence": round(mc.confidence, 2),
                "price": mc.price,
                "reason": mc.reason,
                "contradictions": mc.contradictions,

                # 市场条件
                "conditions": {
                    "trend_4h": mc.trend_4h,
                    "vwap_dev": f"{mc.vwap_dev*100:+.2f}%",
                    "amplitude": f"{mc.amplitude*100:.1f}%",
                    "funding_rate": f"{mc.funding_rate*100:+.4f}%",
                    "taker_ratio": round(mc.taker_ratio, 2),
                    "ls_ratio": round(mc.ls_ratio, 2),
                    "pos_in_range": f"{mc.pos_in_range:.0%}",
                    "vol_trend": mc.vol_trend,
                },

                # 仓位管理
                "sizing": sizing,

                # 关键价位
                "levels": {
                    "support": mc.support,
                    "resistance": mc.resistance,
                    "stop_loss": round(mc.price * (1 - sizing["stop_loss_pct"]/100), 4)
                                if mc.signal == "LONG"
                                else round(mc.price * (1 + sizing["stop_loss_pct"]/100), 4),
                    "take_profit": round(mc.price * (1 + sizing["take_profit_pct"]/100), 4)
                                   if mc.signal == "LONG"
                                   else round(mc.price * (1 - sizing["take_profit_pct"]/100), 4),
                },
            }
            plans.append(plan)

        # 按信心度排序
        plans.sort(key=lambda x: x["confidence"], reverse=True)
        return plans

    def print_analysis(self):
        """打印完整分析"""
        conditions = self.scan_market()

        print("=" * 80)
        print("唯物主义市场分析")
        print("=" * 80)

        for mc in conditions:
            flag = {"LONG": "[LONG]", "SHORT": "[SHORT]", "WAIT": "[WAIT]"}.get(mc.signal, "?")
            print(f"\n{flag} {mc.pair} @ {mc.price}")
            print(f"  趋势: {mc.trend_4h:<12} 振幅: {mc.amplitude*100:.1f}%  "
                  f"涨跌: {mc.change*100:+.1f}%  位置: {mc.pos_in_range:.0%}")
            print(f"  资金费率: {mc.funding_rate*100:+.4f}%  "
                  f"主买/卖: {mc.taker_ratio:.2f}  "
                  f"多/空比: {mc.ls_ratio:.2f}  "
                  f"期现差: {mc.basis*100:+.4f}%")
            if mc.contradictions:
                print(f"  矛盾: {', '.join(mc.contradictions)}")
            print(f"  信号: {mc.signal} (conf={mc.confidence:.0%})  {mc.reason}")

        # 交易计划
        plans = self.get_trading_plan()
        if plans:
            print("\n" + "=" * 80)
            print(f"交易计划 (资金={self.capital}U 杠杆={self.leverage}x)")
            print("=" * 80)
            for p in plans:
                print(f"\n>>> {p['signal']} {p['pair']} @ {p['price']}")
                print(f"    信心: {p['confidence']:.0%}  原因: {p['reason']}")
                print(f"    仓位: {p['sizing']['notional']}U名义 / {p['sizing']['margin']}U保证金")
                print(f"    止损: {p['levels']['stop_loss']} ({p['sizing']['stop_loss_pct']}%)")
                print(f"    止盈: {p['levels']['take_profit']} ({p['sizing']['take_profit_pct']}%)")
                print(f"    支撑: {p['levels']['support']}  阻力: {p['levels']['resistance']}")
        else:
            print("\n当前无明确交易信号，等待矛盾激化。")
