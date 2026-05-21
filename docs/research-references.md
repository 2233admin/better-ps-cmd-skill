# Quantamental Thematic Investing — 学术与工具参考

系统定位: **Quantamental Thematic Investing** (量化广度筛选 + 叙事深度验证 + 人机融合决策)

## 核心论文

### Multi-Agent Trading Systems

| 论文 | 作者 | 年份 | 来源 | 核心发现 |
|------|------|------|------|---------|
| Algorithmic Trading, AI, and Securities Markets | — | 2025 | SSRN 6746480 | 多 agent 交易公司架构（7 种 agent 分工），broadcast/direct/negotiation 三种通信范式；agent 数量与冲突 objective 的 trade-off |
| TradingAgents | TauricResearch | 2025 | GitHub 76k★ | 全交易公司多 agent 模拟（Apache-2.0），fundamental/sentiment/technical/researcher/trader/risk/PM agents |

### 叙事驱动市场

| 论文 | 作者 | 年份 | 期刊 | 核心发现 |
|------|------|------|------|---------|
| Narrative Economics | Robert Shiller | 2017 | AER 107(4) | 病毒式叙事驱动市场，非基本面。获2013诺贝尔奖 |
| Investor Sentiment: Wisdom or Power of Words | - | 2025 | IRFA | 叙事传播力（非群体智慧）预测个股收益 |
| Backtesting Sentiment Signals | - | 2025 | arXiv 2507.03350 | 情绪回归模型28月累计+50.63% |

### A股市场无效性

| 论文 | 作者 | 年份 | 期刊 | 核心发现 |
|------|------|------|------|---------|
| Information Asymmetry and Market Efficiency in China | Hu & Prigent | 2019 | Economic Modelling | 信息不对称+羊群交易双重破坏效率 |
| Policy Interventions and China's Stock Market | Wei Xiong | 2025 | BFI WP 2025-22 | 政府=第三方参与者，政策噪声压过基本面 |
| Market Manipulation and Behavioral Bias | - | 2025 | JBEF | A股操纵诱发散户行为偏差，形成反馈环 |
| Retail Investor Trading Behaviors | Tsinghua PBCSF | 2023 | Annual Reviews | 散户年化异常收益-4.4%，活跃交易者最差 |

### 人机融合

| 论文 | 作者 | 年份 | 期刊 | 核心发现 |
|------|------|------|------|---------|
| From Man vs. Machine to Man + Machine | Cao, Jiang, Wang, Yang | 2024 | JFE Vol.160 | 人机组合消除90%人类错误+40%AI错误 |
| Quant vs. Fundamental Analysis | SSGA | 2015 | 机构报告 | "Quantamental"混合策略优于纯量化或纯基本面 |

### LLM驱动选股

| 论文 | 作者 | 年份 | 来源 | 核心发现 |
|------|------|------|------|---------|
| MarketSenseAI 2.0 | - | 2025 | arXiv 2502.00415 | GPT-4+RAG: S&P100上+125.9% vs 指数73.5% |
| AlphaAgent | Tang et al. | 2025 | arXiv 2502.16789 | LLM因子挖掘: CSI500 +11%年化超额, IR=1.5 |
| Automated Equity Research | - | 2024 | arXiv 2407.18327 | LLM可自动化80%研报内容 |
| Multi-Agent Investment Reports | - | 2025 | ACL FinNLP | 多Agent生成完整研报(摘要/估值/催化剂/风险) |

### 量化地震实证

| 事件 | 来源 | 数据 |
|------|------|------|
| 2024 A股量化地震 | MSCI | 平均亏-8.6%, AUM缩水42%, 小盘因子拥挤 |
| 量化基金监管 | CSRC | HFT注册+仓位披露+做空限制 |

### Regime Detection / HMM

| 论文 | 作者 | 年份 | 来源 | 核心发现 |
|------|------|------|------|---------|
| POMONA | NeurIPS 2024 | — | — | SOTA panel HMM，状态切换的因果解释 |
| Regime-Switching | Hamilton | 1989 | Econometrica | 经典 Markov switching model |
| GPU HMM benchmark | k-atana XAR-466/467 | 2026 | internal | B=7504 × T=1889，GPU 14-19x 提速；feature-scale 崩溃问题 |
| Multivariate HMM slow-bear | k-atana XAR-465 | 2026 | internal | 单变量 HMM 在 A 股慢熊上 fail（2018/2021-22），需多变量扩展 |

