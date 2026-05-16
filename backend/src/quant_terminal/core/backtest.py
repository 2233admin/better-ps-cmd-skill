
"""回测引擎 - 生产级向量化回测框架

基于Polars的高性能回测引擎，支持:
- 向量化信号处理
- 多品种并行回测
- 完整的绩效指标计算
- 滑点和手续费模拟
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime
import numpy as np
import polars as pl
from loguru import logger

try:
    import torch
except ImportError:  # GPU support is optional for CPU backtests.
    torch = None


@dataclass
class BacktestResult:
    """回测结果数据类"""
    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    win_rate: float
    profit_factor: float
    equity_curve: pl.DataFrame
    trades: pl.DataFrame
    metrics: Dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestConfig:
    """回测配置"""
    initial_capital: float = 1_000_000.0
    commission: float = 0.0001  # 万分之一
    slippage: float = 0.0002    # 万分之二滑点
    position_size: float = 1.0  # 仓位比例
    allow_short: bool = True
    margin_ratio: float = 0.15  # 期货保证金比例


class BacktestEngine:
    """向量化回测引擎

    高性能回测实现，使用Polars进行向量化计算
    支持股票、期货等多种资产类型

    Example:
        >>> engine = BacktestEngine(initial_capital=1_000_000)
        >>> result = engine.run(data, signals)
        >>> print(f"夏普比率: {result.sharpe_ratio:.2f}")
    """

    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        use_gpu: bool = False
    ):
        """初始化回测引擎

        Args:
            config: 回测配置，默认使用默认配置
            use_gpu: 是否使用GPU加速
        """
        self.config = config or BacktestConfig()
        self._torch: Optional[Any] = torch
        self.use_gpu = bool(use_gpu and self._torch and self._torch.cuda.is_available())
        self.device = self._torch.device('cuda' if self.use_gpu else 'cpu') if self._torch else None

        if use_gpu and not self.use_gpu:
            logger.warning("[BacktestEngine] GPU不可用，使用CPU")

        if self.use_gpu:
            logger.info(f"[BacktestEngine] GPU加速已启用: {self._torch.cuda.get_device_name(0)}")

        self._equity_curve: Optional[pl.DataFrame] = None
        self._trades: List[Dict] = []

    def run(
        self,
        data: pl.DataFrame,
        signals: pl.DataFrame,
        strategy_name: str = "unknown"
    ) -> BacktestResult:
        """运行回测

        Args:
            data: OHLCV数据，必须包含datetime, open, high, low, close, volume列
            signals: 交易信号，必须包含datetime, signal列 (1=多头, -1=空头, 0=平仓)
            strategy_name: 策略名称

        Returns:
            BacktestResult: 回测结果

        Raises:
            ValueError: 数据格式不正确
        """
        # 数据验证
        self._validate_data(data, signals)

        # 向量化回测计算
        if self.use_gpu:
            return self._run_gpu(data, signals, strategy_name)
        else:
            return self._run_cpu(data, signals, strategy_name)

    def _validate_data(
        self,
        data: pl.DataFrame,
        signals: pl.DataFrame
    ) -> None:
        """验证输入数据格式"""
        required_data_cols = {'datetime', 'open', 'high', 'low', 'close', 'volume'}
        required_signal_cols = {'datetime', 'signal'}

        if not required_data_cols.issubset(set(data.columns)):
            missing = required_data_cols - set(data.columns)
            raise ValueError(f"数据缺少必需列: {missing}")

        if not required_signal_cols.issubset(set(signals.columns)):
            missing = required_signal_cols - set(signals.columns)
            raise ValueError(f"信号缺少必需列: {missing}")

    def _run_cpu(
        self,
        data: pl.DataFrame,
        signals: pl.DataFrame,
        strategy_name: str
    ) -> BacktestResult:
        """CPU版本回测"""
        # 合并数据和信号
        df = data.join(signals, on='datetime', how='left')
        df = df.with_columns(
            pl.col('signal').fill_null(0)
        )

        # 计算持仓变化
        df = self._calculate_positions(df)

        # 计算收益
        df = self._calculate_returns(df)

        # 生成交易记录
        trades = self._extract_trades(df)

        # 计算绩效指标
        metrics = self._calculate_metrics(df, trades)

        # 构建权益曲线
        equity_curve = df.select([
            'datetime',
            'close',
            'signal',
            'position',
            'daily_return',
            'cumulative_return',
            'equity'
        ])

        return BacktestResult(
            total_return=metrics['total_return'],
            annual_return=metrics['annual_return'],
            sharpe_ratio=metrics['sharpe_ratio'],
            max_drawdown=metrics['max_drawdown'],
            total_trades=metrics['total_trades'],
            win_rate=metrics['win_rate'],
            profit_factor=metrics['profit_factor'],
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics
        )

    def _run_gpu(
        self,
        data: pl.DataFrame,
        signals: pl.DataFrame,
        strategy_name: str
    ) -> BacktestResult:
        """GPU版本回测"""
        if self._torch is None:
            raise RuntimeError("GPU backtest requires the optional torch dependency")

        torch = self._torch

        # 将数据移到GPU
        close = torch.tensor(data['close'].to_numpy(), dtype=torch.float32, device=self.device)
        signal = torch.tensor(signals['signal'].to_numpy(), dtype=torch.float32, device=self.device)

        n = len(close)

        # 向量化计算收益
        price_changes = (close[1:] - close[:-1]) / close[:-1]
        trade_signals = signal[:-1]

        returns = price_changes * trade_signals
        costs = (trade_signals != 0).float() * (self.config.commission + self.config.slippage)
        returns = returns - costs

        # 计算累计收益
        cumulative = torch.cumsum(returns, dim=0)
        equity = self.config.initial_capital * (1 + cumulative)

        # 转回CPU进行结果组装
        returns_np = returns.cpu().numpy()
        equity_np = torch.cat([
            torch.tensor([self.config.initial_capital], device=self.device),
            equity
        ]).cpu().numpy()

        # 构建结果DataFrame
        df = data.with_columns([
            pl.Series('daily_return', np.concatenate([[0], returns_np])),
            pl.Series('equity', equity_np),
            pl.Series('cumulative_return', np.concatenate([[0], cumulative.cpu().numpy()]))
        ])

        # 计算指标
        trades = self._extract_trades(df)
        metrics = self._calculate_metrics(df, trades)

        return BacktestResult(
            total_return=metrics['total_return'],
            annual_return=metrics['annual_return'],
            sharpe_ratio=metrics['sharpe_ratio'],
            max_drawdown=metrics['max_drawdown'],
            total_trades=metrics['total_trades'],
            win_rate=metrics['win_rate'],
            profit_factor=metrics['profit_factor'],
            equity_curve=df.select(['datetime', 'close', 'equity', 'daily_return']),
            trades=trades,
            metrics=metrics
        )

    def _calculate_positions(self, df: pl.DataFrame) -> pl.DataFrame:
        """计算持仓状态"""
        # 检测信号变化点
        df = df.with_columns([
            pl.col('signal').shift(1).alias('prev_signal')
        ])

        # 使用状态机计算实际持仓
        position = []
        current_pos = 0

        for row in df.iter_rows(named=True):
            signal = row['signal']

            if current_pos == 0:  # 空仓
                if signal == 1:
                    current_pos = 1
                elif signal == -1:
                    current_pos = -1
            elif current_pos == 1:  # 多头
                if signal == -1:
                    current_pos = -1
                elif signal == 0:
                    current_pos = 0
            elif current_pos == -1:  # 空头
                if signal == 1:
                    current_pos = 1
                elif signal == 0:
                    current_pos = 0

            position.append(current_pos)

        return df.with_columns([
            pl.Series('position', position)
        ])

    def _calculate_returns(self, df: pl.DataFrame) -> pl.DataFrame:
        """计算收益"""
        df = df.with_columns([
            pl.col('position').shift(1).fill_null(0).alias('prev_position'),
            ((pl.col('close') - pl.col('close').shift(1)) / pl.col('close').shift(1)).alias('price_change'),
        ]).with_columns([
            (pl.col('position') - pl.col('prev_position')).abs().alias('trade_size'),
        ]).with_columns([
            (
                (pl.col('price_change').fill_null(0) * pl.col('prev_position'))
                - (pl.col('trade_size') * (self.config.commission + self.config.slippage))
            ).alias('daily_return')
        ]).with_columns([
            pl.col('daily_return').fill_null(0).cum_sum().alias('cumulative_return'),
            (self.config.initial_capital * (1 + pl.col('daily_return').fill_null(0).cum_sum())).alias('equity')
        ])
        return df

    def _extract_trades(self, df: pl.DataFrame) -> pl.DataFrame:
        """提取交易记录"""
        trades = []

        entry_time = None
        entry_price = None
        entry_direction = None
        entry_index = None

        for index, row in enumerate(df.iter_rows(named=True)):
            position = row['position']
            prev_position = row.get('prev_position', 0) if 'prev_position' in row else 0

            def close_trade() -> None:
                nonlocal entry_time, entry_price, entry_direction, entry_index
                if entry_time is None:
                    return

                exit_time = row['datetime']
                exit_price = row['close']

                pnl = (exit_price - entry_price) / entry_price
                if entry_direction == 'short':
                    pnl = -pnl

                trades.append({
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'direction': entry_direction,
                    'pnl': pnl - self.config.commission - self.config.slippage,
                    'bars_held': max(index - entry_index, 0)
                })

                entry_time = None
                entry_price = None
                entry_direction = None
                entry_index = None

            def open_trade() -> None:
                nonlocal entry_time, entry_price, entry_direction, entry_index
                entry_time = row['datetime']
                entry_price = row['close']
                entry_direction = 'long' if position == 1 else 'short'
                entry_index = index

            # 检测开平仓
            if prev_position == 0 and position != 0:
                open_trade()
            elif prev_position != 0 and position == 0:
                close_trade()
            elif prev_position != 0 and position != 0 and prev_position != position:
                close_trade()
                open_trade()

        if trades:
            return pl.DataFrame(trades)
        else:
            return pl.DataFrame({
                'entry_time': [],
                'exit_time': [],
                'entry_price': [],
                'exit_price': [],
                'direction': [],
                'pnl': [],
                'bars_held': []
            })

    def _calculate_metrics(
        self,
        df: pl.DataFrame,
        trades: pl.DataFrame
    ) -> Dict[str, float]:
        """计算绩效指标"""
        returns = df['daily_return'].drop_nulls().to_numpy()

        if len(returns) == 0 or len(trades) == 0:
            return {
                'total_return': 0.0,
                'annual_return': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown': 0.0,
                'total_trades': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
            }

        total_return = float(returns.sum())

        # 年化收益 (假设252个交易日)
        n_days = len(returns)
        annual_return = total_return * (252 / max(n_days, 1))

        # 夏普比率
        returns_std = float(returns.std())
        sharpe_ratio = (returns.mean() / (returns_std + 1e-10)) * np.sqrt(252)

        # 最大回撤
        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = cumulative - running_max
        max_drawdown = abs(float(drawdown.min()))

        # 交易统计
        total_trades = len(trades)
        if total_trades > 0 and 'pnl' in trades.columns:
            pnls = trades['pnl'].to_numpy()
            win_rate = float((pnls > 0).sum() / len(pnls))

            wins = pnls[pnls > 0].sum() if len(pnls[pnls > 0]) > 0 else 0
            losses = abs(pnls[pnls < 0].sum()) if len(pnls[pnls < 0]) > 0 else 1e-10
            profit_factor = wins / losses
        else:
            win_rate = 0.0
            profit_factor = 0.0

        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'volatility': returns_std * np.sqrt(252),
            'calmar_ratio': annual_return / (max_drawdown + 1e-10),
        }

    def run_batch(
        self,
        data_dict: Dict[str, pl.DataFrame],
        signals_dict: Dict[str, pl.DataFrame],
        strategy_name: str = "batch"
    ) -> Dict[str, BacktestResult]:
        """批量运行多个品种的回测

        Args:
            data_dict: 品种代码到数据的映射
            signals_dict: 品种代码到信号的映射
            strategy_name: 策略名称

        Returns:
            品种代码到回测结果的映射
        """
        results = {}

        for code, data in data_dict.items():
            if code in signals_dict:
                logger.info(f"[BacktestEngine] 运行 {code} 回测...")
                results[code] = self.run(data, signals_dict[code], strategy_name)
            else:
                logger.warning(f"[BacktestEngine] {code} 缺少信号数据，跳过")

        return results

    def get_summary(self, results: Dict[str, BacktestResult]) -> pl.DataFrame:
        """生成回测结果汇总表"""
        summary = []

        for code, result in results.items():
            summary.append({
                'code': code,
                'total_return': result.total_return,
                'annual_return': result.annual_return,
                'sharpe_ratio': result.sharpe_ratio,
                'max_drawdown': result.max_drawdown,
                'total_trades': result.total_trades,
                'win_rate': result.win_rate,
                'profit_factor': result.profit_factor,
            })

        return pl.DataFrame(summary)
