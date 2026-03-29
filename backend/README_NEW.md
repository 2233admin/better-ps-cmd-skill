# Quant Terminal - 生产级量化交易系统

[![CI](https://github.com/yourusername/quant-terminal/actions/workflows/ci.yml/badge.svg)](https://github.com/yourusername/quant-terminal/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/yourusername/quant-terminal/branch/main/graph/badge.svg)](https://codecov.io/gh/yourusername/quant-terminal)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A股日内 + 加密货币量化交易系统。基于PyTorch构建，支持GPU加速回测和参数优化。

## 功能特性

### 核心模块
- **回测引擎** (`core`): 向量化回测，支持CPU/GPU双模式
- **策略模块** (`strategies`): 策略基类 + DualThrust/RBreaker实现
- **数据模块** (`data`): 统一数据接口，支持AKShare/本地缓存
- **GPU加速** (`gpu`): CUDA并行计算，2700+ 参数/秒
- **优化模块** (`optimization`): 网格搜索/随机搜索参数优化

### 技术亮点
- **GPU加速**: CUDA 13.0支持，大规模参数搜索
- **高性能**: Polars向量化计算
- **可扩展**: 插件化策略架构
- **类型安全**: 完整的类型注解
- **容器化**: Docker支持CPU/GPU版本

## 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/yourusername/quant-terminal.git
cd quant-terminal

# 使用Poetry安装
poetry install --with dev

# 激活虚拟环境
poetry shell
```

### 使用示例

#### 1. 回测

```python
from quant_terminal.core.backtest import BacktestEngine
from quant_terminal.strategies import DualThrustStrategy
from quant_terminal.data import FuturesDataProvider

# 获取数据
provider = FuturesDataProvider()
df = provider.fetch('IF0', 'daily', start='2024-01-01')

# 创建策略
strategy = DualThrustStrategy()
signals = strategy.generate_signals(df)

# 运行回测
engine = BacktestEngine()
result = engine.run(df, signals)

print(f"总收益: {result.total_return:.2%}")
print(f"夏普比率: {result.sharpe_ratio:.2f}")
```

#### 2. GPU参数优化

```python
from quant_terminal.gpu import GPUGridSearch

# GPU网格搜索
searcher = GPUGridSearch()

param_grid = {
    'n_periods': range(3, 15),
    'k1': [0.1, 0.2, 0.3, 0.4, 0.5],
    'k2': [0.1, 0.2, 0.3, 0.4, 0.5],
}

results = searcher.search(df, param_grid, dual_thrust_strategy)
best = searcher.get_best(results)

print(f"最佳参数: {best}")
```

## 项目结构

```
quant-terminal/
├── src/
│   └── quant_terminal/
│       ├── __init__.py
│       ├── core/              # 核心引擎
│       │   ├── backtest.py    # 回测引擎
│       │   ├── portfolio.py   # 组合管理
│       │   └── metrics.py     # 绩效指标
│       ├── strategies/        # 策略模块
│       │   ├── base.py        # 策略基类
│       │   ├── dual_thrust.py # DualThrust策略
│       │   └── rbreaker.py    # RBreaker策略
│       ├── data/              # 数据模块
│       │   ├── base.py        # 数据接口基类
│       │   └── futures.py     # 期货数据
│       ├── gpu/               # GPU加速
│       │   ├── core.py        # GPU核心
│       │   └── grid_search.py # GPU网格搜索
│       ├── optimization/      # 优化模块
│       │   ├── grid_search.py # CPU网格搜索
│       │   └── random_search.py
│       └── utils/             # 工具模块
├── tests/                     # 测试
│   ├── unit/                  # 单元测试
│   ├── integration/           # 集成测试
│   └── conftest.py           # pytest配置
├── pyproject.toml            # Poetry配置
├── Dockerfile                # 容器配置
└── .github/workflows/ci.yml  # CI/CD
```

## 开发

### 运行测试

```bash
# 运行所有测试
poetry run pytest

# 运行特定测试
poetry run pytest tests/unit/test_backtest.py -v

# 覆盖率报告
poetry run pytest --cov=src/quant_terminal --cov-report=html
```

### 代码质量

```bash
# 格式化
poetry run black src/ tests/

# 静态检查
poetry run ruff check src/ tests/

# 类型检查
poetry run mypy src/
```

## Docker部署

```bash
# CPU版本
docker build --target production-cpu -t quant-terminal:cpu .
docker run -p 8000:8000 quant-terminal:cpu

# GPU版本
docker build --target production-gpu -t quant-terminal:gpu .
docker run --gpus all -p 8000:8000 quant-terminal:gpu
```

## 许可证

MIT License - 详见 [LICENSE](LICENSE)

## 免责声明

本软件仅供学习研究，不构成投资建议。使用本软件交易产生的损失，开发者不承担责任。
