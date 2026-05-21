# k-atana Research Summary — GPU HMM Regime Detection

> 整合自 XAR-466 Phase 2 / XAR-467 Phase 1 / XAR-465 的实验记录
> 最后更新：2026-05-21

---

## 1. 问题背景

全A股 regime detection 的核心挑战：

```
输入：B = 7504 股票 × T = 1889 交易日（2018-2026）× D = 20 特征
目标：对每只股票每日推断其处于哪种 market regime
约束：
  1. PIT-correct：只能用 available_at 之前的数据
  2. 全量股票 GPU batched 一次跑完
  3. 每日滚动 refit（不能等 batch 结束再重训）
```

---

## 2. GPU 加速 benchmark（XAR-466 Phase 2）

### 配置

| 参数 | 值 |
|------|-----|
| B × T | 7504 × 1889 |
| D | 20（ret, vol, mom, dd, range, amihud, turn_z 等）|
| K（隐状态数）| 8 |
| HMM 类型 | pomegranate DenseHMM, diagonal covariance |
| min_train | 504 天 |
| refit_every | 21 天 |
| max_iter | 10 |
| GPU | RTX 5090（cu132, sm_120 Blackwell）|
| 数据密度 | 85.8% 真实 / 14.2% 前向填充 |

### 结果

| Variant | 耗时 | GPU 峰值显存 | 相对 v1 |
|---------|------|------------|---------|
| v1（expanding window, cold init）| 245.8s = 4m06s | 9.66 GB | 1.00x |
| v2（T_max=1000, warm-start）| 185.3s = 3m05s | 5.19 GB | 1.33x |
| CPU projection（hmmlearn 逐股串行）| ~3473s = 58 min | — | — |
| **GPU vs CPU 提速** | — | — | **14-19x** |

### 工程决策

**pomegranate 保留，不换 Rust/Triton**。

触发 rewrite 的条件（Curry 规则）：
- B > 5000 时提速趋于平缓，或
- 每日 refit 延迟低于 100ms

目前 3-5 分钟的 full-universe scan 落在"加速"区间，两个条件均未触发。

---

## 3. 状态崩溃问题（XAR-467 Phase 1）

### 症状

v1/v2 的 posterior 全塌到一个状态，8 个隐状态里只有 1 个有效：

```
State 0 占比：100%（其余 7 个状态为空）
原因：feature 量纲差异 5 个数量级
```

| 特征 | std（raw）| max |
|------|-----------|-----|
| ret | 0.06 | ±7.88 |
| mom_120d | 0.27 | — |
| range_20d | 1.22 | 715 |
| kurt_20d | 1.53 | 13.29 |
| amihud_20d | **171** | **115,129** |
| vol_accel | **93** | **84,294** |

`amihud = |ret| / amount` 在低成交量股票上爆炸。`vol_accel = vol_5d / vol_20d` 在 vol_20d 接近 0 时爆炸。

diagonal Gaussian HMM 的 shared cross-section params 被这两个特征主导，EM 收敛到单一"近零"模式。

### 修复方案（已验证）

**Layer 1：log1p 变换（panel builder 级别，一次性）**

```python
LOG1P_COLS = (10, 12, 18)   # range_20d, amihud_20d, vol_accel
for c in LOG1P_COLS:
    panel[:, :, c] = np.log1p(np.clip(panel[:, :, c], 0.0, None))
```

PIT-safe（每格独立映射，无时间泄漏）。变换后：

| 特征 | std before | std after |
|------|-----------|----------|
| range_20d | 1.22 | 0.07 |
| amihud_20d | 171 | 0.05 |
| vol_accel | 93 | 0.29 |

**Layer 2：expanding-window z-score per refit**

每次 refit 前用训练集统计量标准化全部面板数据：

```python
def _normalize_features(panel, fit_end_idx):
    train = panel[:, :fit_end_idx, :].reshape(-1, D)
    mu = train.mean(axis=0)
    sigma = np.where(train.std(axis=0) < 1e-3, 1e-3, train.std(axis=0))
    z = (panel - mu) / sigma
    z = np.clip(z, -10, 10).astype(np.float32)
    z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
    z += jitter_rng.normal(0, 1e-3, size=z.shape)
    return z
```

