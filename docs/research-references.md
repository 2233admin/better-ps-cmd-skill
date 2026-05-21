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
