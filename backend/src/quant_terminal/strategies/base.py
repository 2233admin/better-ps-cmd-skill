"""
策略基类定义

所有策略必须继承Strategy基类并实现必要接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Any
import polars as pl
import numpy as np


class SignalType(Enum):
    """信号类型"""
    BUY = 1
    SELL = -1
    HOLD = 0
    CLOSE = 2  # 平仓信号


@dataclass
class Signal:
    """交易信号"""
    timestamp: Any  # datetime
    code: str
    signal_type: SignalType
    price: float
    volume: int = 1
    confidence: float = 1.0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class StrategyConfig:
    """策略配置基类"""
    name: str = "base_strategy"
    description: str = ""

    # 风险控制参数
    max_position_size: int = 10
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05

    # 交易成本
    commission: float = 0.0001
    slippage: float = 0.0002

    def to_dict(self) -> Dict[str, Any]:
        return {
            k: v for k, v in self.__dict__.items()
        }


class Strategy(ABC):
    """策略基类

    所有策略必须继承此类并实现generate_signals方法

    Example:
        class MyStrategy(Strategy):
            @property
            def name(self) -> str:
                return "my_strategy"

            def generate_signals(self, df: pl.DataFrame) -> List[Signal]:
                # 实现信号生成逻辑
                return signals
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        """初始化策略

        Args:
            config: 策略配置
        """
        self.config = config or StrategyConfig()
        self._params: Dict[str, Any] = {}
        self._is_initialized: bool = False

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        pass

    @property
    def params(self) -> Dict[str, Any]:
        """获取当前参数"""
        return self._params.copy()

    def set_params(self, **params) -> "Strategy":
        """设置策略参数

        Args:
            **params: 参数键值对

        Returns:
            self (链式调用)
        """
        self._params.update(params)
        return self

    def get_params(self) -> Dict[str, Any]:
        """获取策略参数"""
        return self._params.copy()

    @abstractmethod
    def generate_signals(self, df: pl.DataFrame) -> List[Signal]:
        """生成交易信号

        Args:
            df: OHLCV数据

        Returns:
            信号列表
        """
        pass

    def generate_signals_batch(
        self,
        data_dict: Dict[str, pl.DataFrame]
    ) -> Dict[str, List[Signal]]:
        """批量生成多个品种的信号

        Args:
            data_dict: 品种代码到数据的映射

        Returns:
            品种代码到信号列表的映射
        """
        results = {}
        for code, df in data_dict.items():
            results[code] = self.generate_signals(df)
        return results

    def on_bar(self, bar: Dict[str, Any]) -> Optional[Signal]:
        """逐根K线处理 (用于实盘)

        Args:
            bar: 单根K线数据

        Returns:
            信号或None
        """
        # 默认实现，子类可覆盖
        return None

    def on_tick(self, tick: Dict[str, Any]) -> Optional[Signal]:
        """逐笔处理 (用于高频交易)

        Args:
            tick: 单笔tick数据

        Returns:
            信号或None
        """
        # 默认实现，子类可覆盖
        return None

    def initialize(self) -> None:
        """初始化策略

        在回测或实盘开始前调用
        """
        self._is_initialized = True

    def cleanup(self) -> None:
        """清理策略资源

        在回测或实盘结束时调用
        """
        self._is_initialized = False

    def validate_data(self, df: pl.DataFrame) -> bool:
        """验证输入数据格式

        Args:
            df: 输入数据

        Returns:
            数据是否有效
        """
        required_cols = {'datetime', 'open', 'high', 'low', 'close'}
        return required_cols.issubset(set(df.columns))

    def get_state(self) -> Dict[str, Any]:
        """获取策略状态 (用于序列化)

        Returns:
            状态字典
        """
        return {
            'name': self.name,
            'params': self._params,
            'config': self.config.to_dict() if self.config else {}
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        """恢复策略状态

        Args:
            state: 状态字典
        """
        if 'params' in state:
            self._params = state['params']

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"
