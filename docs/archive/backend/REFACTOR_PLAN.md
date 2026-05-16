# Quant Terminal 生产级重构计划

## 目标
将现有的原型代码重构为可维护、可测试、可开源的生产级量化交易系统。

## 最终架构

```
quant-terminal/
├── pyproject.toml              # 现代Python项目配置
├── README.md                   # 项目文档
├── LICENSE                     # 开源协议
├── Dockerfile                  # 容器化
├── .github/
│   └── workflows/
│       └── ci.yml              # CI/CD
├── configs/
│   ├── default.yaml            # 默认配置
│   └── strategies/             # 策略配置
├── src/
│   └── quant_terminal/         # 主包
│       ├── __init__.py
│       ├── core/               # 核心引擎
│       │   ├── __init__.py
│       │   ├── backtest.py     # 回测引擎
│       │   ├── portfolio.py    # 组合管理
│       │   └── metrics.py      # 绩效指标
│       ├── strategies/         # 策略模块
│       │   ├── __init__.py
│       │   ├── base.py         # 策略基类
│       │   ├── dual_thrust.py  # Dual Thrust策略
│       │   ├── rbreaker.py     # R-Breaker策略
│       │   └── adaptive.py     # 自适应策略
│       ├── data/               # 数据模块
│       │   ├── __init__.py
│       │   ├── base.py         # 数据接口基类
│       │   ├── futures.py      # 期货数据
│       │   └── storage.py      # 数据存储
│       ├── gpu/                # GPU加速
│       │   ├── __init__.py
│       │   ├── core.py         # GPU核心
│       │   ├── grid_search.py  # 网格搜索
│       │   └── kernels.py      # CUDA kernels
│       ├── optimization/       # 优化模块
│       │   ├── __init__.py
│       │   ├── grid_search.py  # CPU网格搜索
│       │   ├── random_search.py
│       │   └── ga.py           # 遗传算法
│       └── utils/              # 工具
│           ├── __init__.py
│           ├── config.py       # 配置管理
│           ├── logging.py      # 日志
│           └── validation.py   # 验证
├── tests/                      # 测试
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── scripts/                    # 运行脚本
│   ├── run_backtest.py
│   ├── run_grid_search.py
│   └── run_gpu_search.py
└── notebooks/                  # 研究笔记
    └── examples/
```

## Agent分工

### Agent 1: 架构与核心引擎
**负责**: 核心包结构、回测引擎、配置系统
**输出**:
- `src/quant_terminal/core/` 模块
- `pyproject.toml`
- `configs/` 配置系统

### Agent 2: 策略模块
**负责**: 策略基类、所有策略实现
**输出**:
- `src/quant_terminal/strategies/` 模块
- 提取5个脚本中的策略逻辑
- 统一接口

### Agent 3: 数据模块
**负责**: 数据获取、存储、接口抽象
**输出**:
- `src/quant_terminal/data/` 模块
- 统一数据访问接口

### Agent 4: GPU与优化
**负责**: GPU加速、网格搜索优化
**输出**:
- `src/quant_terminal/gpu/` 模块
- `src/quant_terminal/optimization/` 模块

### Agent 5: 测试与CI/CD
**负责**: 测试框架、CI/CD、文档
**输出**:
- `tests/` 完整测试覆盖
- `.github/workflows/ci.yml`
- `README.md` 文档

## 接口定义

### 策略基类
```python
from abc import ABC, abstractmethod
import polars as pl

class Strategy(ABC):
    """策略基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def generate_signals(self, df: pl.DataFrame) -> pl.DataFrame:
        """生成交易信号"""
        pass

    def get_params(self) -> dict:
        """获取策略参数"""
        return {}

    def set_params(self, **params):
        """设置策略参数"""
        pass
```

### 回测引擎
```python
from dataclasses import dataclass
from typing import List
import polars as pl

@dataclass
class BacktestResult:
    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    equity_curve: pl.DataFrame
    trades: pl.DataFrame

class BacktestEngine:
    """回测引擎"""

    def __init__(
        self,
        initial_capital: float = 1_000_000,
        commission: float = 0.0001,
        slippage: float = 0.0002
    ):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def run(
        self,
        data: pl.DataFrame,
        signals: pl.DataFrame
    ) -> BacktestResult:
        """运行回测"""
        pass
```

### 数据接口
```python
from abc import ABC, abstractmethod
import polars as pl
from datetime import datetime
from typing import Optional

class DataProvider(ABC):
    """数据提供者基类"""

    @abstractmethod
    def fetch(
        self,
        code: str,
        timeframe: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None
    ) -> pl.DataFrame:
        pass
```

## 依赖管理

```toml
[project]
name = "quant-terminal"
version = "0.1.0"
description = "Production-grade quantitative trading system"
requires-python = ">=3.10"

dependencies = [
    "polars>=0.20.0",
    "numpy>=1.24.0",
    "pandas>=2.0.0",
    "pyyaml>=6.0",
    "loguru>=0.7.0",
    "pydantic>=2.0.0",
    "click>=8.0.0",
]

[project.optional-dependencies]
gpu = ["torch>=2.0.0", "cupy-cuda12x"]
ml = ["scikit-learn>=1.3.0", "xgboost>=2.0.0"]
all = ["quant-terminal[gpu,ml]"]

dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "black>=23.0.0",
    "ruff>=0.1.0",
    "mypy>=1.0.0",
    "pre-commit>=3.0.0",
]
```

## 重构检查清单

- [ ] 所有模块都有 `__init__.py`
- [ ] 所有公共函数都有类型注解
- [ ] 所有模块都有文档字符串
- [ ] 代码通过 `black` 格式化
- [ ] 代码通过 `ruff` 检查
- [ ] 代码通过 `mypy` 类型检查
- [ ] 测试覆盖率 > 80%
- [ ] CI/CD 通过
- [ ] README 完整
- [ ] 示例可运行

## 时间预估

- Agent 1 (架构): 2-3 小时
- Agent 2 (策略): 3-4 小时
- Agent 3 (数据): 2-3 小时
- Agent 4 (GPU): 2-3 小时
- Agent 5 (测试): 2-3 小时
- 整合测试: 1-2 小时

总计: 约 1 天完成生产级重构