## 可用工具与轮子

### 金融NLP

| 工具 | 语言 | 用途 | GitHub |
|------|------|------|--------|
| FinBERT2 (valuesimplex) | 中文 | 32B token中文金融语料预训练, 胜GPT-4 9.7-12.3% | valuesimplex/FinBERT |
| FinGPT | 中英 | 开源金融LLM, 支持情绪分析指令微调 | AI4Finance-Foundation/FinGPT |
| FinRobot | 英文 | LLM研报自动生成, Financial CoT | AI4Finance-Foundation/FinRobot |

### 技术分析

| 工具 | 用途 | PyPI |
|------|------|------|
| smartmoneyconcepts | ICT/SMC检测 (OB/FVG/BOS/CHoCH) | `pip install smartmoneyconcepts` |
| smart-money-concept | ICT分析+图表 | `pip install smart-money-concept` |
| KunQuant | Alpha101编译器, 170x加速 | `pip install KunQuant` |

### 量化框架

| 工具 | 用途 | GitHub |
|------|------|--------|
| Microsoft Qlib | AI量化全流程 (Alpha158因子+ML) | microsoft/qlib |
| FinRL | 金融强化学习 (PPO/A2C/SAC) | AI4Finance-Foundation/FinRL |
| AlphaAgent | LLM自动挖因子 | RndmVariableQ/AlphaAgent |
| Alpha-101-GTJA-191 | A股量价因子合集 | wpwpwpwpwpwpwpwpwp/Alpha-101-GTJA-191 |

### 知识图谱

| 工具 | 用途 | GitHub |
|------|------|--------|
| stock-knowledge-graph | A股知识图谱 (行业/概念/高管) | lemonhu/stock-knowledge-graph |
| TradeTheEvent | 事件驱动交易 (ACL 2021) | Zhihan1996/TradeTheEvent |

## 国内头部量化机构竞品调研（2026-05-21）

> 核心结论：**国内头部几乎全部黑箱**——唯一真正可见的是幻方，因为它拆出了 DeepSeek AI 研究部门必须开源。其余机构（明汯、启林、九坤等）零公开技术文档。

### 幻方 (High-Flyer) + DeepSeek

| 维度 | 可见信息 |
|------|---------|
| 背景 | 2016年梁文锋创立，早期算法多因子量价模型，逐步 ML 化。Fire-Flyer I (~2亿) / Fire-Flyer II (~10亿, 万卡 A100)。2025年收益率 56.6%（国内量化第二），2024年10月市场中性产品因做空挤压清盘 |
| GPU 基础设施 | 3FS 分布式文件系统：6.6 TiB/s 读吞吐，180 存储节点，SSD + RDMA + CRAQ；3FS 还作 KVCache tier（40 GiB/s 读） |
| AI 训练栈 | HAI-LLM：8D 并行（Expert + Data + Tensor + Pipeline + Sequence + ZeRD + CPU-offload + Checkpointing），2048 H800 |
| CUDA Kernels | FlashMLA（660 TFLOPS 密集 / 410 TFLOPS 稀疏 FP8 KV cache）、DeepGEMM（1550 TFLOPS FP8 JIT）、DeepEP（NCCL MoE Expert-Parallel）、DualPipe（双向流水线，减少 bubble） |
| 模型训练 | GRPO（Group Relative Policy Optimization）训练 DeepSeek-R1-Zero，涌现推理能力，零监督微调。V3 训练成本 ~$6M vs GPT-4 ~$100M |
| 量化交易栈 | **零公开**——DeepSeek 是 AI 公司必须开源，量化交易部分完全黑箱 |

**关键洞察**：幻方的竞争优势在于**硬件基础设施 + LLM 训练**（DeepSeek 部分可见），**量化策略本身完全不可见**。10,000 A100 的集群可以并行跑因子挖掘，但没公开他们用这个集群做什么因子。

### 明汯 (Minghong Investment, MHC)

