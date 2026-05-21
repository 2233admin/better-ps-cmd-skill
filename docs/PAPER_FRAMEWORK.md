# 论文框架：信贷脉冲与非传统因子在A股宏观择时中的实证研究

## 暂定标题

**中文**: 信贷脉冲作为A股择时的主导因子：来自13个非传统因子与LLM因子挖掘的实证对比

**English**: Credit Impulse as the Dominant Timing Factor in China's A-Share Market: Empirical Evidence from 13 Alternative Factors and LLM-Driven Factor Mining

## 摘要 (Abstract)

本文构建了一个包含13个非传统宏观因子的"因子动物园"(Alpha Zoo)，系统性地回测了各因子对沪深300的择时能力。实证发现：

1. 信贷脉冲(社融12月滚动变化率)是**唯一统计显著**的择时因子(IC=+0.065, p=0.003)，年化超额+5.0%，Sharpe 0.56
2. 铜金比、螺纹钢-铜背离、BDI动量、PMI修正荣枯线等"物质层"因子均无显著择时能力
3. 信贷脉冲与BDI加速度几乎零相关(ρ=0.026)，具备组合潜力
4. 进一步引入LLM进化式因子挖掘(QuantaAlpha框架)和订单流微观结构因子(OFI)，探索宏观-微观多尺度择时的可能性

关键词: 信贷脉冲、宏观择时、非传统因子、LLM因子挖掘、订单流不平衡、A股

---

## 1. 引言 (Introduction)

### 1.1 研究动机
- A股市场的散户主导特征使得宏观因子择时比个股选股更具实践价值
- 传统技术指标(MA/RSI/MACD)在A股的过拟合问题
- 唯物主义分析框架：经济基础(信贷周期)决定上层建筑(市场走势)

### 1.2 研究贡献
1. 首次系统性对比13个非传统宏观因子在A股的择时能力
2. 提出"叙事-物质背离"框架并实证检验
3. 将LLM因子挖掘与传统宏观择时结合
4. 引入订单流微观结构因子作为宏观信号的执行层优化

### 1.3 文献综述
- **信贷脉冲**: Biggs (2009)首提credit impulse概念; 国内：社融脉冲与股市关系
- **另类数据因子**: 铜金比(Gundlach), BDI(航运周期), 挖掘机指数(三一重工)
- **LLM因子挖掘**: Alpha-GPT (2023), RD-Agent (Microsoft, 2024), QuantaAlpha (清华, 2026), Chain-of-Alpha (2025), AlphaAgent (2025)
- **订单流**: Cont et al. OFI线性关系; Deep OFI (2023); Order Book Filtration (2025)
- **Regime切换**: Hamilton (1989)马尔科夫切换; 2026 Regime-Adaptive混合AI

---

## 2. 数据与方法 (Data & Methodology)

### 2.1 数据源

| 数据 | 来源 | 频率 | 时间跨度 | 行数 |
|------|------|------|---------|------|
| 社融增量 | 央行/akshare | 月 | 2015-2025 | 132 |
| M0/M1/M2 | 央行/akshare | 月 | 2008-2026 | 1,302 |
| PMI(制造业/非制造业) | 统计局/akshare | 月 | 2008-2026 | 654 |
| CPI | 统计局/akshare | 月 | ~30年 | 357 |
| GDP | 统计局/akshare | 季 | ~20年 | 80 |
| 房地产投资 | 统计局/akshare | 月 | 1998-2025 | 1,608 |
| LPR利率 | 央行/akshare | 日 | 2013-2026 | 3,179 |
| 中美利差(12列) | Wind/akshare | 日 | 2010-2026 | 40,388 |
| Shibor隔夜 | 央行/akshare | 日 | 2015-2026 | 2,235 |
| 沪铜主力 | 上期所/akshare | 日 | 2005-2026 | 5,148 |
| 螺纹钢主力 | 上期所/akshare | 日 | 2009-2026 | 4,112 |
| BDI波罗的海 | 波交所/akshare | 日 | 1991-2026 | 8,650 |
| 黄金Au99.99 | 上金所/akshare | 日 | 2016-2026 | 2,233 |
| 主要指数(上证/300/500/创业板) | 交易所/akshare | 日 | 1990-2026 | 46,842 |
| 工程机械指数 | CME/akshare | 月 | 2011-2026 | 3,885 |
| 猪价指数 | 搜猪网/akshare | 周 | 2015-2026 | 557 |
| 融资余额(深市) | 深交所/akshare | 日 | 2025-2026 | 172 |
| **合计** | | | | **~121,000** |

