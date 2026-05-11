"""期货策略模块 - 专为国内期货市场设计的策略"""

import polars as pl
import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class FuturesSignal:
    """期货信号"""
    timestamp: str
    code: str
    direction: int  # 1=做多, -1=做空, 0=平仓
    price: float
    confidence: float
    reason: str


def futures_ma_cross_strategy(
    data: pl.DataFrame,
    fast_period: int = 5,
    slow_period: int = 20,
    contract_value: float = 300.0,  # 沪深300每点300元
) -> pl.DataFrame:
    """期货均线交叉策略 (支持做多和做空)

    Args:
        data: OHLCV数据
        fast_period: 快线周期
        slow_period: 慢线周期
        contract_value: 合约乘数

    Returns:
        包含信号的数据框
    """
    # 计算均线
    data = data.with_columns([
        pl.col("close").rolling_mean(window_size=fast_period).alias("ma_fast"),
        pl.col("close").rolling_mean(window_size=slow_period).alias("ma_slow"),
    ])

    # 生成信号
    signals = data.with_columns([
        # 金叉: 快线上穿慢线 -> 做多 (+1)
        pl.when(
            (pl.col("ma_fast") > pl.col("ma_slow")) &
            (pl.col("ma_fast").shift(1) <= pl.col("ma_slow").shift(1))
        ).then(1)
        # 死叉: 快线下穿慢线 -> 做空 (-1)
        .when(
            (pl.col("ma_fast") < pl.col("ma_slow")) &
            (pl.col("ma_fast").shift(1) >= pl.col("ma_slow").shift(1))
        ).then(-1)
        .otherwise(0)
        .alias("signal")
    ])

    return signals


def futures_rsi_strategy(
    data: pl.DataFrame,
    period: int = 14,
    oversold: float = 30,
    overbought: float = 70,
) -> pl.DataFrame:
    """期货RSI策略 (反转策略)

    期货可以用RSI做反转:
    - RSI < oversold (超卖) -> 做多 (预期反弹)
    - RSI > overbought (超买) -> 做空 (预期回落)
    """
    # 计算RSI
    def calculate_rsi(close: pl.Series, period: int) -> pl.Series:
        delta = close.diff()
        gain = delta.clip_min(0)
        loss = (-delta).clip_min(0)

        avg_gain = gain.rolling_mean(window_size=period)
        avg_loss = loss.rolling_mean(window_size=period)

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    data = data.with_columns([
        calculate_rsi(pl.col("close"), period).alias("rsi")
    ])

    # 生成信号
    signals = data.with_columns([
        # RSI从超卖区向上突破 -> 做多
        pl.when(
            (pl.col("rsi") > oversold) &
            (pl.col("rsi").shift(1) <= oversold)
        ).then(1)
        # RSI从超买区向下突破 -> 做空
        .when(
            (pl.col("rsi") < overbought) &
            (pl.col("rsi").shift(1) >= overbought)
        ).then(-1)
        .otherwise(0)
        .alias("signal")
    ])

    return signals


def futures_breakout_strategy(
    data: pl.DataFrame,
    lookback: int = 20,
) -> pl.DataFrame:
    """期货突破策略

    - 突破前N日高点 -> 做多
    - 跌破前N日低点 -> 做空
    """
    data = data.with_columns([
        pl.col("high").rolling_max(window_size=lookback).alias("highest_high"),
        pl.col("low").rolling_min(window_size=lookback).alias("lowest_low"),
    ])

    signals = data.with_columns([
        # 突破高点 -> 做多
        pl.when(pl.col("close") > pl.col("highest_high").shift(1)).then(1)
        # 跌破低点 -> 做空
        .when(pl.col("close") < pl.col("lowest_low").shift(1)).then(-1)
        .otherwise(0)
        .alias("signal")
    ])

    return signals


def futures_grid_strategy(
    data: pl.DataFrame,
    grid_size: float = 0.01,  # 1%的网格间距
    num_grids: int = 5,
) -> pl.DataFrame:
    """期货网格策略

    在基准价上下设置网格，触及网格线时交易
    """
    # 计算基准价 (可以用移动平均线)
    data = data.with_columns([
        pl.col("close").rolling_mean(window_size=20).alias("base_price")
    ])

    # 计算网格位置
    signals = data.with_columns([
        # 当前价格相对于基准价的偏离
        ((pl.col("close") - pl.col("base_price")) / pl.col("base_price")).alias("deviation")
    ])

    # 简化的网格信号
    signals = signals.with_columns([
        pl.when(pl.col("deviation") < -grid_size).then(1)  # 低于网格买入
        .when(pl.col("deviation") > grid_size).then(-1)    # 高于网格卖出
        .otherwise(0)
        .alias("signal")
    ])

    return signals


