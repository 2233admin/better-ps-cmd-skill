"""
测试配置
"""

import pytest
import sys
from pathlib import Path

# 添加src到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def sample_ohlcv_data():
    """提供示例OHLCV数据"""
    import polars as pl
    from datetime import datetime, timedelta

    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(100)]

    return pl.DataFrame({
        "datetime": dates,
        "open": [100 + i * 0.1 for i in range(100)],
        "high": [101 + i * 0.1 for i in range(100)],
        "low": [99 + i * 0.1 for i in range(100)],
        "close": [100.5 + i * 0.1 for i in range(100)],
        "volume": [1000000] * 100,
    })


@pytest.fixture
def sample_signals():
    """提供示例信号数据"""
    import polars as pl
    from datetime import datetime, timedelta

    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(100)]
    signals = [0] * 100
    signals[20] = 1  # 买入
    signals[50] = -1  # 卖出
    signals[80] = 0  # 平仓

    return pl.DataFrame({
        "datetime": dates,
        "signal": signals,
    })
