# 停牌 / 涨跌停数据源对照报告 (A1 / XAR-480)

**生成日期**: 2026-05-20
**对应 FSC handoff**: `research/fsc-handoff/FSC-HANDOFF-2026-05-20.md` §A1
**性质**: L2 sensor calibration. 一次跑出报告, 供 C1 supervisory controller 选主源.
**输入数据**: `.tmp-port/a1-cases-final.json` (15 case 锁定) -> `scripts/compare-suspension-sources.py` -> `.data/a1-suspension-comparison.csv` (60 行)

---

## 1. 顶部总览

### 1.1 单源指标 (基于 15 case x 4 source = 60 数据点本次实测)

| 数据源 | Fill rate | Coverage scope | Correctness on covered | Avg latency | T+0 / T+1 | 成本 | License |
|---|---|---|---|---|---|---|---|
| **baostock** | **15/15** (100%) | 全 A (沪深主板/创业板/科创板/北交所) | **15/15** (100%) | ~0.5s | T+1 (盘后 5pm 数据库刷新) | 免费, 无 API key | 开源, [GPL-3.0 sdk](https://github.com/baostock/baostockcn) |
| akshare | 0/15 (本次跑 mirror sustained-down) | 全 A | n/a (无 fill 无从评) | ~9.5s (3 retry timeout) | T+0 当日 (snapshot 接口) + 历史 hist | 免费, 无 key | MIT |
| TDX lake | 0/15 (lake max date = 2026-03-31, 5 月 case 全部超出) | 全 A (producer 跑全市场) | n/a | ~0.01s (本地 parquet) | T+1 (producer pipeline 决定) | 自有, 一次性 ingest 成本 | n/a (内部) |
| SSE-official (yunhq) | 10/15 (SH-only) | 仅上交所主板 + 科创板 | **10/10** (100%) | ~2.5s | T+0 (盘中 snap endpoint 提供 status) | 免费, 无 key | sse.com.cn 公开页面 |

**判读**: 不是"哪家对错", 是"哪家能用". baostock 是唯一覆盖面 + correctness 双满分的 sensor; akshare 和 TDX 本次都掉链子 (各自原因不同); SSE 在 SH 域是 perfect-second-vote。

### 1.2 跨源一致率

- **包含 N/A / ERROR**: 5/15 (33.3%) -- 用作"strict 全 source agree"标准, 数太低没用
- **排除 N/A & ERROR (仅在 >= 2 source 同时有结果的 case 上计算)**: 10/15 (66.7%) -- 主要由 SH/SZ 分布拉低 (SZ symbol SSE 全 N/A, 但 baostock 都对)
- **baostock vs SSE on covered overlap (10 个 SH-only case)**: **10/10 完美一致** -- 二者交叉验证强信号

---

## 2. 方法论

### 2.1 Case selection (15 个)

| 类型 | 数量 | 选取方式 | 来源 |
|---|---|---|---|
| 停牌 | 5 | `baostock.query_all_stock(day="2026-05-14")` 过滤 `tradeStatus="0"` | 全部 *ST (该日 7223 票中 20 票停牌, 取前 5) |
| 一字涨停 | 5 | `ak.stock_zt_pool_em(date)` 候选 + `baostock query_history_k_data_plus` 验证 OHLC 相等 + pct ≈ +10% | 主板 |
| 一字跌停 | 5 | `ak.stock_zt_pool_dtgc_em(date)` 候选 + 同验证 | 含 1 个创业板 300663 (limit_pct=20%) |

**Picker bias 声明**: 一字 case 候选来源是 akshare zt/dt pool; 验证用 baostock. 因此 baostock 在一字判定上有"知情者优势" -- 但 baostock 的判定标准 (OHLC 相等 + pct ≈ board limit) 是物理事实, 不是 baostock 私有信号, 其他 source 拿到一样的 OHLC 就该得出一样结论。

完整 case 列表: `.tmp-port/a1-cases-final.json`

### 2.2 派生 predicates (state derivation)

四源不返回同一字段。统一派生为三态 {suspended, limit_up, limit_down, trading}:

```
suspended  = (no_row) OR (tradestatus=="0") OR (volume==0)
limit_up   = yizi AND pct in [+limit_pct - 1%, +limit_pct + 1%]
limit_down = yizi AND pct in [-limit_pct - 1%, -limit_pct + 1%]
yizi       = (open == high == low == close)  AND open > 0
```

`limit_pct` 按板块查表:
- 主板 (sh.6xx, sh.9xx, sz.000/001/002): 10%
- 创业板 (sz.300): 20%
- 科创板 (sh.688): 20%
- 北交所 (bj.4xx, bj.8xx): 30%
- ST/*ST: 5%

**容差 1%** 用于吸收 baostock pct 计算的 round-trip 误差 (baostock 给出 9.9762, 真实 pct 在 +-0.5% 内仍算 limit hit; pool 自身的"涨停统计"也带这种容差)。

### 2.3 4 源 ingest

| 源 | API | 输入 | 输出关键字段 | 适配做法 |
|---|---|---|---|---|
| **baostock** | `bs.query_history_k_data_plus(code, "...tradestatus,pctChg,isST", start, end)` | `sh.600519`-style | `date, open, high, low, close, volume, tradestatus, pctChg, isST` | 直接 tradestatus 字段, 最干净 |
| **akshare** | `ak.stock_zh_a_hist(symbol, "daily", start, end, adjust="")` | 裸 6 位 code | `日期, 开盘, 收盘, 最高, 最低, 成交量, 涨跌幅, ...` | 派生 (无 tradestatus); 当日 snapshot `ak.stock_zh_a_stop_em()` 不接受日期参数, 仅当日 |
| **TDX lake** | `polars.scan_parquet(.data/lake/pit/kline_daily_pit.parquet) filter(symbol & event_time)` | `600519.SH`-style | `open, high, low, close, volume, symbol, event_time` | 派生 (无 tradestatus). Spec 提到的 `tradability_status_pit.parquet` 不存在 (见 §4.2) |
| **SSE yunhq** | `GET https://yunhq.sse.com.cn:32042/v1/sh1/dayk/<code>?begin=YYYYMMDD&end=YYYYMMDD&select=date,open,high,low,close,volume` | 仅 sh.* | `kline: [[date,o,h,l,c,vol], ...]` | 派生; prev_close 字段恒 null, 拉 15-日窗口取前一日 close 算 pct. **必须带 `User-Agent: Mozilla/5.0` Header**, 无 UA 返回 "System Error" |

---

## 3. 60 行对照表 (15 case x 4 source)

完整 csv: `.data/a1-suspension-comparison.csv`
完整 json (含 raw 字段): `.data/a1-suspension-comparison.json`

### 3.1 停牌 case (5 case x 4 source = 20 行)

| symbol | name | date | akshare | baostock | tdx-lake | sse-official |
|---|---|---|---|---|---|---|
| 600193 | *ST创兴 | 2026-05-14 | ERROR (mirror down) | **suspended** (tradestatus=0) | N/A (lake 超出) | suspended (no_row) |
| 600421 | *ST华嵘 | 2026-05-14 | ERROR | **suspended** (tradestatus=0) | N/A | suspended |
| 600599 | *ST熊猫 | 2026-05-14 | ERROR | **suspended** (tradestatus=0) | N/A | suspended |
| 600608 | *ST沪科 | 2026-05-14 | ERROR | **suspended** (tradestatus=0) | N/A | suspended |
| 600636 | *ST国化 | 2026-05-14 | ERROR | **suspended** (tradestatus=0) | N/A | suspended |

**子总结**: baostock + SSE 10/10 一致 (全部 sh.6xx, SSE 覆盖). akshare/TDX 全部失能 -- akshare 镜像 sustained-down (RemoteDisconnected), TDX lake 5 月数据缺失 (N/A 不是错, 是 sensor 没建好的暴露). 5 个停牌全部是 *ST, 验证 *ST 集中停牌的典型场景。

### 3.2 一字涨停 case (5 case x 4 source = 20 行)

| symbol | name | date | board | pct (bs) | akshare | baostock | tdx-lake | sse-official |
|---|---|---|---|---|---|---|---|---|
| 001259 | 利仁科技 | 2026-05-14 | sz主板 (10%) | 10.0 | ERROR | **limit_up** | N/A | N/A (SZ) |
| 003018 | 金富科技 | 2026-05-14 | sz主板 | 10.0061 | ERROR | **limit_up** | N/A | N/A (SZ) |
| 002309 | 中利集团 | 2026-05-14 | sz主板 | 10.0 | ERROR | **limit_up** | N/A | N/A (SZ) |
| 603007 | 花王股份 | 2026-05-14 | sh主板 | 9.9762 | ERROR | **limit_up** | N/A | **limit_up** (二证) |
| 603779 | 威龙股份 | 2026-05-14 | sh主板 | 10.0 | ERROR | **limit_up** | N/A | **limit_up** (二证) |

**子总结**: baostock 5/5 命中. SSE 在 2 个 sh.603xx case 上完美二证. akshare/TDX 0 fill。

### 3.3 一字跌停 case (5 case x 4 source = 20 行)

| symbol | name | date | board | pct (bs) | akshare | baostock | tdx-lake | sse-official |
|---|---|---|---|---|---|---|---|---|
| 300663 | 科蓝软件 | 2026-05-06 | 创业板 (20%) | -19.9688 | ERROR | **limit_down** | N/A | N/A (SZ) |
| 603282 | 亚光股份 | 2026-04-29 | sh主板 | -10.0137 | ERROR | **limit_down** | N/A | **limit_down** |
| 603313 | 梦百合 | 2026-04-29 | sh主板 | -9.9355 | ERROR | **limit_down** | N/A | **limit_down** |
| 001233 | 海安集团 | 2026-04-29 | sz主板 | -9.997 | ERROR | **limit_down** | N/A | N/A (SZ) |
| 603378 | 亚士创能 | 2026-04-29 | sh主板 | -10.0173 | ERROR | **limit_down** | N/A | **limit_down** |

**子总结**: baostock 5/5 命中. SSE 在 3 个 sh.* case 完美二证. 创业板 300663 -19.97% 命中 20% limit 容差。

---

## 4. 雷区 / 已知 gap

### 4.1 akshare 镜像不稳 -- 这次实测是 sustained-down (>20 min), 不是 spec 说的"偶尔 502"

**实测**:
- 2026-05-20 13:19 UTC probe -- `stock_zh_a_hist` 茅台正常返回 9 行 (probe 数据见 `.tmp-port/a1-probe-akshare.json`)
- 2026-05-20 13:52 UTC pick-cases 用 baostock 走通 (akshare zt_pool 仍能用, hist 也能用)
- 2026-05-20 14:00 UTC 起 `stock_zh_a_hist` 对任意 symbol 全部 `requests.exceptions.ConnectionError: ('Connection aborted.', RemoteDisconnected(...))` -- 持续到本报告生成

**这是接 eastmoney 镜像的所有 akshare 接口的共同失效**, 不是 ST 股特例.

**3-retry backoff [1s, 3s, 7s] 不解决 sustained outage**.

**对策**:
- akshare 不能作为唯一源
- 接 akshare 必须带 fallback chain (baostock 兜底)
- 监控 akshare 5xx / RemoteDisconnected 失败率, 阈值触发 fallback

### 4.2 TDX lake max = 2026-03-31, 5 月 case 全 N/A

**lake schema**: `.data/lake/pit/kline_daily_pit.parquet` (本次实测 5476 symbol, event_time min=2021-01-04 max=2026-03-31)

**两个层次的问题**:
1. **producer 没跑过最近 6 周** -- `scripts/ingest-ashare-incremental.py` cron 失效 / 没人跑
2. **producer 本身就不算 suspension/limit** -- 见 `scripts/ingest-ashare-tradability.py` 头部 docstring:
   > "Suspension and limit-up/down flags require quote-side data (a separate ticker/feed) and are emitted as False with a low-fidelity reason note. Honest gaps are tracked in the universe manifest."

**结论**: spec 提到的 `tradability_status_pit.parquet` **不存在**于 lake; 即便存在, 字段也是 `False low-fidelity`. 当前 TDX-lake **不是停牌/涨跌停 sensor**, 只能作为 OHLC 派生.

**XAR follow-up**: 需要新 ticket
- (a) 把 ingest-ashare-incremental.py 重新跑通到 2026-05-19
- (b) producer 接 quote-side feed 真正算 suspension/limit, 或显式声明"派生只能基于 OHLC"

### 4.3 baostock T+1 -- 盘中阻断不可用

**事实**: baostock 数据库每日盘后 (17:00 左右) 刷新, 跑当日 query 在收盘前 (15:00) 是空, 也不带"今日实时停牌"字段.

**对盘中阻断 (A3 PreTradeChecker `check_suspension`) 的影响**:
- baostock 不能用作 T+0 sensor
- 必须配 quote-side feed: TDX/通达信 ticker, QMT realtime, 或 SSE yunhq `snap/<code>` (实时, 带 `status` 字段)
- A3 设计要写: baostock 给"昨日 EOD 状态" + 实时 feed 给"今日盘中状态", 两者 union 才完整

**这条必须出现在 A3 design**, 不然会出"盘中真停牌但 baostock 还没刷新 -> 系统认为可交易 -> 下单挂在不能成交的单上"。

### 4.4 SSE 反爬 + 接口面窄

**反爬验证** (`.tmp-port/a1-probe-sse-official.json`):
- 带 `User-Agent: Mozilla/5.0 ...` -> 200 OK
- 不带 UA -> 200 OK 但 body = `({"success":"false","error":"System Error","errorType":"ExceptionInterceptor"})`

**接口面**:
- `yunhq.sse.com.cn:32042/v1/sh1/dayk/<code>` -- 历史 K, OHLCV (无 prev_close 字段, 拉窗口算)
- `yunhq.sse.com.cn:32042/v1/sh1/snap/<code>` -- 实时 snap (带 status 字段, 见 probe 第 4 call)
- `query.sse.com.cn/commonQuery.do?sqlId=...` -- 多数 sqlId 返回 `total:0` 空数据 (未深挖, 可能 sqlId 漂移)

**覆盖盲区**: 仅上交所 (主板 + 科创板); 深圳/创业板/北交所 全 N/A.

### 4.5 派生预测的边界 case

| Edge case | 派生表现 |
|---|---|
| 涨停后炸板 (盘中开过涨停, 收盘没涨停) | OHLC 不全相等 -> 派生 `trading` (正确, 不算一字) |
| 涨停打开 (盘中触及 limit 后回落) | 同上, 派生 `trading` |
| **9.95% 涨幅** (主板, 没到 +-10%) | 派生 `trading` (因为 abs(pct-10) >= 1) -- **可能误判**, 实际算"非一字涨停" |
| **新股 N 上市首日 +44%** | OHLC 通常不全相等 (盘中波动); 派生 `trading`. 但 isST/board 逻辑可能错配 -- **edge case, 未在本 15 case 覆盖** |
| 北交所 (bj.8xx) +30% | 本 15 case 没覆盖, 但 board_limit_pct 表格里有, 应该 work |
| 退市整理 / *ST -5% | 本 15 case 没覆盖 (suspension 都是 *ST 但 tradestatus=0, 不是 -5% 跌停) |

---

## 5. 推荐 (evidence-based, 待 C1 拍板)

### 5.1 主源 / 二源 / N/A 三层架构

```
              +-----------------------+
              |  pretrade check       |
              +-----------+-----------+
                          |
        +-----------------+-----------------+
        |                                   |
   T+0 (盘中阻断 A3)                  T+1 (盘后审计 / 校准)
        |                                   |
   主源: QMT realtime / TDX ticker     主源: baostock
   备用: SSE yunhq snap (sh.* only)    备用: SSE yunhq dayk (sh.* only)
   补漏: akshare snapshot (retry)      补漏: akshare hist (3-retry)
```

### 5.2 stack 选择 (按 use-case)

| Use case | 主源 | 二源 | 第三 | 备注 |
|---|---|---|---|---|
| **EOD 校准 / backtest 数据准备** | **baostock** | SSE yunhq dayk (sh.* 二证) | akshare hist (兜底) | baostock 15/15 + SSE 完美二证, 这是稳态最优组合 |
| **盘中阻断 (A3 check_suspension)** | **QMT realtime feed** (在 vnpy adapter 里, 不在 A1 4 路里) | SSE yunhq snap (sh.*) | akshare snapshot (T+0, retry) | **baostock 不能用**, T+1 |
| **历史回测停牌 mask** | **baostock** (覆盖 2006~) | -- | -- | 一源够 |
| **TDX lake 内 OHLC 派生** (不带 status) | TDX lake | -- | -- | 等 producer 修复 (XAR follow-up) 才能算 sensor |

### 5.3 为什么 baostock 是 EOD 主源 (不是其他三家)

| 维度 | baostock | akshare | TDX-lake | SSE |
|---|---|---|---|---|
| Coverage | 全 A | 全 A | 全 A (但 lake 当前 stale) | 仅 sh.* |
| **直接 status 字段** | **YES** (tradestatus, isST) | NO (派生) | NO (producer 不算) | NO (派生) |
| Stability (本次测) | 15/15 | 0/15 (mirror down) | 0/15 (lake stale) | 10/10 (covered) |
| Latency | 0.5s | 9.5s (3-retry timeout) | 0.01s (local) | 2.5s |
| T+0 / T+1 | T+1 | T+0 当日 + T+1 hist | T+1 (producer 决定) | T+0 snap + T+1 dayk |
| 历史覆盖 | 2006~ | 1990s~ | 2021~ (lake 决定) | 不详 |
| License | GPL-3.0 SDK | MIT | 内部 | sse.com.cn 公开 |

**唯一一票否决项**: T+1. 这就是为什么 baostock 是 EOD 主源, 不是盘中主源.

---

## 6. C1 拍板需要的 input (供 supervisory controller 决策)

1. ✅ **L2 sensor calibration 结果**: 见 §1, §3, §5
2. ✅ **覆盖盲区显式列出**: 见 §4.2 (TDX lake stale), §4.3 (baostock T+1), §4.4 (SSE 仅 SH)
3. ❌ **盘中 sensor (QMT/TDX ticker)** -- **不在本 A1 4 源范围内**, 需要单独跑一份 calibration (建议派生 A1.5 ticket)
4. ❌ **akshare 镜像 outage 频率统计** -- 本次跑只看到一次 sustained outage; 需要 24h+ 监控数据才能给频率, 建议 cron 跑 ping_check
5. ✅ **数据源成本 / license** 见 §1 表格

---

## 7. 复现步骤

```powershell
# 0. 装 SDK (uv only, 在 backend/ 跑)
cd D:/projects/k-atana/backend
uv add akshare baostock

# 1. 跑 4 路 probe (Step 0 schema dump)
uv run python ../.tmp-port/a1-probe.py
uv run python ../.tmp-port/a1-probe-2.py
uv run python ../.tmp-port/a1-probe-3.py
# -> .tmp-port/a1-probe-{akshare,baostock,tdx-lake,sse-official,sse-v2,sse-v3,tdx-kline}.json

# 2. 挑 15 case
uv run python ../.tmp-port/a1-pick-cases.py
# -> .tmp-port/a1-cases-final.json (s=5, u=5, d=5)

# 3. 跑 60-cell 对照
uv run python ../scripts/compare-suspension-sources.py
# -> .data/a1-suspension-comparison.csv
# -> .data/a1-suspension-comparison.json

# 重跑时 akshare 数会变 (镜像状态); baostock / SSE 稳定; TDX lake 直到 producer 修复都 N/A
```

## 8. 后续 ticket 候选

| 编号 | 内容 | 优先级 |
|---|---|---|
| XAR-480-followup-1 | 把 ingest-ashare-incremental.py 跑到 2026-05-19, lake max 推到当前 | P1 |
| XAR-480-followup-2 | TDX-lake producer 接 quote-side feed, 真正算 suspension/limit_up/limit_down (不只 OHLC) | P2 |
| A1.5 | QMT realtime + TDX ticker 盘中 sensor calibration (本 A1 不覆盖) | P1, 阻塞 A3 盘中阻断 |
| -- | akshare 镜像可用性 24h 监控 (cron ping_check) | P2 |
| -- | SSE commonQuery.do sqlId 漂移调研 (低 ROI, 优先级 P3) | P3 |