### 2.2 因子构建

#### 2.2.1 信贷脉冲 (Credit Impulse)
```
CI_t = (ΣS_{t-11:t} - ΣS_{t-23:t-12}) / |ΣS_{t-23:t-12}|
```
其中 S_t 为月度社融增量。CI > 0 表示信贷加速扩张。

#### 2.2.2 铜金比 (Copper-Gold Ratio)
```
CGR_t = P_copper_t / P_gold_t
CGR_mom_t = (CGR_t - CGR_{t-60}) / |CGR_{t-60}|
```

#### 2.2.3 BDI加速度 (二阶导)
```
BDI_acc_t = mom(mom(BDI, 20), 20)
```

#### 2.2.4 叙事-物质背离度
```
DIV_t = Z_material_t - Z_narrative_t
```
物质层 = mean(Z(铜_mom60), Z(钢_mom60), Z(BDI_mom60))
叙事层 = mean(Z(PMI-50), Z(-LPR_chg))

#### 2.2.5 融资暴增反转
```
MARGIN_REV_t = -Z_30d(ΔMargin_t / Margin_{t-1})
```

*（其余因子构建见附录A）*

### 2.3 回测方法

- **标的**: 沪深300指数
- **信号**: 因子值 > 0 → 满仓, ≤ 0 → 空仓 (无杠杆, 无做空)
- **执行**: T日收盘计算信号, T+1日开盘执行 (防前视偏差)
- **评价指标**: 年化收益、Sharpe比率、最大回撤、Calmar比率、IC及其p值、胜率、持仓占比
- **稳健性**: 滚动窗口参数敏感性、分样本检验

---

## 3. 实证结果 (Empirical Results)

### 3.1 Alpha Zoo: 13因子大乱斗

**[表1: 因子择时能力综合对比]** ← 已有数据

| Rank | 因子 | Sharpe | IC | p值 | 超额年化 | Tier |
|------|------|--------|-----|-----|---------|------|
| 1 | 信贷脉冲 | 0.56 | +0.065 | 0.003** | +5.0% | T1 |
| 2 | BDI加速度 | 0.38 | +0.024 | 0.089 | +0.1% | T2 |
| 3 | 多因子复合 | 0.34 | -0.007 | 0.761 | +1.6% | T2 |
| 4-13 | (其余10个) | <0.2 | — | >0.1 | ≤0 | T3 |

**[表2: 因子间相关矩阵]** ← 已有数据
- 信贷脉冲 vs BDI加速度: ρ=0.026 (正交)
- BDI加速度 vs 多因子复合: ρ=0.777 (冗余)

**[表3: 信号组合分布与收益]** ← 已有数据
- 脉冲+/物质-: 41%天数, 日均+0.062%, 年化+15.5% (最佳)
- 脉冲+/物质+: 27%天数, 日均+0.027%, 年化+6.8%
→ 物质确认是滞后信号, 反而削弱收益

### 3.2 信贷脉冲领先性分析

**[表4: Spearman相关 — 信贷脉冲 vs 沪深300未来收益]** ← 已有数据

| 窗口 | 相关系数 | p值 |
|------|---------|------|
| 当月 | +0.237 | 0.014 |
| 未来1月 | +0.271 | 0.005 |
| **未来3月** | **+0.423** | **<0.001** |
| 未来6月 | +0.375 | <0.001 |

→ 3个月领先性最强, 符合"社融→投资→利润→股价"传导链

