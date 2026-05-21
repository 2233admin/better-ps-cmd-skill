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

> "AI trained on corporate disclosures surpasses most human analysts in return prediction.
> Humans outperform AI when intangible assets or financial distress are involved."
> — Cao et al. 2024, Journal of Financial Economics
