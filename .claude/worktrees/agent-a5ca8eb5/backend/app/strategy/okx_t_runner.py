"""OKX 加密货币做T实时运行器

将 OKX 行情 → GPU因子引擎 → T策略信号 → OKX交易执行 串联成闭环。
支持 paper (模拟) 和 okx (实盘) 两种模式。
"""

import asyncio
import time

from loguru import logger

from ..data.okx_feed import fetch_usdt_tickers, okx_to_quotes
from ..data.okx_client import get_okx_client
from ..strategy.t_strategy import TStrategy, TStrategyConfig
from ..trade.executor import OrderExecutor


# 加密货币做T的默认参数 (波动更大，阈值放宽)
CRYPTO_T_CONFIG = TStrategyConfig(
    # 正T: 跌破VWAP 1.5%买入，回升0.8%卖出 (VWAP是24h均价，偏离天然大)
    long_t_buy_deviation=-0.015,
    long_t_sell_deviation=0.008,
    long_t_vol_confirm=False,  # REST轮询无有效量比

    # 反T: 冲高VWAP上方2%卖出，回落到0.3%接回
    short_t_sell_deviation=0.02,
    short_t_buy_deviation=0.003,

    # 半路T: 急杀4%关注，反弹0.8%确认，目标1.5%
    scalp_drop_threshold=-0.04,
    scalp_reversal_confirm=0.008,
    scalp_target=0.015,

    # 通用
    signal_cooldown=60.0,
    min_ticks=10,  # 10个tick (~20秒)
    min_amount=0,  # 加密不用成交额过滤
    volume_per_signal=1,
    max_daily_t_count=10,
)


class OKXTRunner:
    """OKX 做T实时运行器"""

    def __init__(
        self,
        targets: list[dict],
        mode: str = "paper",
        config: TStrategyConfig | None = None,
        interval: float = 2.0,
    ):
        """
        Args:
            targets: [{"code": "BTC-USDT", "mode": "long_t", "volume": 1}, ...]
            mode: "paper" (模拟) 或 "okx" (实盘)
            config: T策略参数，None=使用加密货币默认参数
            interval: 行情拉取间隔(秒)
        """
        self.targets = targets
        self.interval = interval
        self.config = config or CRYPTO_T_CONFIG
        self.t_strategy = TStrategy(self.config)
        self.executor = OrderExecutor(mode=mode)
        # 加密货币24h交易，放开时间限制和金额限制
        self.executor.risk_manager.force_close_time = "23:59"
        self.executor.risk_manager.max_order_amount = 1_000_000
        self._running = False
        self._tick_count = 0
        self._signals_log: list[dict] = []

        # 设置做T目标
        self.t_strategy.set_targets(targets)

        # 提取目标交易对
        self.pairs = [t["code"] for t in targets]

        # 如果有持仓信息，同步
        self._sync_positions()

    def _sync_positions(self):
        """从 OKX 同步真实持仓到 T 策略"""
        if self.executor.mode != "okx":
            return
        try:
            client = get_okx_client()
            positions = client.get_positions()
            pos_map = {}
            for p in positions:
                inst_id = p.get("instId", "")
                # 永续合约的 instId 去掉 -SWAP 后缀匹配
                base = inst_id.replace("-SWAP", "")
                pos_amt = abs(float(p.get("pos", 0)))
                if pos_amt > 0:
                    pos_map[base] = int(pos_amt)
            self.t_strategy.set_positions(pos_map)
            logger.info(f"Synced OKX positions: {pos_map}")
        except Exception as e:
            logger.error(f"Sync positions error: {e}")

    async def run(self):
        """主循环"""
        self._running = True
        logger.info(
            f"OKX T-Runner started: {len(self.pairs)} pairs, "
            f"mode={self.executor.mode}, interval={self.interval}s"
        )

        # 初始化 GPU 引擎 (可选)
        gpu_engine = None
        try:
            from ..data.gpu_factors import get_gpu_factor_engine
            gpu_engine = get_gpu_factor_engine()
        except Exception:
            logger.info("GPU factor engine not available, using T-strategy only")

        while self._running:
            try:
                t0 = time.perf_counter()

                # 1. 拉取行情
                tickers = fetch_usdt_tickers(self.pairs)
                quotes = okx_to_quotes(tickers)
                t_fetch = (time.perf_counter() - t0) * 1000

                if not quotes:
                    await asyncio.sleep(self.interval)
                    continue

                # 2. GPU 因子更新 (如果可用)
                if gpu_engine:
                    gpu_engine.update(quotes)
                    gpu_engine.compute()

                # 3. T 策略处理
                signals = self.t_strategy.on_quotes(quotes)
                self._tick_count += 1

                # 4. 执行信号
                for sig in signals:
                    result = self.executor.submit_order(
                        code=sig.code,
                        direction=sig.direction,
                        price=sig.price,
                        volume=sig.volume,
                        strategy=sig.strategy,
                    )
                    log_entry = {
                        "tick": self._tick_count,
                        "time": time.strftime("%H:%M:%S"),
                        "code": sig.code,
                        "direction": sig.direction,
                        "price": sig.price,
                        "reason": sig.reason,
                        "result": "OK" if "error" not in result else result["error"],
                    }
                    self._signals_log.append(log_entry)
                    logger.info(
                        f"[OKX-T] {sig.reason} → "
                        f"{sig.direction} {sig.code} @ {sig.price} "
                        f"({'OK' if 'error' not in result else result['error']})"
                    )

                # 5. 定期日志
                if self._tick_count % 30 == 0:
                    status = self.t_strategy.get_status()
                    logger.info(
                        f"OKX-T tick #{self._tick_count}: "
                        f"{len(quotes)} quotes, fetch={t_fetch:.0f}ms, "
                        f"signals_total={len(self._signals_log)}"
                    )
                    for code, info in status.get("stocks", {}).items():
                        logger.info(
                            f"  {code}: price={info['price']:.2f} "
                            f"vwap={info['vwap']} dev={info['deviation']} "
                            f"vol_ratio={info['vol_ratio']} trend={info['trend']}"
                        )

                await asyncio.sleep(self.interval)

            except Exception as e:
                logger.error(f"OKX T-Runner error: {e}")
                await asyncio.sleep(5)

    def stop(self):
        """停止"""
        self._running = False
        logger.info(f"OKX T-Runner stopped. Total signals: {len(self._signals_log)}")

    def get_status(self) -> dict:
        """获取运行状态"""
        return {
            "running": self._running,
            "tick_count": self._tick_count,
            "mode": self.executor.mode,
            "pairs": self.pairs,
            "signals_count": len(self._signals_log),
            "recent_signals": self._signals_log[-10:],
            "strategy_status": self.t_strategy.get_status(),
            "positions": self.executor.get_positions(),
            "pnl": self.executor.get_pnl_summary(),
        }


# 全局实例
_runner: OKXTRunner | None = None


def get_okx_t_runner() -> OKXTRunner | None:
    return _runner


def start_okx_t_runner(
    targets: list[dict],
    mode: str = "paper",
    interval: float = 2.0,
) -> OKXTRunner:
    global _runner
    if _runner and _runner._running:
        _runner.stop()
    _runner = OKXTRunner(targets=targets, mode=mode, interval=interval)
    return _runner
