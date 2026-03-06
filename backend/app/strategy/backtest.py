"""回测框架 - 基于 Polars 的向量化回测"""

from dataclasses import dataclass, field

import numpy as np
import polars as pl
from loguru import logger


@dataclass
class BacktestResult:
    """回测结果"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    profit_trades: int = 0
    loss_trades: int = 0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trade_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_return": round(self.total_return, 4),
            "annual_return": round(self.annual_return, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown": round(self.max_drawdown, 4),
            "win_rate": round(self.win_rate, 4),
            "total_trades": self.total_trades,
            "profit_trades": self.profit_trades,
            "loss_trades": self.loss_trades,
            "avg_profit": round(self.avg_profit, 4),
            "avg_loss": round(self.avg_loss, 4),
            "profit_factor": round(self.profit_factor, 2),
        }


class VectorBacktester:
    """向量化回测引擎"""

    def __init__(
        self,
        initial_capital: float = 1_000_000,
        commission: float = 0.0005,  # 万5
        slippage: float = 0.001,  # 0.1%
    ):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def run(
        self,
        data: pl.DataFrame,
        signals: pl.DataFrame,
    ) -> BacktestResult:
        """运行回测

        Args:
            data: OHLCV 数据, 必须包含 date/datetime, open, high, low, close, volume
            signals: 信号数据, 必须包含 date/datetime, signal (+1=买, -1=卖, 0=无)

        Returns:
            BacktestResult
        """
        result = BacktestResult()

        if data.is_empty() or signals.is_empty():
            return result

        # 合并数据和信号
        time_col = "datetime" if "datetime" in data.columns else "date"
        merged = data.join(signals, on=time_col, how="left")
        merged = merged.with_columns(pl.col("signal").fill_null(0))

        # 计算仓位: signal 直接作为仓位方向
        # 实际持仓 = signal 的累积方向
        positions = merged["signal"].to_numpy()
        closes = merged["close"].to_numpy()

        # 简化: 持有期收益
        capital = self.initial_capital
        equity = [capital]
        trades = []
        pos = 0  # 当前持仓: 0=空仓, 1=多头
        entry_price = 0.0

        for i in range(1, len(positions)):
            signal = int(positions[i])
            price = closes[i]

            if signal == 1 and pos == 0:
                # 买入
                entry_price = price * (1 + self.slippage)
                cost = entry_price * self.commission
                capital -= cost
                pos = 1
                trades.append({
                    "type": "buy",
                    "price": entry_price,
                    "index": i,
                })
            elif signal == -1 and pos == 1:
                # 卖出
                exit_price = price * (1 - self.slippage)
                pnl = (exit_price - entry_price) / entry_price
                cost = exit_price * self.commission
                capital = capital * (1 + pnl) - cost
                pos = 0
                trades.append({
                    "type": "sell",
                    "price": exit_price,
                    "pnl": pnl,
                    "index": i,
                })

            equity.append(capital if pos == 0 else capital * (price / entry_price))

        # 计算统计指标
        equity_arr = np.array(equity)
        returns = np.diff(equity_arr) / equity_arr[:-1]

        result.equity_curve = equity
        result.total_return = (equity_arr[-1] / self.initial_capital) - 1
        result.trade_log = trades

        # 年化收益 (假设 250 个交易日)
        n_days = len(returns)
        if n_days > 0:
            result.annual_return = (1 + result.total_return) ** (250 / n_days) - 1

        # 夏普比 (无风险利率 3%)
        if len(returns) > 1 and np.std(returns) > 0:
            result.sharpe_ratio = (np.mean(returns) - 0.03 / 250) / np.std(returns) * np.sqrt(250)

        # 最大回撤
        peak = np.maximum.accumulate(equity_arr)
        drawdown = (peak - equity_arr) / peak
        result.max_drawdown = float(np.max(drawdown))

        # 交易统计
        trade_pnls = [t["pnl"] for t in trades if "pnl" in t]
        result.total_trades = len(trade_pnls)
        profits = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p <= 0]
        result.profit_trades = len(profits)
        result.loss_trades = len(losses)
        result.win_rate = len(profits) / len(trade_pnls) if trade_pnls else 0
        result.avg_profit = float(np.mean(profits)) if profits else 0
        result.avg_loss = float(np.mean(losses)) if losses else 0

        total_profit = sum(profits) if profits else 0
        total_loss = abs(sum(losses)) if losses else 0
        result.profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

        logger.info(
            f"Backtest: {result.total_trades} trades, "
            f"return={result.total_return:.2%}, "
            f"sharpe={result.sharpe_ratio:.2f}, "
            f"mdd={result.max_drawdown:.2%}"
        )

        return result
