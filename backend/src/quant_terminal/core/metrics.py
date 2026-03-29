
"""绩效指标计算模块

支持各类风险调整收益指标的计算
"""

from typing import Dict, List, Optional, Union
import numpy as np
import polars as pl
from dataclasses import dataclass


@dataclass
class MetricsResult:
    """指标计算结果"""
    total_return: float
    annual_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    calmar_ratio: float
    win_rate: float
    profit_factor: float
    avg_trade: float
    avg_win: float
    avg_loss: float


def calculate_sharpe(
    returns: Union[np.ndarray, List[float]],
    risk_free_rate: float = 0.02,
    periods_per_year: int = 252
) -> float:
    """计算夏普比率

    Args:
        returns: 收益率序列
        risk_free_rate: 年化无风险利率
        periods_per_year: 每年周期数 (日频252，小时频约252*6.5)

    Returns:
        夏普比率
    """
    returns = np.asarray(returns)

    if len(returns) < 2 or returns.std() == 0:
        return 0.0

    # 计算周期无风险利率
    period_rf = risk_free_rate / periods_per_year

    excess_returns = returns - period_rf
    sharpe = excess_returns.mean() / (returns.std() + 1e-10)

    # 年化
    return sharpe * np.sqrt(periods_per_year)


def calculate_sortino(
    returns: Union[np.ndarray, List[float]],
    risk_free_rate: float = 0.02,
    periods_per_year: int = 252
) -> float:
    """计算索提诺比率 (只考虑下行风险)

    Args:
        returns: 收益率序列
        risk_free_rate: 年化无风险利率
        periods_per_year: 每年周期数

    Returns:
        索提诺比率
    """
    returns = np.asarray(returns)

    if len(returns) < 2:
        return 0.0

    period_rf = risk_free_rate / periods_per_year
    excess_returns = returns - period_rf

    # 下行标准差
    downside_returns = returns[returns < 0]
    if len(downside_returns) == 0:
        return np.inf

    downside_std = downside_returns.std()

    return (excess_returns.mean() / (downside_std + 1e-10)) * np.sqrt(periods_per_year)


def calculate_drawdown(
    returns: Union[np.ndarray, List[float]],
    method: str = "percent"
) -> Dict[str, float]:
    """计算回撤指标

    Args:
        returns: 收益率序列
        method: 计算方法 (percent: 百分比, dollar: 金额)

    Returns:
        包含max_drawdown, avg_drawdown, max_drawdown_duration的字典
    """
    returns = np.asarray(returns)

    if len(returns) == 0:
        return {
            'max_drawdown': 0.0,
            'avg_drawdown': 0.0,
            'max_drawdown_duration': 0
        }

    # 计算累计收益曲线
    cumulative = np.cumsum(returns)
    running_max = np.maximum.accumulate(cumulative)

    # 回撤
    drawdown = cumulative - running_max

    # 计算回撤持续时间
    in_drawdown = drawdown < 0
    durations = []
    current_duration = 0

    for is_dd in in_drawdown:
        if is_dd:
            current_duration += 1
        else:
            if current_duration > 0:
                durations.append(current_duration)
            current_duration = 0

    if current_duration > 0:
        durations.append(current_duration)

    return {
        'max_drawdown': abs(float(drawdown.min())),
        'avg_drawdown': abs(float(drawdown.mean())),
        'max_drawdown_duration': max(durations) if durations else 0
    }


def calculate_returns(
    equity_curve: Union[np.ndarray, List[float]],
    method: str = "arithmetic"
) -> np.ndarray:
    """计算收益率序列

    Args:
        equity_curve: 权益曲线
        method: arithmetic (算数) 或 log (对数)

    Returns:
        收益率序列
    """
    equity = np.asarray(equity_curve)

    if len(equity) < 2:
        return np.array([])

    if method == "arithmetic":
        return (equity[1:] - equity[:-1]) / equity[:-1]
    elif method == "log":
        return np.log(equity[1:] / equity[:-1])
    else:
        raise ValueError(f"Unknown method: {method}")


def calculate_calmar(
    returns: Union[np.ndarray, List[float]],
    periods_per_year: int = 252
) -> float:
    """计算Calmar比率 (年化收益 / 最大回撤)

    Args:
        returns: 收益率序列
        periods_per_year: 每年周期数

    Returns:
        Calmar比率
    """
    returns = np.asarray(returns)

    if len(returns) == 0:
        return 0.0

    annual_return = returns.mean() * periods_per_year
    max_dd = calculate_drawdown(returns)['max_drawdown']

    if max_dd == 0:
        return np.inf

    return annual_return / max_dd


