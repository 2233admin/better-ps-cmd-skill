"""
回测引擎单元测试
"""

import pytest
import polars as pl
import numpy as np
from datetime import datetime, timedelta

from quant_terminal.core.backtest import (
    BacktestEngine,
    BacktestConfig,
    BacktestResult
)


class TestBacktestEngine:
    """BacktestEngine测试类"""

    def test_initialization(self):
        """测试引擎初始化"""
        config = BacktestConfig(
            initial_capital=1_000_000,
            commission=0.0001,
            slippage=0.0002
        )
        engine = BacktestEngine(config)

        assert engine.config.initial_capital == 1_000_000
        assert engine.config.commission == 0.0001

    def test_data_validation(self, sample_ohlcv_data):
        """测试数据验证"""
        engine = BacktestEngine()

        # 有效数据
        assert engine._validate_data(
            sample_ohlcv_data,
            pl.DataFrame({"datetime": [], "signal": []})
        ) is None  # 不抛出异常

        # 缺少列的数据
        invalid_data = sample_ohlcv_data.drop("close")
        with pytest.raises(ValueError):
            engine._validate_data(invalid_data, pl.DataFrame())

    def test_run_backtest(self, sample_ohlcv_data, sample_signals):
        """测试回测执行"""
        engine = BacktestEngine()
        result = engine.run(sample_ohlcv_data, sample_signals)

        assert isinstance(result, BacktestResult)
        assert hasattr(result, 'total_return')
        assert hasattr(result, 'sharpe_ratio')
        assert hasattr(result, 'max_drawdown')

    def test_batch_backtest(self, sample_ohlcv_data, sample_signals):
        """测试批量回测"""
        engine = BacktestEngine()

        data_dict = {"IF0": sample_ohlcv_data, "IC0": sample_ohlcv_data}
        signals_dict = {"IF0": sample_signals, "IC0": sample_signals}

        results = engine.run_batch(data_dict, signals_dict)

        assert "IF0" in results
        assert "IC0" in results
        assert isinstance(results["IF0"], BacktestResult)

    def test_get_summary(self, sample_ohlcv_data, sample_signals):
        """测试结果汇总"""
        engine = BacktestEngine()

        data_dict = {"IF0": sample_ohlcv_data}
        signals_dict = {"IF0": sample_signals}

        results = engine.run_batch(data_dict, signals_dict)
        summary = engine.get_summary(results)

        assert isinstance(summary, pl.DataFrame)
        assert "code" in summary.columns
        assert "sharpe_ratio" in summary.columns


class TestBacktestConfig:
    """BacktestConfig测试类"""

    def test_default_values(self):
        """测试默认值"""
        config = BacktestConfig()

        assert config.initial_capital == 1_000_000.0
        assert config.commission == 0.0001
        assert config.slippage == 0.0002

    def test_custom_values(self):
        """测试自定义值"""
        config = BacktestConfig(
            initial_capital=500_000,
            commission=0.0002,
            slippage=0.0003
        )

        assert config.initial_capital == 500_000
        assert config.commission == 0.0002
        assert config.slippage == 0.0003
