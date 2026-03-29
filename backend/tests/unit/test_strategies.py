"""
策略模块单元测试
"""

import pytest
import polars as pl
import numpy as np
from datetime import datetime, timedelta

from quant_terminal.strategies import (
    DualThrustStrategy,
    RBreakerStrategy,
    DualThrustConfig,
    Signal,
    SignalType
)


class TestDualThrustStrategy:
    """Dual Thrust策略测试"""

    def test_initialization(self):
        """测试策略初始化"""
        config = DualThrustConfig(n_periods=5, k1=0.6, k2=0.6)
        strategy = DualThrustStrategy(config)

        assert strategy.n_periods == 5
        assert strategy.k1 == 0.6
        assert strategy.k2 == 0.6

    def test_name_property(self):
        """测试策略名称"""
        strategy = DualThrustStrategy()
        assert "dual_thrust" in strategy.name

    def test_validate_data(self):
        """测试数据验证"""
        strategy = DualThrustStrategy()

        # 有效数据
        valid_data = pl.DataFrame({
            "datetime": [datetime.now()],
            "open": [100],
            "high": [101],
            "low": [99],
            "close": [100.5],
        })
        assert strategy.validate_data(valid_data) is True

        # 缺少列的数据
        invalid_data = valid_data.drop("close")
        assert strategy.validate_data(invalid_data) is False

    def test_generate_signals(self):
        """测试信号生成"""
        # 创建测试数据
        dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        data = pl.DataFrame({
            "datetime": dates,
            "open": [100 + i * 0.5 for i in range(30)],
            "high": [105 + i * 0.5 for i in range(30)],
            "low": [95 + i * 0.5 for i in range(30)],
            "close": [102 + i * 0.5 for i in range(30)],
            "volume": [1000000] * 30,
            "code": ["IF0"] * 30,
        })

        strategy = DualThrustStrategy(DualThrustConfig(n_periods=5, k1=0.5, k2=0.5))
        signals = strategy.generate_signals(data)

        # 验证信号格式
        assert isinstance(signals, list)
        if signals:
            assert isinstance(signals[0], Signal)
            assert hasattr(signals[0], 'signal_type')
            assert hasattr(signals[0], 'price')

    def test_set_params(self):
        """测试参数设置"""
        strategy = DualThrustStrategy()

        strategy.set_params(n_periods=10, k1=0.7, k2=0.8)

        assert strategy.n_periods == 10
        assert strategy.k1 == 0.7
        assert strategy.k2 == 0.8


class TestRBreakerStrategy:
    """R-Breaker策略测试"""

    def test_initialization(self):
        """测试策略初始化"""
        strategy = RBreakerStrategy()
        assert strategy.break_factor == 0.35

    def test_name_property(self):
        """测试策略名称"""
        strategy = RBreakerStrategy()
        assert "rbreaker" in strategy.name

    def test_generate_signals(self):
        """测试信号生成"""
        dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        data = pl.DataFrame({
            "datetime": dates,
            "open": [100 + i * 0.5 for i in range(30)],
            "high": [105 + i * 0.5 for i in range(30)],
            "low": [95 + i * 0.5 for i in range(30)],
            "close": [102 + i * 0.5 for i in range(30)],
            "volume": [1000000] * 30,
            "code": ["IF0"] * 30,
        })

        strategy = RBreakerStrategy()
        signals = strategy.generate_signals(data)

        assert isinstance(signals, list)


class TestSignal:
    """Signal数据类测试"""

    def test_signal_creation(self):
        """测试信号创建"""
        signal = Signal(
            timestamp=datetime.now(),
            code="IF0",
            signal_type=SignalType.BUY,
            price=4000.0,
            volume=1,
            confidence=0.8
        )

        assert signal.code == "IF0"
        assert signal.signal_type == SignalType.BUY
        assert signal.price == 4000.0

    def test_signal_metadata(self):
        """测试信号元数据"""
        signal = Signal(
            timestamp=datetime.now(),
            code="IF0",
            signal_type=SignalType.SELL,
            price=4000.0,
            metadata={"reason": "breakout", "strength": 0.9}
        )

        assert signal.metadata is not None
        assert signal.metadata["reason"] == "breakout"
