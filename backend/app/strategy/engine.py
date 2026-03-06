"""策略调度引擎"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger


class StrategyState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class StrategyInfo:
    name: str
    description: str
    state: StrategyState = StrategyState.STOPPED
    params: dict = field(default_factory=dict)
    pnl: float = 0.0
    trade_count: int = 0
    started_at: float | None = None
    error: str = ""


class StrategyEngine:
    """策略调度引擎 - 管理策略的生命周期"""

    def __init__(self):
        self.strategies: dict[str, StrategyInfo] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._register_builtin()

    def _register_builtin(self):
        """注册内置策略"""
        builtins = [
            ("momentum_breakout", "动量突破策略: 开盘30分钟量价突破追涨"),
            ("mean_reversion", "均值回归策略: 偏离VWAP超过2σ反向操作"),
            ("premium_arbitrage", "转股溢价率套利: 买入低溢价可转债"),
            ("pair_trading", "配对交易: 正股与可转债价差回归"),
            ("t_trading", "做T策略: 基于分时均价线的日内高抛低吸(正T/反T/半路T)"),
        ]
        for name, desc in builtins:
            self.strategies[name] = StrategyInfo(name=name, description=desc)

    def list_strategies(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "state": s.state.value,
                "pnl": round(s.pnl, 2),
                "trade_count": s.trade_count,
                "params": s.params,
            }
            for s in self.strategies.values()
        ]

    def start_strategy(self, name: str, params: dict) -> bool:
        if name not in self.strategies:
            logger.error(f"Strategy not found: {name}")
            return False

        strategy = self.strategies[name]
        if strategy.state == StrategyState.RUNNING:
            logger.warning(f"Strategy {name} already running")
            return True

        strategy.params = params
        strategy.state = StrategyState.RUNNING
        strategy.started_at = time.time()
        strategy.error = ""
        logger.info(f"Strategy started: {name} with params {params}")
        return True

    def stop_strategy(self, name: str):
        if name not in self.strategies:
            return
        strategy = self.strategies[name]
        strategy.state = StrategyState.STOPPED
        strategy.started_at = None

        if name in self._tasks:
            self._tasks[name].cancel()
            del self._tasks[name]

        logger.info(f"Strategy stopped: {name}")

    def get_strategy_status(self, name: str) -> dict | None:
        if name not in self.strategies:
            return None
        s = self.strategies[name]
        return {
            "name": s.name,
            "state": s.state.value,
            "pnl": round(s.pnl, 2),
            "trade_count": s.trade_count,
            "params": s.params,
            "started_at": s.started_at,
            "error": s.error,
        }

    def on_quotes(self, quotes: list[dict]):
        """行情推送回调 - 各策略处理信号"""
        for name, strategy in self.strategies.items():
            if strategy.state != StrategyState.RUNNING:
                continue
            try:
                if name == "t_trading":
                    self._process_t_strategy(quotes)
                else:
                    self._process_strategy(name, quotes)
            except Exception as e:
                strategy.state = StrategyState.ERROR
                strategy.error = str(e)
                logger.error(f"Strategy {name} error: {e}")

    def _process_strategy(self, name: str, quotes: list[dict]):
        """处理策略逻辑 → 生成信号 → 自动下单"""
        from .signals import generate_signals
        from ..trade.executor import get_executor

        signals = generate_signals(name, quotes, self.strategies[name].params)
        if not signals:
            return []

        logger.info(f"Strategy {name} generated {len(signals)} signals")
        executor = get_executor()
        strategy = self.strategies[name]

        for sig in signals:
            if sig.confidence < strategy.params.get("min_confidence", 0.5):
                continue
            result = executor.submit_order(
                code=sig.code,
                direction=sig.direction,
                price=sig.price,
                volume=sig.volume,
                strategy=name,
            )
            if "error" not in result:
                strategy.trade_count += 1
                logger.info(f"[{name}] Auto-order: {sig.direction} {sig.code} @ {sig.price}")

        return signals

    def _process_t_strategy(self, quotes: list[dict]):
        """处理做T策略"""
        from .t_strategy import get_t_strategy
        from ..trade.executor import get_executor

        t_strat = get_t_strategy()
        strategy = self.strategies["t_trading"]

        # 同步持仓信息
        executor = get_executor()
        t_strat.set_positions({p.code: p.volume for p in executor.positions.values()})

        signals = t_strat.on_quotes(quotes)
        if not signals:
            return

        min_conf = strategy.params.get("min_confidence", 0.5)
        for sig in signals:
            if sig.confidence < min_conf:
                continue
            result = executor.submit_order(
                code=sig.code,
                direction=sig.direction,
                price=sig.price,
                volume=sig.volume,
                strategy=sig.strategy,
            )
            if "error" not in result:
                strategy.trade_count += 1
                logger.info(
                    f"[T] {sig.reason} → {sig.direction} {sig.code} "
                    f"@ {sig.price} x {sig.volume}"
                )


_engine: StrategyEngine | None = None


def get_strategy_engine() -> StrategyEngine:
    global _engine
    if _engine is None:
        _engine = StrategyEngine()
    return _engine