class FuturesBacktester:
    """期货回测引擎 (考虑保证金和杠杆)"""

    def __init__(
        self,
        initial_capital: float = 1_000_000,
        margin_ratio: float = 0.12,  # 保证金比例 12%
        contract_value: float = 300.0,  # 每点价值
        commission: float = 0.0001,  # 手续费
        slippage: float = 0.0002,  # 滑点
    ):
        self.initial_capital = initial_capital
        self.margin_ratio = margin_ratio
        self.contract_value = contract_value
        self.commission = commission
        self.slippage = slippage

    def run(self, data: pl.DataFrame, signals: pl.DataFrame) -> dict:
        """运行回测

        Returns:
            {
                "total_return": 总收益率,
                "annual_return": 年化收益率,
                "max_drawdown": 最大回撤,
                "sharpe": 夏普比率,
                "trades": 交易记录列表,
            }
        """
        capital = self.initial_capital
        position = 0  # 0=空仓, 1=多头, -1=空头
        entry_price = 0.0
        trades = []
        equity_curve = [capital]

        closes = data["close"].to_numpy()
        signal_values = signals["signal"].to_numpy()

        for i in range(1, len(closes)):
            price = closes[i]
            signal = int(signal_values[i])

            # 计算手续费 (双边)
            commission_cost = price * self.contract_value * self.commission

            # 开多
            if signal == 1 and position == 0:
                position = 1
                entry_price = price * (1 + self.slippage)
                margin = entry_price * self.contract_value * self.margin_ratio
                capital -= commission_cost

                trades.append({
                    "time": i,
                    "action": "OPEN_LONG",
                    "price": entry_price,
                    "margin": margin,
                })

            # 开空
            elif signal == -1 and position == 0:
                position = -1
                entry_price = price * (1 - self.slippage)
                margin = entry_price * self.contract_value * self.margin_ratio
                capital -= commission_cost

                trades.append({
                    "time": i,
                    "action": "OPEN_SHORT",
                    "price": entry_price,
                    "margin": margin,
                })

            # 平多
            elif position == 1 and (signal == -1 or signal == 0):
                exit_price = price * (1 - self.slippage)
                pnl = (exit_price - entry_price) * self.contract_value
                capital += pnl - commission_cost
                position = 0

                trades.append({
                    "time": i,
                    "action": "CLOSE_LONG",
                    "price": exit_price,
                    "pnl": pnl,
                })

            # 平空
            elif position == -1 and (signal == 1 or signal == 0):
                exit_price = price * (1 + self.slippage)
                pnl = (entry_price - exit_price) * self.contract_value
                capital += pnl - commission_cost
                position = 0

                trades.append({
                    "time": i,
                    "action": "CLOSE_SHORT",
                    "price": exit_price,
                    "pnl": pnl,
                })

            # 记录权益
            if position == 0:
                equity_curve.append(capital)
            elif position == 1:
                # 多头浮动盈亏
                unrealized = (price - entry_price) * self.contract_value
                equity_curve.append(capital + unrealized)
            else:
                # 空头浮动盈亏
                unrealized = (entry_price - price) * self.contract_value
                equity_curve.append(capital + unrealized)

        # 计算指标
        total_return = (equity_curve[-1] - self.initial_capital) / self.initial_capital

        # 年化收益率 (假设数据是日线的)
        days = len(closes)
        annual_return = (1 + total_return) ** (365 / days) - 1 if days > 0 else 0

        # 最大回撤
        max_dd = 0
        peak = equity_curve[0]
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd

        # 夏普比率 (简化计算)
        returns = np.diff(equity_curve) / equity_curve[:-1]
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if len(returns) > 0 else 0

        return {
            "total_return": round(total_return, 4),
            "annual_return": round(annual_return, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe": round(sharpe, 2),
            "total_trades": len(trades),
            "final_capital": round(equity_curve[-1], 2),
            "trades": trades,
            "equity_curve": equity_curve,
        }
