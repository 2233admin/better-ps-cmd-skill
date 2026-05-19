# P1 5080 Handoff — A股 alt-data ingest

**Created**: 2026-05-20
**From**: 5090 Claude (Curry's main session)
**To**: 5080 Claude (data experiment box)
**Scope**: P1.1 LHB + P1.2 北向 PIT ingest. P1.3 regime + P1.4 capacity 留在 5090 做。

## Why 5080

- akshare 限流 + tushare 卡 → 数据实验跑在 5080，不阻塞 5090 主开发
- Curry 明确指派 (2026-05-20): "akshare 免费 数据也不少 tushare我们买了一个月但是可能很卡 我建议去 5080 做这个数据实验"

## Wheel verdict 摘要 (full table → `docs/P1-WHEEL-AUDIT.md`)

- **LHB**: akshare primary (`stock_lhb_detail_em` + `stock_lhb_stock_detail_em(flag=买入/卖出)` 覆盖 top5 营业部 + 净额) → tushare `top_list`/`top_inst` paid fallback
- **HSGT**: akshare primary (`stock_hsgt_fund_flow_summary_em` + `stock_hsgt_hist_em` + `stock_hsgt_hold_stock_em`) → tushare `hsgt_top10` + `hk_hold` paid fallback (akshare 缺 top10 成交活跃股)
- **Regime (5090 跑)**: hmmlearn primary 3-state HMM (expanding/rolling refit, PIT-safe), ruptures 做 change-point feature only

**Pandas caveat**: akshare/tushare 内部返回 pandas。5080 ingest 路径允许局部 import pandas，但 raw evidence 落 parquet 后立即 polars 化，**研究层禁止 pandas**。如果坚持绝对禁 pandas → 走 direct HTTP fallback (efinance 字段不全，schema-mismatch)。

## 必读 (按顺序)

1. `docs/P1-WHEEL-AUDIT.md` — 完整 wheel audit + 字段覆盖明细 + 源码链接
2. `CLAUDE.md` — domain glossary
3. `docs/ASHARE_DATA_PIT_SPEC.md` — PIT contract 标准
4. `docs/DATA_LAKE_LAYOUT.md` — lake 目录约定
5. `.planning/intel/` (若存在) + 已有 ingest scripts (`scripts/ingest-ashare-*.py`) 看 PIT wrap 模式

## Linear 单

- XAR-459 P1.1 LHB (owner:5080)
- XAR-460 P1.2 北向 (owner:5080)
- XAR-461 P1.3 regime (5090)
- XAR-462 P1.4 capacity + cost model (5090)
- XAR-463 Kronos retraining (parked)

## 数据落地约定 (codex 给的两层路径)

**Raw evidence (5080 抓取层, 不进 promote pipeline)**:
```
D:/projects/k-atana/.data/lake/raw/
  ├─ lhb/source={akshare,tushare}/run_id=YYYYMMDD-HHMMSS/trade_date=YYYY-MM-DD/part-*.{json,parquet}
  └─ hsgt/source={akshare,tushare}/run_id=.../trade_date=.../part-*.{json,parquet}
```

**Promoted PIT sidecars (lake 标准位置, 5090 消费)**:
```
DATA/Ashare/pit/
  ├─ lhb_daily_pit/                # 日榜汇总 (trade_date, symbol, reason, net_buy, ...)
  ├─ lhb_broker_detail_pit/        # top5 营业部明细
  ├─ hsgt_flow_daily_pit/          # 沪/深股通净流入 + quota 余额
  ├─ hsgt_hold_stock_pit/          # 个股北向持股
  ├─ hsgt_top10_pit/               # 沪/深 top10 成交活跃股
  └─ regime_daily_pit/             # (5090 写, 列在此处供参考)
```

`.data/` 在 `.gitignore`，**不入 git**。push 给 5090 走数据通路 (duckdb 复制 / parquet rsync)，不走 git。`DATA/Ashare/pit/` 走现有 PIT sidecar 通路 (参见 `docs/ASHARE_DATA_PIT_SPEC.md`)。

## PIT contract (强制)

每条记录 3 列 timestamp，缺一不可：

- `event_time` — 事件**发生**的市场时间（T 日收盘 / 披露时间）
- `available_at` — 数据**可被使用**的时间（披露 + 处理延迟，e.g. LHB 是 T+1 09:30 开盘前）
- `source_updated_at` — 数据**抓取**入库的 wall-clock 时间

回测/因子计算只能用 `available_at <= now()` 的行。任何下游 join 强制 `lhs.available_at <= rhs.available_at`。

## 实施步骤 (P1.1 LHB 为例，P1.2 同模式)

### Step 0: 读 wheel-audit verdict
读 `docs/P1-WHEEL-AUDIT.md` → 确认数据源选型 (预期 akshare primary, tushare fallback)。如果 verdict 推荐别的，stop 一下问回 5090。

### Step 1: SDK probe
不要直接 batch 拉。先单日 / 单股 probe，dump 原始 response shape：

```python
import akshare as ak
df = ak.stock_lhb_detail_em(start_date="20260519", end_date="20260519")
print(df.dtypes); print(df.head().to_dict()); print(len(df))
```

记录字段名 / dtype / 单位 (万元? 元?) / 时区 / 是否带营业部明细。**写到 progress.txt**。

### Step 2: 单日 fixture parquet
- 写 1 个 trade_date 的 parquet 到 `.data/lake/raw/lhb/event_date=2026-05-19/part-0.parquet`
- 字段标准化：`symbol` (6位字符串)，金额单位统一成元，时间统一 Asia/Shanghai
- PIT 三列写好

### Step 3: 验证 (TDD before bulk)
- `scripts/validate-ashare-lake.py` 加 LHB schema check (若没有就先加)
- 读回来：`pl.scan_parquet(...).filter(pl.col("symbol") == "000001").collect()`
- 手动核对 1-2 条 vs 东方财富网页

### Step 4: 历史回填 (按 wheel verdict 给出的可用历史)
- 串行跑（akshare 速率敏感），3-5 日一批 commit
- 单失败 → log + skip，**不要 silent partial state**（参考 feedback_continue_on_error_silent_partial_state）
- 覆盖率写 `_coverage.json`

### Step 5: tushare fallback (若 akshare 字段不全)
- 读 D:/keys/.env 拿 TUSHARE_TOKEN（如未配，先问回 5090）
- 用 `ts.pro_api().top_list` + `top_inst`
- 字段映射成 akshare 的 schema

## 禁止

- 直接 commit `.data/` 内容 (gitignored 已经防了，但别 force-add)
- 把 D:/keys/.env 里的 token 写到 script (用 `os.environ`)
- silent skip 失败日 (XAR-456 那种空字符串默认 bug 是教训)
- batch 跑前不测 5 条 (`test-before-bulk` 硬规则)
- 不读 wheel-audit 就开工

## Push 协议

数据：不进 git。
代码：进 `scripts/ingest-ashare-{lhb,hsgt}.py`，commit 直推 master (k-atana 无 PR review)。
memory：会话末更新 `~/.claude/projects/.../memory/project_katana_*.md`，跑 `memory-push.sh`。

## 跑完汇报

写到这个文件 `## 5080 Report (date)` 段（append）：
- 抓了哪些 trade_date 范围
- 总行数 / 平均字段缺失率
- akshare vs tushare 字段对比表
- 已知坑 / unresolved
- 下一步建议

5090 next session 读到这里继续。

## Unresolved

- tushare 月卡剩余天数 + 积分上限 → 5080 在 ts.pro_api().user 拉
- 是否需要 SSE/SZSE 官方 LHB PDF 兜底 (akshare 字段不全时) → 等 5080 实际跑出来再说

## 5080 Report (待 5080 填)

_(留空，5080 Claude 完成后回填)_