| 维度 | 可见信息 |
|------|---------|
| 背景 | 裘慧明（UPenn 物理 PhD，历任 Millennium Management）创立，谢晗宇（前 Citadel Securities）任投资总监。2020年管理规模 >1000亿，2024年约 $7B AUM |
| 方法论 | **中高频统计套利**，风格风险因子（growth / momentum）作 alpha 信号。创始人学术背景暗示第一性原理金融建模 |
| 技术栈 | **零公开**——国际人才背景说明接触过 Citadel/Millennium 的最佳实践，但具体实现不明 |
| 已知弱点 | 2021年Q1 亏损 27%（基金报告披露），波动率峰值期风控缺口 |

### 启林 (Qilin) / 九坤 (Jiukun) / 鸣熙 / 诚奇 / 灵均

| 机构 | 可见过滤 |
|------|---------|
| 启林 | 零公开。业内定位：**高频 + 中频**策略，市场 microstructure 研究能力强 |
| 九坤 | 零公开。业内定位：多策略（alpha 捕获 + 做市 + CTA），最大 AUM 之一 |
| 鸣熙/诚奇/灵均 | 零公开。中大规模，AUM ~数十亿，方法论不透明 |

### 竞品共性模式（从可见信息推断）

| 维度 | 共性 |
|------|------|
| 人才 | 重金从清北复交物理/数学 PhD、美国对冲基金（Citi/MS）招募 |
| 因子方法 | 多因子量价模型起步，ML/LLM 是当前转型方向 |
| 策略类型 | 中高频统计套利、做市、多策略为主流 |
| 基础设施 | 定制 HPC 集群（DeepSeek 除外均为黑箱） |

### k-atana 可差异化维度（vs 国内头部完全黑箱）

| 维度 | 国内头部 | k-atana |
|------|---------|---------|
| **PIT 数据契约** | 内部可能有，不公开 | **显式设计原则**（event_time + available_at + source_updated_at）——研究可信度的制度保障 |
| **Rust 原生信号管线** | 黑箱，Python 为主流 | tdxcli-rs（CUDA Phase 3）+ 架构边界强制（research/trading 分离） |
| **DuckDB + TimescaleDB 数据仓库** | 黑箱 | `warehouse.duckdb` + `import dw` 可复现数据管线 |
| **全量 A 股 GPU batched HMM** | 黑箱 | XAR-466/467：B=7504 × T=1889，14-19x CPU 提速，6.4/8 状态激活 |
| **A股 + 加密跨资产** | A 股为主 | A-share PIT lake + OKX crypto 双市场 |
| **架构决策显式化** | 黑箱 | `EXTERNAL_WHEEL_AUDIT.md` + `OLD_WHEEL_REUSE_MAP.md` 制度化轮子评估 |

### 关键盲点（国内头部看不到的部分）

> 量化基金的**核心 IP 几乎全部不公开**，公开可见的都是冰山一角：

1. **因子研究管线**：特征工程、因子衰减分析、regime 识别方法——零可见
2. **执行和 microstructure**：订单执行算法、交易所路由、延迟架构、暗池——零可见
3. **风险管理框架**：回撤限制、压力测试、short-squeeze 应对——零可见（幻方 2024-10 清盘暴露了这一点）
4. **Regime 检测方法**：HMM 或其他 regime 模型的具体实现——零可见
5. **另类数据**：卫星图像、信用卡、Web 抓取等——零可见（k-atana 的 PIT lake 是显式回答）
6. **组合构建**：因子权重、优化算法（风险平价 vs 均值方差）——零可见
7. **真实护城河**：替代数据 + 超低延迟执行基础设施 + 数十年 live 迭代的因子库

**推断**：qlib 等开源工具抹平了因子研究层面的差距；**执行基础设施**是剩余的护城河（毫秒级 vs 微秒级的差距）。

### 竞品可见性总结

```
幻方 (DeepSeek):     ████████░░ 80% 可见（AI 研究必须开源）
明汯:               █░░░░░░░░░ 10% 可见（基金报告 + 人员背景）
启林/九坤/其余:      ░░░░░░░░░░  0% 可见（完全黑箱）
```

### 资料来源

- Wikipedia: High-Flyer（宁波幻方量化）、Minghong Investment
- GitHub: deepseek-ai/DeepSeek-V3、3FS、FlashMLA、DeepGEMM、DeepEP、DualPipe、DeepSeek-R1
- Hugging Face: deepseek-ai organization（V3/V4 系列规格，2048 H800，8D 并行）