关键参数：
- `sigma floor = 1e-3`（不是 1e-8）：防止 clip 后产生零方差分区导致 pomegranate 报 "Variances must be positive"
- Gaussian jitter `sigma=1e-3`：打破 EM 的退化平衡

**Layer 3：cold-init each refit（不是 warm-start）**

warm-start 导致灾难性崩溃：
- warm-start：refit 0 = 8/8 活跃，refit 1+ = 1/8 崩溃
- cold-init：平均 6.4/8 活跃，6 次偶发崩溃

每个 refit 独立随机初始化，强制 EM 重新搜索多样性——更慢但质量保持。

### 结果（v3 vs v1/v2）

| Variant | 耗时 | 平均活跃状态数 | State 5 占比 |
|---------|------|--------------|-------------|
| v1（no norm, expand）| 4m06s | 1.0/8 | 100% |
| v2（no norm, T_max=1000, warm）| 3m05s | 1.0/8 | 100% |
| **v3（norm, T_max=1000, cold）**| **6m02s** | **6.4/8** | spread |

GPU 峰值显存：5.2 GB（全 variant 一致）。

### 剩余 gap（XAR-467 Phase 2 范围）

1. **~9% cold-init 崩溃率**：6/66 refits 进入退化局部最优。解法：seed-retry guard（`if n_active < 4: re-init with different seed`）。
2. **增量标准化**：z-score 当前每 refit 重算全部 B×T×D，均值/方差流式更新可从 2.5s → ~1s，节省 100s 全量扫描时间。
3. **模块接线**：`_normalize_features` 接入 `app/research/methodology/regime.py`，添加测试。
4. **T_max 消融**：1000 是拍脑袋选的，未经验证。应 sweep T_max = 500/750/1000/1500 找到 IC 稳定区间。

---

## 4. 慢熊检测失败（XAR-465）

### 症状

单变量 Gaussian HMM 在 A 股上命中的是 **volatility regime**，不是 **trend regime**：

| 事件 | 实际 | 检测 |
|------|------|------|
| 2015 Q3 股灾 | bear（35%）| ✅ 强命中 |
| 2020 covid | bear（53%）| ✅ 强命中 |
| 2018 慢熊 | bear（2%）| ❌ 漏掉 |
| 2021-22 慢熊 | bear（8%）| ❌ 漏掉 |

慢熊的特征：日均收益 ~-0.17%，低波动率。与 neutral/quiet-bull 无法区分。

### 修复路径（已规划）

**Path A：Multivariate HMM**
添加特征 `[log_return, vol_20d, momentum_60d]`，让 HMM 学 joint distribution 而非边缘分布。`hmmlearn GaussianHMM` 支持多维 emission，covariance_type 对角/全协方差需敏感性分析。

**Path B：Markov-switching AR（statsmodels）**
原生 trend-vs-vol 分离，但 statsmodels 无 partial fit，每次 refit 重训。

**Path C：Ensemble**
HMM 抓 crash，60d MA + breadth 抓 trend，两者 OR 触发慢熊信号。

---

## 5. PIT-Correct 基础设施（支撑层）

Regime detection 的数据底座同样经过严格设计：

### A 股 PIT 数据管道

```
TdxClient → QMT export → parquet lake
  → coverage manifest（可交易性标记）
  → xdxr（复权因子）PIT producer
  → PIT manifest 验证
  → regime module
```

关键约束：
- `event_time` vs `available_at` 必须严格分离
- 前向填充的股票（前 504 天）不能参与训练
- 停牌/ST/退市 状态不可参与 regime 推断

见：`docs/ASHARE_DATA_PIT_SPEC.md`

### Crypto PIT 数据管道

```
OKX REST API → ccxt adapter
  → funding rate history（当前 33 天，需扩展到 24 个月）
  → open interest history
  → mark price history
  → PIT manifest
```

