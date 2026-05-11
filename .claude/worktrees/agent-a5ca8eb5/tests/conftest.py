"""pytest配置和共享fixture"""

import pytest
import numpy as np
import polars as pl
from datetime import datetime, timedelta
from typing import Generator
from unittest.mock import MagicMock, patch

# 添加backend到路径
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


@pytest.fixture
def sample_ohlcv_data() -> pl.DataFrame:
    """生成样本OHLCV数据用于回测测试"""
    n = 100
    base_price = 100.0
    dates = [datetime(2024, 1, 1) + timedelta(minutes=i) for i in range(n)]

    # 生成随机价格走势
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, n)
    prices = base_price * np.exp(np.cumsum(returns))

    return pl.DataFrame({
        "datetime": dates,
        "open": prices * (1 + np.random.normal(0, 0.001, n)),
        "high": prices * (1 + abs(np.random.normal(0, 0.01, n))),
        "low": prices * (1 - abs(np.random.normal(0, 0.01, n))),
        "close": prices,
        "volume": np.random.randint(1000, 10000, n),
    })


@pytest.fixture
def sample_buy_signals() -> pl.DataFrame:
    """生成样本买入信号"""
    n = 100
    dates = [datetime(2024, 1, 1) + timedelta(minutes=i) for i in range(n)]
    signals = [0] * n
    # 在第10、30、50、70、90个位置产生买入信号
    for i in [10, 30, 50, 70, 90]:
        signals[i] = 1
    # 在第20、40、60、80个位置产生卖出信号
    for i in [20, 40, 60, 80]:
        signals[i] = -1

    return pl.DataFrame({
        "datetime": dates,
        "signal": signals,
    })


@pytest.fixture
def mock_quotes() -> list[dict]:
    """生成样本行情数据"""
    return [
        {
            "code": "000001",
            "price": 10.5,
            "open": 10.0,
            "high": 10.8,
            "low": 9.9,
            "vol": 10000,
            "amount": 105000.0,
            "cur_vol": 100,
            "bid1": 10.4,
            "ask1": 10.6,
            "last_close": 10.0,
        },
        {
            "code": "000002",
            "price": 25.3,
            "open": 25.0,
            "high": 25.5,
            "low": 24.8,
            "vol": 5000,
            "amount": 126500.0,
            "cur_vol": 50,
            "bid1": 25.2,
            "ask1": 25.4,
            "last_close": 25.0,
        },
    ]


@pytest.fixture
def mock_t_strategy_config():
    """T策略配置"""
    from app.strategy.t_strategy import TStrategyConfig
    return TStrategyConfig(
        long_t_buy_deviation=-0.012,
        long_t_sell_deviation=0.008,
        short_t_sell_deviation=0.015,
        short_t_buy_deviation=0.003,
        signal_cooldown=120.0,
        min_ticks=5,  # 测试时降低要求
        volume_per_signal=100,
        max_daily_t_count=2,
    )


@pytest.fixture
def mock_strategy_engine():
    """创建mock策略引擎"""
    from app.strategy.engine import StrategyEngine
    engine = StrategyEngine()
    return engine


@pytest.fixture
def mock_executor():
    """创建mock执行器"""
    from app.trade.executor import OrderExecutor
    return OrderExecutor(mode="paper")


@pytest.fixture(autouse=True)
def reset_singletons():
    """每次测试后重置单例"""
    yield
    # 重置全局单例
    import app.strategy.engine as engine_module
    import app.strategy.t_strategy as t_strategy_module
    import app.trade.executor as executor_module

    engine_module._engine = None
    t_strategy_module._t_strategy = None
    executor_module._executor = None


@pytest.fixture
def mock_store():
    """mock数据存储"""
    with patch("app.trade.executor.get_store") as mock:
        store = MagicMock()
        mock.return_value = store
        yield store


@pytest.fixture
def mock_risk_manager():
    """mock风控管理器"""
    with patch("app.trade.executor.get_risk_manager") as mock:
        risk = MagicMock()
        risk.check_order.return_value = {"passed": True}
        mock.return_value = risk
        yield risk