---

## 系统架构理论依据

```
GPU因子引擎 (39因子)     ← 量化广度: 5000→50 (AI强项, Cao 2024)
      ↓
ICT结构分析              ← 微观验证: 价格位置+资金结构
      ↓
叙事评分引擎             ← 深度验证: 独占性+催化剂+故事逻辑 (Shiller 2017)
      ↓
统一扫描器               ← Quantamental融合 (SSGA报告)
      ↓
人脑决策                 ← 最终拍板: 无形资产+特殊情况 (Cao 2024: 人在此优于AI)
```

## 5条深度缺口分析（2026-05-21）

> 竞品调研 + 执行基础设施 + A股微观结构 + Regime检测 + PIT vs Naive 实验设计

---

### 缺口1：执行基础设施（延迟 profile）

**现状**：150-450ms，属于"高延迟零售量化"层级。

| 层级 | 延迟目标 | 技术 | k-atana |
|------|---------|------|---------|
| 人类手动 | 1-10s | 键盘下单 | — |
| 零售算法 | ~100ms | Python REST | — |
| **机构零售 (mini-HFT)** | **5-50ms** | C++/FPGA/co-lo | ❌ 未达到 |
| 对冲基金 (Prop) | 0.1-5ms | C++/kernel bypass | ❌ |
| HFT (Jane Street) | <100μs | FPGA/微波 | ❌ |
| 交易所 co-lo (Citadel) | <10μs | FPGA+微波+定制硬件 | ❌ |

**到机构零售层级的差距：30-100倍**。核心障碍：

- `urlopen` 同步 HTTP，无异步无连接池
- EasyXT 是外部 Windows 进程，IPC 开销固有
- ccxt 通用层对 OKX 路径增加额外翻译开销
- 无共置、无 kernel bypass、无 FPGA
- XAR-481 dry_run 仍在5处未移除（真实订单从未发出）

**补齐路径**：直接 DMA（替换 EasyXT）+ 共置（深交所期货大厦）+ C++ 热路径重写 + 连接池 + 延迟插桩（p50/p95/p99）。

**文献依据**：Daniel et al. (2008) — 基准偏差使 Sharpe 上浮 8%/年；执行延迟每增加 10ms，HFT 策略夏普比下降约 0.1。

---

### 缺口2：A股微观结构约束未建模（12项缺口）

当前回测本质上是**"假设所有股票每日都可自由交易"**。ASHARE_DATA_PIT_SPEC.md 明确要求建模以下约束，但均未实现：

| 缺口 | 类型 | 严重性 |
|------|------|--------|
| T+1 卖出约束未建模 | 回测模型 | **高** — 频繁翻转时策略无法执行 |
| 涨跌停执行约束未建模 | 回测模型 | **高** — `tradability_status_pit.parquet` 不在 lake 中 |
| 停牌/ST 状态未建模 | 回测模型 | **高** — baostock 有数据但未接入 |
| 融资暴增反转数据不足 | 数据 | **高** — 仅 172 行（2025-2026），无法统计推断 |
| SOE confounder 未控制 | 研究设计 | 中 — 信贷脉冲中混入 SOE 政策效应 |
| 涨跌停密度未纳入 HMM | 特征工程 | 中 — 涨跌停数量是 A 股独特 regime 指标 |
| OFI 涨跌停日处理缺失 | 信号逻辑 | **高** — OFI 在涨跌停日完全失效 |
| 指数期货对冲未设计 | 策略设计 | 中 — 融券不可用但 IC/IM 期货对冲未建模 |
| 因子半衰期未分析 | 因子评价 | 中 — 无因子衰减速度分析 |
| 最小成交金额/手数 | 执行模型 | 低 |
| 滑点随流动性变化 | 执行模型 | 中 |
| 社融数据 available_at 时滞 | PIT 违规 | 高 — 社融 T+14 发布 vs 隐含 T 日使用 |

**立即可做（无需新数据）**：

1. 回测标签加注"research-only" + 注明 T+1 和涨跌停约束未建模
2. 融资反转因子从"13 因子"移出（数据不足）
3. HMM 加入涨跌停密度特征（辅助检测极端 bear regime，可能解决慢熊漏检）
4. OFI 实现中明确加入涨跌停过滤：`if limit_up or limit_down: skip_OFI_signal()`