### 3.3 策略对比

**[表5: 策略回测对比]** ← 已有数据

| 策略 | 年化 | Sharpe | MDD | Calmar |
|------|------|--------|-----|--------|
| 买入持有 | +3.2% | 0.26 | 44.0% | 0.07 |
| 纯信贷脉冲 | +7.5% | 0.60 | 20.9% | 0.36 |
| 脉冲+物质确认 | +3.9% | 0.41 | 20.5% | 0.19 |
| 脉冲+物质+仓位 | +4.3% | 0.46 | 17.2% | 0.25 |

### 3.4 年度分解与熊市防御

**[表6: 年度收益分解]** ← 已有数据

关键发现: 2018年超额+9.7%, 2022年超额+17.3%
→ 信贷脉冲的核心价值在于**熊市防御**, 而非牛市增强

---

## 4. 扩展实验 (待做)

### 4.1 LLM进化式因子挖掘 (QuantaAlpha对比)

**目标**: 用LLM自动从121k行历史数据中挖掘因子，与人工设计的13因子对比

**实验设计**:
- 框架: QuantaAlpha / AlphaAgent
- 种子因子: 信贷脉冲公式
- 搜索空间: macro_history全部因子的算术/逻辑组合
- 评价: IC、ICIR、年化超额、因子衰减速度
- 对比: LLM挖掘因子 vs 人工设计因子 vs 随机因子

**需要做的**:
- [ ] Clone QuantaAlpha, 适配我们的DuckDB数据源
- [ ] 用GPT-4/Claude作为LLM后端运行因子进化
- [ ] 收集top-10 LLM因子, 与信贷脉冲做IC/Sharpe对比
- [ ] 分析LLM是否能"重新发现"信贷脉冲

### 4.2 订单流微观结构因子 (OFI)

**目标**: 在信贷脉冲给出宏观方向后，用订单流因子优化进出场时机

**实验设计**:
- 数据: 通达信L2 tick数据 (已有kline_minute 173k行)
- 因子: Order Flow Imbalance (OFI), Order Book Imbalance (OBI), VPIN
- 框架: hftbacktest (Rust, 支持L2/L3)
- 策略: 宏观信号(月度) × 微观信号(分钟级) 双层过滤

**需要做的**:
- [ ] 从通达信导出L2 tick数据到DuckDB
- [ ] 实现OFI/OBI/VPIN计算
- [ ] 回测: 纯OFI vs 信贷脉冲过滤后的OFI
- [ ] 分析宏观-微观信号的交互效应

### 4.3 强化学习动态权重 (PPO Alpha Weighting)

**目标**: 用PPO自动学习多因子动态权重，替代等权/固定权重

**实验设计**:
- 状态空间: [信贷脉冲, BDI加速度, 铜金比, Shibor, PMI, ...]
- 动作空间: 连续仓位 [0, 1]
- 奖励: Sharpe比率 / Calmar比率
- 框架: FinRL
- 对比: PPO动态 vs 等权 vs 纯信贷脉冲

**需要做的**:
- [ ] 用FinRL搭建A股环境 (接DuckDB数据)
- [ ] 训练PPO agent
- [ ] 样本外测试 (2024-2026)
- [ ] 分析agent学到的权重模式 (是否自动发现信贷脉冲最重要)

### 4.4 Regime切换模型

**目标**: 用隐马尔科夫模型(HMM)识别市场regime，与信贷脉冲信号对比

**实验设计**:
- 模型: 2-state HMM (牛/熊) 和 3-state HMM (牛/震荡/熊)
- 特征: 收益率、波动率、成交量
- 对比: HMM regime vs 信贷脉冲信号的一致性
- 扩展: 信贷脉冲是否是HMM regime转换的领先指标

**需要做的**:
- [ ] 用hmmlearn拟合沪深300
- [ ] 对比regime标签和信贷脉冲信号
- [ ] 测试"信贷脉冲领先HMM regime切换"假说

