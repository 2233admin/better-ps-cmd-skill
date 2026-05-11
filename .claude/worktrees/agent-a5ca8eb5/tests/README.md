# Quant Terminal

## 测试快速参考

### 运行测试

```bash
# 运行所有测试
pytest

# 运行单元测试
pytest tests/unit/ -v

# 运行集成测试
pytest tests/integration/ -v

# 运行特定测试文件
pytest tests/unit/test_backtest.py -v

# 运行带覆盖率
pytest --cov=backend/app --cov-report=html --cov-report=term

# 并行运行测试
pytest -xvs -n auto
```

### 测试结构

```
tests/
├── conftest.py           # pytest配置和fixture
├── unit/                 # 单元测试
│   ├── test_backtest.py  # 回测引擎测试
│   └── test_strategies.py # 策略测试
└── integration/          # 集成测试
    └── test_workflow.py  # 工作流测试
```

### 覆盖率要求

- 总体覆盖率: >80%
- 核心模块: >90%
  - `strategy/backtest.py`
  - `strategy/engine.py`
  - `trade/executor.py`

### 测试标记

- `@pytest.mark.unit`: 单元测试
- `@pytest.mark.integration`: 集成测试
- `@pytest.mark.slow`: 慢测试（跳过: `-m "not slow"`）