**中期（需数据接入）**：

5. 接入 baostock 历史停牌/涨跌停数据
6. 实现 T+1 仓位追踪
7. SOE 持仓比例因子

---

### 缺口3：Regime 检测方法差距

**现状**：单变量 Gaussian HMM 在慢熊（2018/2021-22）上 fail，MV1 多元扩展已改善但 2018 仍无解。

**国内头部实际做法**（从公开信息和社区模式推断）：

| 方法 | 国内头部 | k-atana |
|------|---------|---------|
| 多信号 OR 门（MA200 + breadth + margin + macro）| ✅ 主流做法 | ❌ 未实现 |
| 波动率百分位 regime 门 | ✅ 通用 | ❌ 未纳入 HMM |
| 融资余额变化率 | ✅ A 股特色 | ❌ 未实现 |
| 政策 regime overlay | ✅ A 股特色 | ❌ 未实现 |
| XGBoost/LightGBM 分类器 | ✅ 头部在用 | ❌ 未实现 |
| Zhang et al. Coupled HMM（新闻+跨股关联）| 学术方向 | ❌ 未实现 |
| MS-VAR/Hamilton 框架 | 学术标准 | 计划（Path B）|

**学术文献**（可引用）：

- Zhang et al. (2018) arXiv:1809.00306 — "Extended Coupled HMM over Multi-Sourced Data"，A 股 2016，融合新闻事件+跨股相关
- Liu et al. (2021) arXiv:2104.09700 — "Stock Market Trend Analysis Using HMM and LSTM"，XGBoost-HMM 优于纯 GMM-HMM
- Hamilton (1989) — Markov switching VAR，学术标准

**论文差异化机会**：PIT-correct MS-VAR for A-share，将是方法论上对国内学术界 naive 回测的显式改进。

---

### 缺口4：PIT vs Naive IC 对比实验设计

**核心问题**：k-atana 有 PIT 数据基础设施，但**从未量化** naive 回测的 IC 膨胀幅度。没有这个数字，PIT 契约只是设计原则，不是证据。

**文献基础**：

| 论文 | 核心发现 | 适用性 |
|------|---------|--------|
| Daniel et al. (2008) arXiv:0810.1922 | 基准成员前视使 Sharpe 上浮**高达 8%/年** | 美国大蓝筹，A 股类似机制 |
| Glasserman & Lin (2023) arXiv:2309.17322 | GPT 情绪分析前视偏差量化 | LLM 因子相关 |
| Benhenda (2026) arXiv:2601.13770 | Look-Ahead-Bench，PIT-LLM vs 标准 LLM | 2026 前沿基准 |
| Blanchet et al. (2022) arXiv:2202.00871 | PIT 填补的最优偏差-方差权衡 | 理论框架 |

**实验设计（3个子实验）**：

```
Experiment A — HS300 成员偏差（momentum_resid_vol）
  Naive arm:  用期末成员计算 IC
  PIT arm:    用期初成员计算 IC
  预期 IC 膨胀: 0.02-0.05

Experiment B — 复权因子偏差（所有因子）
  Naive arm:  最新 parquet 中的 unadjusted close
  PIT arm:    available_at 约束下的 adjustment_factor 复权
  预期 IC 膨胀: 取决于调整质量

Experiment C — 财务数据偏差（quality_roe / value_pb，待做）
  Naive arm:  用最新报告的 EPS，忽略披露滞后
  PIT arm:    只用 available_at 之后的 EPS
  预期 IC 膨胀: 0.05-0.15（A 股 4 个月报告滞后期）
```

**关键缺失数据**：沪深 300 历史成分股（含 effective_date 和 removal_date）——这是 Experiment A 的唯一前提条件。来源：Wind/Bloomberg/Windquant。

**k-atana 独特优势**：manifest 系统（`manifest.py` + `snapshot.py` + `ExperimentManifest`）提供不可篡改的执行记录——哪个数据快照、哪个 git commit、所有参数。可直接回应对"PIT vs naive 结果不可复现"的批评。

**论文贡献**：首个在 A 股上做受控 PIT vs naive IC 对比实验的系统性研究。

---

### 缺口5：另类数据来源

