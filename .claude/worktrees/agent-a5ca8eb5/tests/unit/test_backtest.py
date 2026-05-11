"""回测引擎单元测试"""

import pytest
import numpy as np
import polars as pl
from datetime import datetime, timedelta

from app.strategy.backtest import VectorBacktester, BacktestResult


class TestBacktestResult:
    """测试回测结果数据类"""

    def test_default_values(self):
        """测试默认值"""
        result = BacktestResult()
        assert result.total_return == 0.0
        assert result.annual_return == 0.0
        assert result.sharpe_ratio == 0.0
        assert result.max_drawdown == 0.0
        assert result.win_rate == 0.0
        assert result.total_trades == 0
        assert result.equity_curve == []
        assert result.trade_log == []

    def test_to_dict(self):
        """测试转换为字典"""
        result = BacktestResult(
            total_return=0.15,
            annual_return=0.20,
            sharpe_ratio=1.5,
            max_drawdown=0.05,
            win_rate=0.65,
            total_trades=100,
            profit_trades=65,
            loss_trades=35,
            avg_profit=0.02,
            avg_loss=-0.01,
            profit_factor=2.0,
        )
        d = result.to_dict()
        assert d["total_return"] == 0.15
        assert d["sharpe_ratio"] == 1.5
        assert d["total_trades"] == 100


