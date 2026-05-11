# Quant Terminal - 量化交易终端

[![CI](https://github.com/yourusername/quant-terminal/actions/workflows/ci.yml/badge.svg)](https://github.com/yourusername/quant-terminal/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/yourusername/quant-terminal/branch/main/graph/badge.svg)](https://codecov.io/gh/yourusername/quant-terminal)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A股日内 + 加密货币量化交易系统。基于FastAPI构建，支持多策略并行运行、实时回测和AI预测。

## 功能特性

### 核心功能
- **多策略引擎**: 支持动量突破、均值回归、溢价套利、配对交易、做T策略
- **实时行情**: 通达信/AKShare/OKX多数据源
- **回测系统**: 基于Polars的向量化回测，支持多参数优化
- **交易执行**: 模拟盘/同花顺/QMT/OKX多通道支持
- **AI预测**: Temporal Fusion Transformer时序预测模型
- **风控系统**: 实时仓位管理和风险监控

### 技术亮点
- **GPU加速**: RTX 5090支持，20因子×6000股 <2ms
- **高性能**: Polars向量化计算，DuckDB时序存储
- **可扩展**: 插件化策略架构，支持热加载
- **容器化**: Docker支持CPU/GPU版本

## 快速开始

### 环境要求
- Python 3.11+
- Poetry 1.7+
- (可选) NVIDIA GPU + CUDA 12.1+

### 安装

```bash
# 克隆仓库
git clone https://github.com/yourusername/quant-terminal.git
cd quant-terminal

# 安装依赖
poetry install --with dev

# 激活虚拟环境
poetry shell
```

### 配置

```bash
# 复制配置文件
cp .env.example .env

# 编辑配置
vim .env
```

关键配置项：
```env
# 交易模式: paper | ths | qmt | okx
TRADE_MODE=paper

# 数据源配置
TDX_SERVER=119.147.212.81:7709
OKX_API_KEY=your_api_key
OKX_SECRET=your_secret

# 风控参数
MAX_POSITION_PCT=0.8
MAX_SINGLE_ORDER_PCT=0.1
```

### 运行

```bash
# 启动后端服务
python -m uvicorn backend.app.main:app --reload

# 或使用Docker
docker-compose up -d
```

访问 http://localhost:8000/docs 查看API文档。

## 使用示例

### 启动策略

```python
import requests

# 启动动量突破策略
response = requests.post("http://localhost:8000/api/v1/strategies/momentum_breakout/start", json={
    "min_confidence": 0.6,
    "vol_threshold": 2.5
})
print(response.json())
```

### 运行回测

```python
from app.strategy.backtest import VectorBacktester
import polars as pl

# 加载数据
data = pl.read_parquet("data/000001.parquet")
signals = pl.read_parquet("signals/000001_signals.parquet")

# 运行回测
backtester = VectorBacktester(
    initial_capital=1_000_000,
    commission=0.0005,
    slippage=0.001
)
result = backtester.run(data, signals)

print(f"总收益: {result.total_return:.2%}")
print(f"夏普比率: {result.sharpe_ratio:.2f}")
print(f"最大回撤: {result.max_drawdown:.2%}")
```

### 做T策略

```python
from app.strategy.t_strategy import TStrategy, TStrategyConfig

# 配置策略
config = TStrategyConfig(
    long_t_buy_deviation=-0.012,
    long_t_sell_deviation=0.008,
    signal_cooldown=120.0
)
strategy = TStrategy(config)

# 设置目标
strategy.set_targets([
    {"code": "000001", "mode": "long_t"},
    {"code": "000002", "mode": "short_t"}
])
strategy.set_positions({"000001": 1000, "000002": 500})

# 处理行情
signals = strategy.on_quotes(quotes)
for sig in signals:
    print(f"{sig.direction} {sig.code} @ {sig.price} - {sig.reason}")
```

## 项目结构

```
quant-terminal/
├── backend/
│   └── app/
│       ├── api/              # REST API端点
│       ├── strategy/         # 策略引擎
│       │   ├── backtest.py   # 回测框架
│       │   ├── engine.py     # 策略调度引擎
│       │   ├── t_strategy.py # 做T策略
│       │   └── signals.py    # 信号生成
│       ├── trade/            # 交易执行
│       │   ├── executor.py   # 订单执行器
│       │   └── risk.py       # 风控管理
│       ├── data/             # 数据层
│       │   ├── store.py      # DuckDB存储
│       │   └── indicators.py # 技术指标
│       ├── ai/               # AI预测模块
│       │   ├── predict.py    # 预测推理
│       │   └── models/       # 模型定义
│       └── main.py           # FastAPI入口
├── tests/
│   ├── unit/                 # 单元测试
│   └── integration/          # 集成测试
├── frontend/                 # Next.js前端
├── Dockerfile                # 容器配置
└── docker-compose.yml        # Docker编排
```

## 开发指南

### 本地开发

```bash
# 安装开发依赖
poetry install --with dev

# 运行测试
pytest tests/ -v --cov=backend/app --cov-report=html

# 代码格式化
black backend/ tests/
ruff check --fix backend/ tests/

# 类型检查
mypy backend/ --ignore-missing-imports

# 启动开发服务器
python -m uvicorn backend.app.main:app --reload
```

### 添加新策略

1. 在 `backend/app/strategy/` 创建策略文件
2. 继承基础策略类或实现信号生成函数
3. 在 `engine.py` 中注册策略
4. 添加对应的单元测试

示例：

```python
# backend/app/strategy/my_strategy.py
from .signals import Signal

def my_strategy(quotes: list[dict], params: dict) -> list[Signal]:
    signals = []
    for q in quotes:
        if should_buy(q, params):
            signals.append(Signal(
                code=q["code"],
                direction="buy",
                price=q["price"],
                volume=100,
                strategy="my_strategy",
                reason="Custom logic",
                confidence=0.7
            ))
    return signals
```

### 测试

```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/unit/test_backtest.py -v

# 运行带覆盖率报告
pytest --cov=backend/app --cov-report=term-missing --cov-fail-under=80

# 运行性能测试
pytest tests/ -m slow --timeout=300
```

## Docker部署

### 构建镜像

```bash
# CPU版本
docker build --target production-cpu -t quant-terminal:cpu .

# GPU版本
docker build --target production-gpu --build-arg GPU_SUPPORT=true -t quant-terminal:gpu .

# 开发版本
docker build --target development -t quant-terminal:dev .
```

### 运行容器

```bash
# CPU版本
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/.env:/app/.env \
  --name quant-terminal \
  quant-terminal:cpu

# GPU版本
docker run -d \
  --gpus all \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/.env:/app/.env \
  --name quant-terminal-gpu \
  quant-terminal:gpu
```

### Docker Compose

```bash
# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f backend

# 停止服务
docker-compose down
```

## CI/CD

本项目使用GitHub Actions进行持续集成：

- **Lint**: Black代码格式化、Ruff静态检查、MyPy类型检查
- **Test**: pytest单元测试和集成测试，覆盖率>80%
- **Security**: Bandit安全扫描、Safety依赖检查
- **Build**: Docker镜像构建和推送

查看 `.github/workflows/ci.yml` 了解详细配置。

## 性能基准

| 操作 | 性能指标 |
|------|----------|
| 因子计算 (20因子×6000股) | <2ms (RTX 5090) |
| 回测 (10万条K线) | <100ms |
| 信号生成 | <1ms |
| 订单提交 | <10ms |

## 贡献指南

1. Fork本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 创建Pull Request

### 代码规范

- 遵循PEP 8规范
- 使用类型注解
- 编写单元测试（覆盖率>80%）
- 更新相关文档

## 许可证

本项目采用MIT许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 免责声明

本软件仅供学习和研究使用，不构成投资建议。使用本软件进行交易产生的任何损失，开发者不承担责任。

## 联系方式

- 项目主页: https://github.com/yourusername/quant-terminal
- 问题反馈: https://github.com/yourusername/quant-terminal/issues
- 邮件: your.email@example.com

---

**风险提示**: 量化交易涉及高风险，可能导致本金损失。请在使用前充分了解相关风险。