### 4.6 GPU Batched HMM — 全A股 Regime Detection（k-atana XAR-466/467）

> 基于 2026-05-21 实验数据。详见 `docs/RESEARCH_SUMMARY.md`

**已知问题**：单变量 Gaussian HMM 在 A 股学到的是 volatility regime，不是 trend regime：

| 事件 | 实际 | 检测 |
|------|------|------|
| 2015 Q3 股灾 | bear（35%）| ✅ |
| 2020 covid | bear（53%）| ✅ |
| **2018 慢熊** | bear（2%）| ❌ 漏掉 |
| **2021-22 慢熊** | bear（8%）| ❌ 漏掉 |

慢熊特征：日均收益 ~-0.17%，低波动率。与 neutral/quiet-bull 无法区分。

**GPU benchmark（XAR-466 Phase 2）**：

| Variant | 耗时 | GPU 峰值显存 | vs CPU |
|---------|------|------------|-------|
| v1（expanding, cold）| 245.8s | 9.66 GB | 14-19x 提速 |
| v2（T_max=1000, warm）| 185.3s | 5.19 GB | — |
| CPU（逐股串行）| ~3473s | — | baseline |

pomegranate 保留，Rust/Triton 不触发。

**Feature-scale 崩溃问题（XAR-467 Phase 1，已修复）**：

v1/v2 posterior 全塌到一个状态，原因是 amihud_20d（std=171）和 vol_accel（std=93）的量纲差异。修复：log1p 变换 + expanding-window z-score + cold-init each refit。

v3 结果：平均 6.4/8 状态激活，无崩溃。

**下一步**：XAR-467 Phase 2（seed-retry guard、增量标准化、T_max 消融）、XAR-465（Multivariate HMM，加入 vol+momentum 特征）

### 4.7 Multi-Agent 交易公司架构

> 参考：SSRN-6746480 "Algorithmic Trading, AI, and Securities Markets"

**核心发现**：多 agent 交易公司模拟人类对冲基金分工：

```
fundamental_analyst  → 基本面分析（PIT-correct 因子）
technical_analyst    → 技术分析（HMM regime）
sentiment_analyst    → 情绪分析（OKX market data）
risk_manager         → 风险管理（HMM regime 输出）
portfolio_manager    → 组合决策（待做：PyPortfolioOpt）
trader              → 执行（order state machine）
```

**Agent 间通信三种范式**：
1. **Broadcast** — 快但噪音多（适合 trading 场景）
2. **Direct messaging** — 准确但慢（适合 research→PM）
3. **Negotiation** — 质量最高但最慢（适合 risk vs PM 冲突）

**反直觉发现**：agent 并非越多越好。7 个不同 objective function 的 agent 反而比简单 setup 差，因为冲突的 objective 导致 consensus 失败。

k-atana 当前是隐式 agent 分工。论文贡献：显式建模 + 实验对比不同架构（broadcast vs negotiation、不同 agent 数量、不同 objective 表达方式）。

**需要做的**：
- [ ] 将 pipeline 各模块显式建模为 agent
- [ ] 设计 agent 间通信协议（broadcast vs negotiation）
- [ ] 对比实验：简单 3-agent vs 复杂 7-agent 在 A 股的表现
- [ ] 将信贷脉冲假说接入 multi-agent 框架

### 4.5 Qlib端到端对比

**目标**: 用微软Qlib的标准化框架重跑所有实验，增加可复现性

**需要做的**:
- [ ] 将macro_history数据转为Qlib格式
- [ ] 用Qlib内置模型(LightGBM/LSTM/Transformer)做因子组合
- [ ] 与我们手工策略对比
- [ ] 用RD-Agent自动搜索因子+模型组合

---

## 5. 讨论 (Discussion)

### 5.1 为什么信贷脉冲有效而物质信号无效?

唯物主义解释：在中国市场, 信贷是**最上游的因果变量**。社融放量→企业投资→铜/钢需求→价格上涨→利润改善→股价上涨。用下游变量(铜价、螺纹钢)预测股价, 本质上是用"结果"预测"结果", 信息已被price in。