XAR-423/426 进行中（ccxt 替换 + PIT 回填）。

---

## 6. 研究空白与潜在论文方向

### 空白 1：Multivariate HMM for A-share Regime Detection

**现状**：单变量 HMM 在 A 股慢熊上 fail，XAR-465 已识别但未解决。

**潜在贡献**：
- 首个对 A 股全量股票（>7000）做 multivariate HMM regime detection 的系统性研究
- 特征选择（ret + vol + momentum 之外还有什么特征组合最有效）
- 对比 A 股 vs 美股的 regime 结构差异（A 股有涨跌停板限制、散户主导等独特因素）

**需要的实验**：
- 完成 XAR-467 Phase 2（全状态激活的 benchmark）
- Multivariate HMM sweep（D=3/5/8/15 特征组合）
- K=3/4/6/8 状态数 sweep
- IC 验证：每个 regime 配置在 held-out period 上的预测能力

### 空白 2：PIT-Correct Factor Research Infrastructure

**现状**：学术界大多数因子研究在 clean panel 上跑，不考虑 `available_at` 约束。

**潜在贡献**：
- 一个完整的 PIT-correct 回测框架，确保每个因子的"数据泄漏"被可证明地消除
- 对比 PIT-correct vs naive（使用未来数据）回测的因子 IC 差异
- 这是对量化研究方法论的正统贡献，不依赖特定市场

**需要的证据**：
- 在 k-atana 的 18 组因子族（G1-G6）上跑 PIT vs naive 对比实验
- 量化"有多少因子在 PIT-correct 后失效"

### 空白 3：GPU Batched HMM for Panel Data

**现状**：GPU batched HMM 在量化领域已有应用（pomegranate GPU、jax-md），但针对 A 股全量股票的 scaling study 缺失。

**潜在贡献**：
- B = 7504 × T = 1889 × D = 20 的 scaling law（峰值显存、提速比与 B/T/D 的关系）
- cold-init vs warm-start 在不同面板形状下的质量/速度 trade-off
- feature normalization 策略（log1p + z-score vs 其他标准化方法）的系统性消融

### 空白 4：A-share Specific Market Microstructure

**现状**：XAR-481 live execution 正在推进，真实订单数据将可获取。

**潜在贡献**：
- 基于真实 A 股 order flow 的 market impact 模型（A 股涨停板、T+1 制度对 order fill rate 的影响）
- Volatility regime 和 order submission strategy 的条件关系

**需要的条件**：XAR-481 收口 + 至少 3 个月真实数据积累。

---

## 6b. 国内头部量化竞品调研（2026-05-21）

> 核心结论：**国内头部几乎全部黑箱**——唯一真正可见的是幻方，因为它拆出 DeepSeek AI 研究必须开源。详见 `docs/research-references.md`

### 可见性速查

| 机构 | 可见性 | 可见内容 |
|------|--------|---------|
| 幻方 (DeepSeek) | ████████░░ 80% | GPU 训练栈（3FS/FlashMLA/DeepGEMM，2048 H800，8D 并行）；量化交易策略本身零公开 |
| 明汯 (MHC) | █░░░░░░░░░ 10% | 裘慧明 UPenn 物理 PhD + 谢晗宇前 Citadel Securities 说明背景；策略方法论零公开；2021Q1 亏损 27% 披露 |
| 启林 / 九坤 | ░░░░░░░░░░ 0% | 完全黑箱 |
| 鸣熙 / 诚奇 / 灵均 | ░░░░░░░░░░ 0% | 完全黑箱 |

### 竞品共性（从可见信息推断）

- **人才**：清北复交物理/数学 PhD + 美国对冲基金（Citi/MS）背景
- **因子方法**：多因子量价模型起步，ML/LLM 是当前转型方向
- **执行**：中高频统计套利、做市、多策略为主流
- **护城河推断**：qlib 等开源工具抹平因子研究差距；**执行基础设施**（微秒级延迟）+ **另类数据**是真实护城河

### k-atana 可差异化维度

