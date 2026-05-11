"""唯物主义市场监控器 - 持续扫描 + 声音告警 + 仓位监控

每5分钟全市场扫描，检测矛盾变化。
发现强信号时蜂鸣告警 + 写入信号日志。
同时监控现有仓位的盈亏状态。
"""

import asyncio
import json
import time
import winsound
from datetime import datetime
from pathlib import Path

from loguru import logger

from ..data.okx_client import get_okx_client
from .materialist_engine import MaterialistEngine, MarketCondition


SIGNAL_LOG = Path("D:/projects/quant-terminal/data/signals.jsonl")
SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)


def beep_alert(signal: str):
    """声音告警"""
    if signal == "LONG":
        # 三声高音 = 做多信号
        for _ in range(3):
            winsound.Beep(1200, 200)
            time.sleep(0.1)
    elif signal == "SHORT":
        # 两声低音 = 做空信号
        for _ in range(2):
            winsound.Beep(600, 300)
            time.sleep(0.1)
    else:
        # 一声中音 = 仓位告警
        winsound.Beep(900, 500)


class MarketMonitor:
    """市场监控器"""

    def __init__(self, capital: float = 50, leverage: int = 20):
        self.engine = MaterialistEngine(capital=capital, leverage=leverage)
        self.client = get_okx_client()
        self._running = False
        self._scan_count = 0
        self._signals_history: list[dict] = []

        # 上一次的信号状态，用于检测变化
        self._last_signals: dict[str, str] = {}

        # 仓位告警阈值
        self.pnl_alert_high = 5.0   # 浮盈超5U提醒考虑止盈
        self.pnl_alert_low = -2.0   # 浮亏超2U提醒

    def _check_positions(self) -> list[str]:
        """检查现有仓位状态"""
        alerts = []
        try:
            positions = self.client.get_positions()
            for p in positions:
                inst = p.get('instId', '')
                upl = float(p.get('upl', 0))
                pos = p.get('pos', '0')
                side = p.get('posSide', '')

                if upl > self.pnl_alert_high:
                    alerts.append(f"[PROFIT] {inst} {side} x{pos} upl={upl:+.2f}U - 考虑部分止盈")
                elif upl < self.pnl_alert_low:
                    alerts.append(f"[LOSS] {inst} {side} x{pos} upl={upl:+.2f}U - 注意风险")
        except Exception as e:
            logger.error(f"Check positions error: {e}")
        return alerts

    def _log_signal(self, mc: MarketCondition, plan: dict | None = None):
        """记录信号到日志文件"""
        entry = {
            "time": datetime.now().isoformat(),
            "pair": mc.pair,
            "signal": mc.signal,
            "confidence": round(mc.confidence, 2),
            "price": mc.price,
            "reason": mc.reason,
            "contradictions": mc.contradictions,
            "conditions": {
                "trend_4h": mc.trend_4h,
                "vwap_dev": round(mc.vwap_dev, 4),
                "amplitude": round(mc.amplitude, 4),
                "funding_rate": round(mc.funding_rate, 6),
                "taker_ratio": round(mc.taker_ratio, 2),
                "ls_ratio": round(mc.ls_ratio, 2),
                "pos_in_range": round(mc.pos_in_range, 2),
            },
        }
        if plan:
            entry["plan"] = plan

        with open(SIGNAL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        self._signals_history.append(entry)

    async def run(self, interval: int = 300, max_scans: int = 0):
        """主循环

        Args:
            interval: 扫描间隔(秒)，默认5分钟
            max_scans: 最大扫描次数，0=无限
        """
        self._running = True
        logger.info(f"Market Monitor started (interval={interval}s)")

        while self._running:
            try:
                self._scan_count += 1
                now = datetime.now().strftime("%H:%M:%S")

                # 1. 全市场扫描
                conditions = self.engine.scan_market()
                plans = self.engine.get_trading_plan()

                # 2. 检测新信号 / 信号变化
                new_signals = []
                for mc in conditions:
                    prev = self._last_signals.get(mc.pair, "WAIT")
                    if mc.signal != "WAIT" and mc.signal != prev:
                        new_signals.append(mc)
                        plan = next((p for p in plans if p["pair"] == mc.pair), None)
                        self._log_signal(mc, plan)

                    self._last_signals[mc.pair] = mc.signal

                # 3. 检查仓位
                pos_alerts = self._check_positions()

                # 4. 输出
                print(f"\n{'='*60}")
                print(f"[{now}] Scan #{self._scan_count}")
                print(f"{'='*60}")

                # 市场概览
                for mc in conditions:
                    flag = {"LONG": "+", "SHORT": "-", "WAIT": " "}.get(mc.signal, " ")
                    conf = f"{mc.confidence:.0%}" if mc.confidence > 0 else "  -"
                    print(f"  [{flag}] {mc.pair:<14} {mc.price:>10.4f}  "
                          f"{mc.change*100:>+5.1f}%  "
                          f"amp={mc.amplitude*100:>4.1f}%  "
                          f"taker={mc.taker_ratio:>4.1f}  "
                          f"conf={conf}")

                # 新信号告警
                if new_signals:
                    print(f"\n  >>> NEW SIGNALS <<<")
                    for mc in new_signals:
                        print(f"  !!! {mc.signal} {mc.pair} @ {mc.price}")
                        print(f"      Reason: {mc.reason}")
                        if mc.contradictions:
                            print(f"      Contradictions: {', '.join(mc.contradictions)}")
                        beep_alert(mc.signal)

                # 交易计划
                if plans:
                    print(f"\n  Active Plans:")
                    for p in plans:
                        print(f"    {p['signal']} {p['pair']} @ {p['price']}  "
                              f"conf={p['confidence']:.0%}  "
                              f"SL={p['levels']['stop_loss']}  "
                              f"TP={p['levels']['take_profit']}  "
                              f"margin={p['sizing']['margin']}U")

                # 仓位告警
                if pos_alerts:
                    print(f"\n  Position Alerts:")
                    for a in pos_alerts:
                        print(f"    {a}")
                        beep_alert("position")

                # 信号统计
                total_signals = len(self._signals_history)
                if total_signals > 0:
                    print(f"\n  Signal history: {total_signals} total")

                if max_scans > 0 and self._scan_count >= max_scans:
                    break

                await asyncio.sleep(interval)

            except Exception as e:
                logger.error(f"Monitor error: {e}")
                await asyncio.sleep(30)

        logger.info("Market Monitor stopped")

    def stop(self):
        self._running = False

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "scan_count": self._scan_count,
            "signals_total": len(self._signals_history),
            "recent_signals": self._signals_history[-5:],
            "current_signals": self._last_signals,
        }


async def run_monitor(interval: int = 300, capital: float = 50, scans: int = 0):
    """便捷启动函数"""
    monitor = MarketMonitor(capital=capital)
    await monitor.run(interval=interval, max_scans=scans)
    return monitor
