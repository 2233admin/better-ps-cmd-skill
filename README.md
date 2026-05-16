# Quant Terminal — 量化交易终端

A股日内 + 加密货币量化交易系统。

## QMT Phase-1 Boundary

`k-atana` 是研究、数据、因子、回测和终端骨架；EasyXT 只作为 QMT/xtquant 执行适配层。

研究代码不得直接调用 QMT。标准链路是：

```text
k-atana trade intent -> EasyXT phase1 bridge -> sim journal / QMT adapter
```

详见：[Architecture Decision: QMT Phase-1 Boundary](docs/ARCHITECTURE_DECISION_QMT_PHASE1.md)

## 模块分类

### 📊 A股模块
| 文件 | 说明 |
|------|------|
| `data/tdx_parser.py` | 通达信数据解析 |
| `data/tdx_realtime.py` | 通达信实时行情 |
| `data/akshare_feed.py` | AKShare 多源数据 |
| `data/hrhg_feed.py` | 华融汇通数据源 |
| `trade/ths_bridge.py` | 同花顺交易桥接 |
| `trade/qmt_bridge.py` | QMT(XtQuant) 桥接 |
| `trade/ctp_bridge.py` | CTP 期货桥接 |
| `api/market.py` | A股行情 API |
| `api/order.py` | 订单 API |
| `api/portfolio.py` | 持仓组合 API |
| `api/bond.py` | 债券 API |

### 🪙 加密货币(OKX)模块
| 文件 | 说明 |
|------|------|
| `data/okx_client.py` | OKX API 客户端 |
| `data/okx_feed.py` | OKX 数据源 |
| `api/okx_api.py` | OKX REST 端点 |
| `trade/okx_bridge.py` | OKX 交易桥接 |
| `strategy/okx_t_runner.py` | OKX 做T执行器 |
| `strategy/materialist_engine.py` | 唯物引擎(矛盾检测) |
| `strategy/market_monitor.py` | 市场监控器 |
| `frontend/OkxTrading.tsx` | OKX 交易界面 |

### 🧠 AI预测模块
| 文件 | 说明 |
|------|------|
| `ai/predict.py` | 预测推理 |
| `ai/train.py` | 模型训练 |
| `ai/models/tft.py` | Temporal Fusion Transformer |
| `ai/kronos_model/kronos.py` | Kronos 时序模型 |
| `ai/kronos_model/module.py` | Kronos 模块 |
| `ai/kronos_predictor.py` | Kronos 预测器 |
| `api/ai_api.py` | AI 预测 API |

### ⚡ 策略引擎
| 文件 | 说明 |
|------|------|
| `strategy/engine.py` | 策略引擎基类 |
| `strategy/engine_optimizer.py` | 引擎优化器 |
| `strategy/factors.py` | 因子计算 |
| `strategy/signals.py` | 信号生成 |
| `strategy/t_strategy.py` | 做T策略 |
| `strategy/backtest.py` | 回测框架 |

### 🔧 基础设施
| 文件 | 说明 |
|------|------|
| `data/gpu_factors.py` | GPU因子引擎 (RTX 5090) |
| `data/indicators.py` | 技术指标库 |
| `data/store.py` | DuckDB 存储 |
| `data/feed_manager.py` | 数据源管理 |
| `data/device_manager.py` | GPU 设备管理 |
| `trade/executor.py` | 交易执行器 |
| `trade/account_reader.py` | 账户读取 |
| `trade/risk.py` | 风控模块 |

### 🌐 宏观分析
| 文件 | 说明 |
|------|------|
| `macro/briefing.py` | 宏观简报 |
| `macro/indicators.py` | 宏观指标 |
| `macro/allocation.py` | 资产配置 |
| `macro/knowledge.py` | 知识库 |
| `macro/config.py` | 宏观配置 |
| `api/macro_api.py` | 宏观 API |

### 🖥️ 前端
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
- **交易**: OKX API v5 / 同花顺 / QMT / CTP