def calculate_trade_metrics(trades: pl.DataFrame) -> Dict[str, float]:
    """计算交易相关指标

    Args:
        trades: 交易记录DataFrame，必须包含pnl列

    Returns:
        交易指标字典
    """
    if trades.is_empty() or 'pnl' not in trades.columns:
        return {
            'total_trades': 0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'avg_trade': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'win_loss_ratio': 0.0
        }

    pnls = trades['pnl'].to_numpy()

    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    total_trades = len(pnls)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0

    total_wins = wins.sum() if len(wins) > 0 else 0.0
    total_losses = abs(losses.sum()) if len(losses) > 0 else 1e-10
    profit_factor = total_wins / total_losses

    avg_trade = pnls.mean()
    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = losses.mean() if len(losses) > 0 else 0.0

    win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else np.inf

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'avg_trade': avg_trade,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'win_loss_ratio': win_loss_ratio
    }


class MetricsCalculator:
    """指标计算器类

    提供统一的指标计算接口

    Example:
        >>> calc = MetricsCalculator(returns=my_returns)
        >>> result = calc.calculate_all()
        >>> print(f"夏普: {result.sharpe_ratio:.2f}")
    """

    def __init__(
        self,
        returns: Optional[np.ndarray] = None,
        equity_curve: Optional[np.ndarray] = None,
        trades: Optional[pl.DataFrame] = None,
        risk_free_rate: float = 0.02,
        periods_per_year: int = 252
    ):
        """初始化计算器

        Args:
            returns: 收益率序列
            equity_curve: 权益曲线 (与returns二选一)
            trades: 交易记录
            risk_free_rate: 无风险利率
            periods_per_year: 每年周期数
        """
        if returns is not None:
            self.returns = np.asarray(returns)
        elif equity_curve is not None:
            self.returns = calculate_returns(equity_curve)
        else:
            raise ValueError("必须提供returns或equity_curve")

        self.trades = trades
        self.risk_free_rate = risk_free_rate
        self.periods_per_year = periods_per_year

    def calculate_all(self) -> MetricsResult:
        """计算所有指标"""
        trade_metrics = calculate_trade_metrics(self.trades) if self.trades is not None else {}
        drawdown_metrics = calculate_drawdown(self.returns)

        return MetricsResult(
            total_return=float(self.returns.sum()),
            annual_return=float(self.returns.mean() * self.periods_per_year),
            volatility=float(self.returns.std() * np.sqrt(self.periods_per_year)),
            sharpe_ratio=calculate_sharpe(
                self.returns, self.risk_free_rate, self.periods_per_year
            ),
            sortino_ratio=calculate_sortino(
                self.returns, self.risk_free_rate, self.periods_per_year
            ),
            max_drawdown=drawdown_metrics['max_drawdown'],
            calmar_ratio=calculate_calmar(self.returns, self.periods_per_year),
            win_rate=trade_metrics.get('win_rate', 0.0),
            profit_factor=trade_metrics.get('profit_factor', 0.0),
            avg_trade=trade_metrics.get('avg_trade', 0.0),
            avg_win=trade_metrics.get('avg_win', 0.0),
            avg_loss=trade_metrics.get('avg_loss', 0.0)
        )

    def to_dict(self) -> Dict[str, float]:
        """转换为字典格式"""
        result = self.calculate_all()
        return {
            'total_return': result.total_return,
            'annual_return': result.annual_return,
            'volatility': result.volatility,
            'sharpe_ratio': result.sharpe_ratio,
            'sortino_ratio': result.sortino_ratio,
            'max_drawdown': result.max_drawdown,
            'calmar_ratio': result.calmar_ratio,
            'win_rate': result.win_rate,
            'profit_factor': result.profit_factor,
            'avg_trade': result.avg_trade,
            'avg_win': result.avg_win,
            'avg_loss': result.avg_loss
        }

    def get_summary_text(self) -> str:
        """获取格式化的摘要文本"""
        r = self.calculate_all()

        return f"""
========== 绩效指标 ==========
总收益:      {r.total_return:>+10.2%}
年化收益:    {r.annual_return:>+10.2%}
波动率:      {r.volatility:>10.2%}
夏普比率:    {r.sharpe_ratio:>10.2f}
索提诺比率:  {r.sortino_ratio:>10.2f}
最大回撤:    {r.max_drawdown:>10.2%}
Calmar比率:  {r.calmar_ratio:>10.2f}
胜率:        {r.win_rate:>10.1%}
盈亏比:      {r.profit_factor:>10.2f}
平均盈亏:    {r.avg_trade:>+10.2%}
==============================
"""