| 维度 | 国内头部 | k-atana |
|------|---------|---------|
| PIT 数据契约 | 内部有，不公开 | **显式设计原则**，可证明无数据泄漏——研究可信度的制度保障 |
| 全量 A 股 GPU batched HMM | 黑箱 | B=7504 × T=1889，6.4/8 状态激活，14-19x CPU 提速 |
| 慢熊检测 | 黑箱 | Multivariate HMM（XAR-465，待做）针对 2018/2021-22 补漏 |
| 多 agent 架构 | 黑箱 | 显式建模 + broadcast/negotiation 对比实验 |
| 因子研究 PIT vs naive 对比 | 空白 | 首个系统性对比 PIT-correct vs naive 回测 IC 差异的 A 股研究 |
| Rust 原生信号管线 | 黑箱 | tdxcli-rs + 架构边界强制（research/trading 分离） |

---

## 7. 五条深度缺口（2026-05-21）

> 竞品调研 + 执行基础设施 + A股微观结构 + Regime检测 + PIT vs Naive 实验

### 缺口1：执行基础设施 — 150-450ms，差行业30-100倍

**核心发现**：无实测延迟数据（零 `time_ns()` 插桩）。估算：QMT 路径 ~150-450ms，OKX 路径 ~155-405ms。XAR-481 dry_run 在5处未移除，真实订单从未发出。

| 层级 | 延迟 | 技术 | k-atana |
|------|------|------|---------|
| 机构零售 (mini-HFT) | 5-50ms | C++/FPGA/co-lo | ❌ 未达到 |
| 对冲基金 (Prop) | 0.1-5ms | C++/kernel bypass | ❌ |
| HFT (Jane Street) | <100μs | FPGA/微波 | ❌ |

**补齐路径**：直接 DMA（替换 EasyXT）+ 共置（深交所期货大厦）+ C++ 热路径 + 连接池 + 延迟插桩。

### 缺口2：A股微观结构约束 — 12项缺口，回测本质是假的

**核心发现**：`tradability_status_pit.parquet` 不在 lake 中；ASHARE_DATA_PIT_SPEC.md 明确要求建模的约束全部未实现。

| 缺口 | 严重性 |
|------|--------|
| **T+1 卖出约束未建模** | 高 |
| **涨跌停执行约束未建模** | 高 — `tradability_status_pit` 不存在 |
| **停牌/ST 状态未建模** | 高 |
| 融资暴增反转数据不足（172行）| 高 |
| SOE confounder 未控制 | 中 |
| 涨跌停密度未纳入 HMM | 中 |
| **OFI 涨跌停日处理缺失** | 高 |
| 指数期货对冲未设计 | 中 |
| 因子半衰期未分析 | 中 |
| 社融数据 available_at 时滞 | 高 — 社融 T+14 发布 vs 隐含 T 日使用 |

**立即可做**：回测标签加"research-only"；HMM 加入涨跌停密度特征；OFI 实现加涨跌停过滤。

### 缺口3：Regime 检测 — 慢熊 fail，多信号 OR gate 未实现

**核心发现**：单变量 HMM 在 2018/2021-22 慢熊上 fail；MV1 改善但 2018 仍无解。国内头部用多信号 OR gate（MA200 + breadth + margin + macro），k-atana 未实现。

| 方法 | 国内头部 | k-atana |
|------|---------|---------|
| 多信号 OR 门 | ✅ 主流 | ❌ 未实现 |
| 融资余额变化率 | ✅ A股特色 | ❌ |
| 政策 regime overlay | ✅ A股特色 | ❌ |
| XGBoost/LightGBM 分类器 | ✅ 头部在用 | ❌ |
| Zhang et al. Coupled HMM（新闻+跨股）| 学术方向 | ❌ |
| MS-VAR (Hamilton) | 学术标准 | 计划 Path B |

**学术引用**：Zhang et al. (2018) arXiv:1809.00306；Liu et al. (2021) arXiv:2104.09700；Hamilton (1989)。

### 缺口4：PIT vs Naive IC 对比 — 核心贡献无量化证据