class TestVectorBacktester:
    """测试向量化回测引擎"""

    def test_initialization(self):
        """测试初始化参数"""
        bt = VectorBacktester(
            initial_capital=500000,
            commission=0.001,
            slippage=0.002,
        )
        assert bt.initial_capital == 500000
        assert bt.commission == 0.001
        assert bt.slippage == 0.002

    def test_default_initialization(self):
        """测试默认初始化"""
        bt = VectorBacktester()
        assert bt.initial_capital == 1_000_000
        assert bt.commission == 0.0005
        assert bt.slippage == 0.001

    def test_run_with_empty_data(self):
        """测试空数据返回默认结果"""
        bt = VectorBacktester()
        empty_data = pl.DataFrame()
        empty_signals = pl.DataFrame()

        result = bt.run(empty_data, empty_signals)
        assert isinstance(result, BacktestResult)
        assert result.total_return == 0.0
        assert result.total_trades == 0

    def test_run_simple_buy_sell(self, sample_ohlcv_data):
        """测试简单买卖信号"""
        bt = VectorBacktester(initial_capital=100000)

        # 创建简单的买卖信号
        n = len(sample_ohlcv_data)
        signals = pl.DataFrame({
            "datetime": sample_ohlcv_data["datetime"].to_list(),
            "signal": [0] * n,
        })

        # 在第10个位置买入，第20个位置卖出
        signals_list = signals["signal"].to_list()
        signals_list[10] = 1
        signals_list[20] = -1
        signals = signals.with_columns(pl.Series("signal", signals_list))

        result = bt.run(sample_ohlcv_data, signals)

        assert result.total_trades == 1
        assert len(result.equity_curve) > 0
        assert len(result.trade_log) == 2  # 买入和卖出

    def test_run_multiple_trades(self, sample_ohlcv_data):
        """测试多笔交易"""
        bt = VectorBacktester(initial_capital=100000)

        n = len(sample_ohlcv_data)
        signals_list = [0] * n
        # 创建3个完整的买卖周期
        for i in range(3):
            buy_idx = 10 + i * 25
            sell_idx = 20 + i * 25
            if buy_idx < n:
                signals_list[buy_idx] = 1
            if sell_idx < n:
                signals_list[sell_idx] = -1

        signals = pl.DataFrame({
            "datetime": sample_ohlcv_data["datetime"].to_list(),
            "signal": signals_list,
        })

        result = bt.run(sample_ohlcv_data, signals)

        assert result.total_trades >= 2
        assert len(result.equity_curve) > 0

    def test_commission_and_slippage(self):
        """测试手续费和滑点计算"""
        bt = VectorBacktester(
            initial_capital=100000,
            commission=0.001,  # 千分之一
            slippage=0.01,     # 1%
        )

        dates = [datetime(2024, 1, 1) + timedelta(minutes=i) for i in range(10)]
        prices = [100.0] * 10

        data = pl.DataFrame({
            "datetime": dates,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1000] * 10,
        })

        signals = pl.DataFrame({
            "datetime": dates,
            "signal": [0, 1, 0, 0, 0, -1, 0, 0, 0, 0],
        })

        result = bt.run(data, signals)

        # 验证交易成本被计入
        assert result.total_trades == 1
        trade = [t for t in result.trade_log if "pnl" in t][0]
        # 滑点1% + 手续费0.1%，买入和卖出都有成本
        assert trade["pnl"] < 0  # 应该有亏损

    def test_max_drawdown_calculation(self):
        """测试最大回撤计算"""
        bt = VectorBacktester(initial_capital=100000)

        dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(20)]
        # 创建一个先涨后跌的价格序列
        prices = [100, 105, 110, 108, 105, 100, 95, 90, 95, 100,
                  102, 104, 103, 102, 101, 100, 99, 98, 97, 96]

        data = pl.DataFrame({
            "datetime": dates,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1000] * 20,
        })

        # 持仓信号
        signals = pl.DataFrame({
            "datetime": dates,
            "signal": [1] + [0] * 19,
        })

        result = bt.run(data, signals)

        assert result.max_drawdown >= 0
        # 从110跌到90，回撤约18%
        assert result.max_drawdown > 0.15

    def test_sharpe_ratio_calculation(self):
        """测试夏普比率计算"""
        bt = VectorBacktester(initial_capital=100000)

        dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(252)]
        np.random.seed(42)
        # 生成正收益的价格序列
        returns = np.random.normal(0.001, 0.02, 252)
        prices = 100 * np.exp(np.cumsum(returns))

        data = pl.DataFrame({
            "datetime": dates,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": np.random.randint(1000, 10000, 252),
        })

        signals = pl.DataFrame({
            "datetime": dates,
            "signal": [1] + [0] * 251,
        })

        result = bt.run(data, signals)

        # 夏普比率应该为正
        assert result.sharpe_ratio > 0

    def test_profit_factor_calculation(self):
        """测试盈亏比计算"""
        bt = VectorBacktester(initial_capital=100000)

        dates = [datetime(2024, 1, 1) + timedelta(minutes=i) for i in range(20)]
        # 创建一个确定性的价格序列：先涨后跌再涨
        prices = [100, 102, 104, 106, 108, 106, 104, 102, 100, 98,
                  100, 102, 104, 106, 108, 110, 112, 114, 116, 118]

        data = pl.DataFrame({
            "datetime": dates,
            "open": prices,
            "high": [p * 1.005 for p in prices],
            "low": [p * 0.995 for p in prices],
            "close": prices,
            "volume": [1000] * 20,
        })

        # 多个买卖信号
        signals = pl.DataFrame({
            "datetime": dates,
            "signal": [0, 1, 0, 0, -1, 0, 0, 0, 0, 0,
                      1, 0, 0, 0, 0, -1, 0, 0, 0, 0],
        })

        result = bt.run(data, signals)

        if result.profit_trades > 0 and result.loss_trades > 0:
            assert result.profit_factor > 0
        if result.total_trades > 0:
            assert 0 <= result.win_rate <= 1

    def test_annual_return_calculation(self):
        """测试年化收益计算"""
        bt = VectorBacktester(initial_capital=100000)

        # 一年的数据
        dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(252)]
        # 10%的总收益
        prices = np.linspace(100, 110, 252)

        data = pl.DataFrame({
            "datetime": dates,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": [1000] * 252,
        })

        signals = pl.DataFrame({
            "datetime": dates,
            "signal": [1] + [0] * 251,
        })

        result = bt.run(data, signals)

        # 验证年化收益约为10%
        assert result.annual_return > 0.08
        assert result.annual_return < 0.12

    def test_date_column_handling(self):
        """测试日期列处理（支持date和datetime）"""
        bt = VectorBacktester()

        # 使用date列
        dates = [datetime(2024, 1, 1).date() + timedelta(days=i) for i in range(10)]
        data = pl.DataFrame({
            "date": dates,
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.0] * 10,
            "volume": [1000] * 10,
        })

        signals = pl.DataFrame({
            "date": dates,
            "signal": [0, 1, 0, 0, -1, 0, 0, 0, 0, 0],
        })

        result = bt.run(data, signals)
        assert result.total_trades == 1