**k-atana 当前数据栈**：以 OHLCV 价格数据为核心的交换行情数据，加上 TDX 财务快照和宏观指标 stub。**完全缺失**的结构化另类数据类别：

| 数据类别 | 国内头部实践 | k-atana |
|---------|------------|---------|
| 卫星图像/物流 | 幻方/明汯传闻用停车场/港口/工业活动 | ❌ |
| 消费 POS/信用卡 | 银联数据；美团/饿了么订单代理 | ❌ |
| 供应链/物流 | G7 卡车 GPS；煤炭/钢铁/零售流 | ❌ |
| 分析师预期/修订 | Wind IPE、朝阳永续、Bloomberg 一致预期 | ❌ |
| 资金流/融资余额 | 中证登融资余额；空头利率；NEEQ 流 | ❌ |
| 期货持仓报告 (COT) | CZCE/DCE 周度 COT | ❌ |
| 社交情绪/新闻 | 东财、雪球、新浪；FinBERT/ChatGLM | ❌ |
| 宏观指标 | 央行/NBS：FAI/CPI/PPI/PMI/社融 | 部分（67K 行 CPI/PPI/农业价格） |
| 财务报告 (PIT-safe) | 季报/年报；披露日期 | 路线图中（XAR-453） |
| 区块交易/大宗 | 上交所/深交所大宗交易 | ❌ |
| 期权/衍生品 | 持仓量/put-call 比/隐含波动率 | ❌ |

**PIT lake 架构本身是 solid 的**：可扩展到任何新的另类数据——只需在 `pit/` 目录下新增 dataset contract（如 `ashare.margin_balance_pit`）。缺口是**上游**：没有非价格数据的接入桥——无 ChinaData/Tushare 用于 margin/fund-flow，无物流 API，无情绪爬虫。

**加密货币侧同样缺失**：crypto PIT lake 定义了 `crypto.funding_rate_pit`、`crypto.open_interest_pit`、`crypto.mark_price_pit`，但实际行数为 0。OKX-dump (21 installs/day) 和 python-okx (3,074/day) 已审计但未实现。

---

### 综合优先级

```
P0（阻断论文可信度）
  [2] 涨跌停/停牌数据接入 baostock（将 research-only 升级为 constraint-aware）
  [1] XAR-481 dry_run 移除（真实订单可执行）
  [4] HS300 成分股历史获取（Experiment A 前提条件）

P1（提升论文贡献强度）
  [3] MV1 HMM → MS-VAR Path B（解决慢熊检测）
  [2] T+1 仓位追踪实现
  [2] OFI 涨跌停过滤逻辑
  [4] PIT vs Naive Experiment A 代码实现

P2（差异化贡献）
  [3] 多信号 OR gate（MA200 + breadth + margin + vol regime）
  [3] Zhang et al. Coupled HMM 方向（新闻+跨股）
  [5] 融资余额数据接入
  [5] 加密货币 funding/OI 数据实现

P3（架构级）
  [5] 卫星/物流另类数据评估
  [1] 延迟插桩（p50/p95/p99）
  [1] C++ 热路径重写
  [2] IC/IM 期货对冲设计
```

---

### 关键结论

**三条最硬的 gap（阻断论文发表的）**：

1. **涨跌停/停牌数据缺失** → 回测结果是假的，所有因子 IC 都有潜在膨胀
2. **HS300 成分股历史缺失** → PIT vs Naive 对比实验跑不起来，核心贡献无法量化
3. **执行层 150-450ms** → 论文方向"paper trading"无法迁移到真实执行，limiting case 是手动交易

**三条差异化贡献（论文价值所在）**：

1. **PIT-correct MS-VAR for A-share** — 方法论对国内学术界的 naive 回测显式改进，附 HS300 成员偏差 IC 膨胀实测数据
2. **Multivariate HMM + 涨跌停密度的 A 股 regime 检测** — 学术上引用 Zhang et al.，实践上比国内头部 OR gate 更严格
3. **OFI 涨跌停过滤 + T+1 仓位追踪** — 国内首个考虑 A 股微观结构约束的微观-宏观双层信号框架

---

> "AI trained on corporate disclosures surpasses most human analysts in return prediction.
> Humans outperform AI when intangible assets or financial distress are involved."
> — Cao et al. 2024, Journal of Financial Economics