**核心发现**：PIT 契约是设计原则，不是证据。没有跑过 PIT vs naive 对比实验，不知道现有因子的 IC 膨胀了多少。

**实验设计（3个子实验）**：

```
Experiment A — HS300 成分股成员偏差
  Naive: 期末成员 → PIT: 期初成员
  预期 IC 膨胀: 0.02-0.05

Experiment B — 复权因子偏差
  预期 IC 膨胀: 取决于调整质量

Experiment C — 财务数据偏差（fundamentals PIT-safe 后）
  预期 IC 膨胀: 0.05-0.15（A 股 4 个月报告滞后期）
```

**关键缺失**：沪深 300 历史成分股（含 effective_date 和 removal_date）——来自 Wind/Bloomberg。k-atana 独特优势：manifest 系统提供不可篡改的执行记录。

**文献依据**：Daniel et al. (2008) — 美国基准偏差使 Sharpe 上浮 8%/年。

### 缺口5：另类数据 — 零接入路径，价格数据独大

**核心发现**：k-atana 是以 OHLCV 为核心的价格数据终端，国内头部接入 2-3 种另类数据，k-atana 完全缺失。

| 数据类别 | 国内头部 | k-atana |
|---------|---------|---------|
| 卫星/物流（G7/停车场）| 幻方/明汯传闻 | ❌ |
| 消费 POS（银联/美团）| 大型机构 | ❌ |
| 供应链/物流 | 传闻在用 | ❌ |
| 分析师预期修订 | Wind/朝阳永续 | ❌ |
| 融资余额 | 中证登 | ❌ |
| 期货 COT | 九坤传闻 | ❌ |
| 社交情绪/新闻 | FinBERT 在研 | ❌ |
| 加密 funding/OI 历史 | — | ❌ 仅定义，未实现 |

**PIT lake 架构本身 solid**：可扩展，新增数据集只需在 `pit/` 下新建 contract。缺口是上游——无接入桥。

---

## 8. 修订后的优先级建议

```
P0（阻断论文可信度 — 3项硬缺口）
  [2] 涨跌停/停牌数据接入 baostock（research-only → constraint-aware）
  [1] XAR-481 dry_run 移除（真实订单可执行）
  [4] HS300 成分股历史获取（Experiment A 前提）

P1（提升论文贡献强度）
  [3] MV1 → MS-VAR Path B（解决慢熊检测）
  [2] T+1 仓位追踪实现
  [2] OFI 涨跌停过滤逻辑
  [4] PIT vs Naive Experiment A 代码实现

P2（差异化贡献）
  [3] 多信号 OR gate（MA200 + breadth + margin + vol regime）
  [3] Zhang et al. Coupled HMM 方向
  [5] 融资余额数据接入
  [5] 加密 funding/OI 数据实现

P3（架构级）
  [1] 延迟插桩（p50/p95/p99）+ C++ 热路径
  [2] IC/IM 期货对冲设计
  [5] 卫星/物流另类数据评估
```

**三条最硬阻断**（无论文可信度）：
1. 涨跌停/停牌数据缺失 → 回测结果为假
2. HS300 成分股历史缺失 → PIT vs Naive 实验跑不起来
3. 执行层 150-450ms → 论文方向只能 paper trade

**三条差异化贡献**（论文价值所在）：
1. PIT-correct MS-VAR for A-share（方法论改进）
2. Multivariate HMM + 涨跌停密度的 A 股 regime 检测
3. OFI + T+1 微观-宏观双层信号框架

---

## 8. 参考文献（初步，待补）

- Bilias, Y., et al. "Sequential testing regimes." Econometrics Journal
- Hamilton, J. "A new approach to the economic analysis of nonstationary time series." Econometrica 1989
- POMONA (NeurIPS 2024) — state-of-the-art HMM for panel data
- Amihud, Y. "Illiquidity and stock returns." Journal of Financial Markets 2002
- qlib Alpha158/360 — A-share factor benchmark
- k-atana EXTERNAL_WHEEL_AUDIT.md — 2026-05-18 live data survey
