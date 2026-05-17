# k-atana — 多市场量化研究平台终端

`k-atana` 是研究、数据、因子、回测、paper trading 和受控交易测试终端，不是默认自动实盘系统。

主链路：

```text
数据接入 -> 因子/特征 -> 策略/信号 -> 回测 -> paper/live_test -> 报告/前端
```

市场边界见：[Product Boundary](docs/PRODUCT_BOUNDARY.md)。
研究层边界见：[Research Architecture](docs/RESEARCH_ARCHITECTURE.md)。
A 股数据/PIT 规范见：[A-Share Data And PIT Specification](docs/ASHARE_DATA_PIT_SPEC.md)。
实验复现规范见：[Experiment Manifest Specification](docs/EXPERIMENT_MANIFEST_SPEC.md)。

## QMT Phase-1 Boundary

`k-atana` 是研究、数据、因子、回测和终端骨架；EasyXT 只作为 QMT/xtquant 执行适配层。

研究代码不得直接调用 QMT。标准链路是：

```text
k-atana trade intent -> EasyXT phase1 bridge -> sim journal / QMT adapter
```

详见：[Architecture Decision: QMT Phase-1 Boundary](docs/ARCHITECTURE_DECISION_QMT_PHASE1.md)

## 周一测试门禁

后端测试固定使用仓库内虚拟环境，避免系统 Python 版本和依赖漂移：

```bash
scripts/test-katana.sh all
```

项目当前按两个主市场收口：数字货币和 A 股。期货、GPU 搜索和老宏观/Streamlit 入口已收进 `legacy/` 冷区，不进入默认测试门禁。目录边界见：[Project Structure](docs/PROJECT_STRUCTURE.md)。

```powershell
cd C:\Users\Administrator\projects\k-atana\backend
.\run_tests.ps1
```

默认门禁覆盖回测核心、策略信号、product boundary 和 trade intent 合约。EasyXT bridge 联调是可选外部服务测试：

```powershell
$env:KATANA_EASYXT_BRIDGE_URL = "http://127.0.0.1:8000"
.\run_tests.ps1 -Target "tests\integration"
```

`k-atana` 只生成 broker-agnostic trade intent；QMT 下单、dry-run/sim journal 和幂等由 EasyXT phase-1 bridge 承担。

## 模块分类

### A股模块
| 文件 | 说明 |
|------|------|
| `data/tdx_parser.py` | 通达信数据解析 |
| `data/tdx_realtime.py` | 通达信实时行情 |
| `data/akshare_feed.py` | AKShare 多源数据 |
| `data/hrhg_feed.py` | 华融汇通数据源 |
| `trading/adapters/qmt/` | QMT/EasyXT 边界适配 |
| `api/ashare/market.py` | A股行情 API |
| `api/order.py` | 订单 API |
| `api/portfolio.py` | 持仓组合 API |
| `api/ashare/bond.py` | 债券 API |

### 加密货币(OKX)模块
| 文件 | 说明 |
|------|------|
| `data/okx_client.py` | OKX API 客户端 |
| `data/okx_feed.py` | OKX 数据源 |
| `api/crypto/okx_api.py` | OKX REST 端点 |
| `trading/adapters/okx/` | OKX 受限 live_test 适配 |
| `strategy/okx_t_runner.py` | OKX 做T执行器 |
| `strategy/materialist_engine.py` | 唯物引擎(矛盾检测) |
| `strategy/market_monitor.py` | 市场监控器 |
| `frontend/OkxTrading.tsx` | OKX 交易界面 |

### AI预测模块
| 文件 | 说明 |
|------|------|
| `ai/predict.py` | 预测推理 |
| `ai/train.py` | 模型训练 |
| `ai/models/tft.py` | Temporal Fusion Transformer |
| `ai/kronos_model/kronos.py` | Kronos 时序模型 |
| `ai/kronos_model/module.py` | Kronos 模块 |
| `ai/kronos_predictor.py` | Kronos 预测器 |
| `api/ai_api.py` | AI 预测 API |

### 策略引擎
| 文件 | 说明 |
|------|------|
| `strategy/engine.py` | 策略引擎基类 |
| `strategy/engine_optimizer.py` | 引擎优化器 |
| `strategy/factors.py` | 因子计算 |
| `strategy/signals.py` | 信号生成 |
| `strategy/t_strategy.py` | 做T策略 |
| `strategy/backtest.py` | 回测框架 |

### 基础设施
| 文件 | 说明 |
|------|------|
| `data/gpu_factors.py` | GPU因子引擎 (RTX 5090) |
| `data/indicators.py` | 技术指标库 |
| `data/store.py` | DuckDB 存储 |
| `data/feed_manager.py` | 数据源管理 |
| `data/device_manager.py` | GPU 设备管理 |
| `trading/executor.py` | paper 和边界执行器 |
| `trading/risk.py` | 风控模块 |
| `trade/*` | 迁移期兼容 wrapper |

### 宏观分析
| 文件 | 说明 |
|------|------|
| `macro/briefing.py` | 宏观简报 |
| `macro/indicators.py` | 宏观指标 |
| `macro/allocation.py` | 资产配置 |
| `macro/knowledge.py` | 知识库 |
| `macro/config.py` | 宏观配置 |
| `api/macro_api.py` | 宏观 API |

### 前端
| 文件 | 说明 |
|------|------|
| `frontend/OkxTrading.tsx` | OKX 交易界面 |
| `frontend/Chart.tsx` | K线图表 |
| `frontend/AIPanel.tsx` | AI 预测面板 |
| `frontend/Portfolio.tsx` | 持仓管理 |
| `frontend/OrderBook.tsx` | 订单簿 |
| `frontend/Backtest.tsx` | 回测界面 |
| `frontend/MacroBriefing.tsx` | 宏观简报 |
| `frontend/Strategy.tsx` | 策略管理 |
| `frontend/api.ts` | API 客户端 |

## 技术栈

- **后端**: Python 3.11 + FastAPI + DuckDB + PyTorch
- **前端**: Next.js 14 + Tailwind CSS
- **GPU**: RTX 5090, 20因子×6000股 <2ms
- **交易测试**: paper / OKX API v5 live_test / EasyXT-QMT boundary