### 5.2 信贷脉冲的局限性

1. 2024年政策驱动的突击反弹未被捕获 (超额-7.4%)
2. 社融数据发布滞后约2周
3. "宽信用"不一定导致"宽股市" (如2022年)

### 5.3 与国际市场的可迁移性

QuantaAlpha论文显示CSI300因子可迁移到S&P500, 信贷脉冲在全球范围(美联储/ECB信贷数据)的适用性值得探索。

---

## 6. 结论 (Conclusion)

[待实验全部完成后撰写]

---

## 附录

### 附录A: 全部13因子构建公式
### 附录B: 参数敏感性分析
### 附录C: 分样本稳健性检验 (2008-2015 vs 2016-2022 vs 2023-2026)
### 附录D: LLM因子挖掘详细结果
### 附录E: 订单流因子计算细节

---

## 参考文献 (部分)

1. Biggs, M. (2009). "Credit Impulse - The New Black." Deutsche Bank Research.
2. Cont, R., Kukanov, A., Stoikov, S. (2014). "The Price Impact of Order Book Events." Journal of Financial Econometrics.
3. QuantaAlpha Team (2026). "QuantaAlpha: An Evolutionary Framework for LLM-Driven Alpha Mining." arXiv:2602.07085.
4. Chain-of-Alpha (2025). "Unleashing the Power of LLMs for Alpha Mining." arXiv:2508.06312.
5. AlphaAgent (2025). "LLM-Driven Alpha Mining with Regularized Exploration." arXiv:2502.16789.
6. Alpha-GPT (2023). "Human-AI Interactive Alpha Mining for Quantitative Investment." arXiv:2308.00016.
7. PPO Alpha Weighting (2025). "Adaptive Alpha Weighting with PPO." arXiv:2509.01393.
8. Order Book Filtration (2025). "Directional Signal Extraction at High Frequency." arXiv:2507.22712.
9. Fed (2025). "Order Flow Imbalances and Amplification of Price Movements."
10. MIGA (ICLR 2025). "Mixture of Experts with Group Aggregation for Stock Prediction."
11. Regime-Adaptive (2026). "Hybrid AI-Driven Trading System." ComSIA/Springer LNNS.
12. FinRL (2025). "Financial Reinforcement Learning."
13. Microsoft Qlib + RD-Agent (2025). "AI-Oriented Quantitative Investment Platform."

---

## 实验优先级与排期

| 优先级 | 实验 | 预计工作量 | 依赖 | 状态 |
|--------|------|-----------|------|------|
| P0 | Alpha Zoo 13因子对比 | ✅ 已完成 | — | ✅ Done |
| P0 | 信贷脉冲领先性分析 | ✅ 已完成 | — | ✅ Done |
| P0 | 组合策略回测 | ✅ 已完成 | — | ✅ Done |
| P0 | 叙事-物质背离检验 | ✅ 已完成 | — | ✅ Done |
| P1 | 参数敏感性 & 稳健性 | 1天 | 已有代码 | TODO |
| P1 | HMM Regime对比 | 1天 | hmmlearn | TODO |
| P2 | QuantaAlpha LLM因子挖掘 | 2-3天 | GPU, API key | TODO |
| P2 | Qlib端到端对比 | 2天 | pip install qlib | TODO |
| P3 | 订单流OFI因子 | 3-5天 | L2 tick数据 | TODO |
| P3 | FinRL PPO动态权重 | 2-3天 | FinRL | TODO |
| P1 | HMM Regime benchmark（GPU batched）| 1天 | hmmlearn, RTX 5090 | ✅ GPU 加速已验证（14-19x），feature collapse 已修 |
| P2 | Multivariate HMM（vol+momentum）| 2-3天 | XAR-467 Phase 2 | TODO — 解决慢熊检测 |
| P2 | Multi-agent 显式架构 | 3-5天 | SSRN-6746480 | TODO — 设计实验 |
