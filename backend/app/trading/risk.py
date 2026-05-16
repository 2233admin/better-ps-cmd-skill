"""Risk controls for the active trading layer."""

import time
from datetime import datetime

from loguru import logger


class RiskManager:
    """Risk control manager."""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.max_loss_per_trade = cfg.get("max_loss_per_trade", 0.005)
        self.max_daily_loss = cfg.get("max_daily_loss", 0.02)
        self.force_close_time = cfg.get("force_close_time", "14:50")
        self.max_positions = cfg.get("max_positions", 10)
        self.max_order_amount = cfg.get("max_order_amount", 100000)
        self.max_orders_per_minute = 10

        self.daily_pnl = 0.0
        self.account_value = 1_000_000
        self.trade_count_window: list[float] = []
        self.is_stopped = False

    def check_order(
        self,
        code: str,
        direction: str,
        price: float,
        volume: int,
        positions: dict,
    ) -> dict:
        amount = price * volume

        if self.is_stopped:
            return {"passed": False, "reason": "交易已停止（触发日度止损）"}

        now = datetime.now()
        force_h, force_m = map(int, self.force_close_time.split(":"))
        if direction == "buy" and now.hour * 60 + now.minute >= force_h * 60 + force_m:
            return {"passed": False, "reason": f"超过 {self.force_close_time}，禁止开新仓"}

        if amount > self.max_order_amount:
            return {
                "passed": False,
                "reason": f"单笔金额 {amount:.0f} 超过限制 {self.max_order_amount}",
            }

        if direction == "buy" and code not in positions and len(positions) >= self.max_positions:
            return {
                "passed": False,
                "reason": f"持仓数 {len(positions)} 已达上限 {self.max_positions}",
            }

        now_ts = time.time()
        self.trade_count_window = [t for t in self.trade_count_window if now_ts - t < 60]
        if len(self.trade_count_window) >= self.max_orders_per_minute:
            return {"passed": False, "reason": "委托频率过高，请稍后"}

        if self.daily_pnl < -self.max_daily_loss * self.account_value:
            self.is_stopped = True
            return {"passed": False, "reason": "触发日度止损限制，今日停止交易"}

        return {"passed": True, "reason": ""}

    def record_trade(self, direction: str, amount: float, price: float):
        self.trade_count_window.append(time.time())

    def update_daily_pnl(self, pnl: float):
        self.daily_pnl = pnl
        if pnl < -self.max_daily_loss * self.account_value:
            self.is_stopped = True
            logger.warning(f"Daily loss limit reached: {pnl:.2f}")

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.is_stopped = False
        self.trade_count_window.clear()
        logger.info("Risk manager daily reset")

    def should_force_close(self) -> bool:
        now = datetime.now()
        force_h, force_m = map(int, self.force_close_time.split(":"))
        return now.hour * 60 + now.minute >= force_h * 60 + force_m

    def get_status(self) -> dict:
        return {
            "is_stopped": self.is_stopped,
            "daily_pnl": round(self.daily_pnl, 2),
            "account_value": self.account_value,
            "daily_loss_limit": round(self.max_daily_loss * self.account_value, 2),
            "trade_count_last_minute": len(self.trade_count_window),
        }


_risk_manager: RiskManager | None = None


def get_risk_manager() -> RiskManager:
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager
