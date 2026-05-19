# P1 Wheel Audit (2026-05-20)

GitHub metadata was queried live from `https://api.github.com/repos/...` on
2026-05-20 Asia/Shanghai. Rows marked as mirrors or API collections are not
treated as production wheels unless the verdict says so.

Stack caveat: AkShare/Tushare/efinance are source adapters and may return pandas
objects internally. For k-atana, keep them isolated inside 5080 ingest, persist
raw evidence immediately, and normalize/promote with polars/duckdb only. If
`no pandas` is interpreted as an absolute dependency ban, these source wheels are
not admissible and the fallback becomes direct HTTP endpoint adapters.

## Task 1: LHB 龙虎榜

| 候选 | gh stars | pushed_at | license | LHB 字段覆盖 | verdict |
|---|---:|---|---|---|---|
| akshare (`akfamily/akshare`) | 19487 | 2026-05-18 | MIT | 全量接近: `stock_lhb_detail_em` 覆盖交易日、代码、名称、解读、上榜原因、净买额、买入额、卖出额、换手率；`stock_lhb_stock_detail_date_em` + `stock_lhb_stock_detail_em(flag=买入/卖出)` 覆盖 top5 营业部、买入/卖出金额、净额、类型；另有 `stock_lhb_stock_statistic_em`, `stock_lhb_jgmmtj_em`, `stock_lhb_hyyyb_em`, `stock_lhb_yyb_detail_em`, `stock_lhb_yybph_em`, `stock_lhb_traderstatistic_em`, Sina 系列。 | fit-primary: 免费、字段最贴近；`个股/大盘异动归属` 需要由 `上榜原因`/指数状态派生；`available_at=T+1 09:30`。 |
| tushare (`waditu/tushare`) | 15005 | 2024-03-13 | BSD-3-Clause | 全量但付费/慢: `top_list` 覆盖日榜汇总、上榜理由、买卖额、净额，历史 2005 至今，需 2000 积分；`top_inst` 覆盖营业部 `exalter`、买卖方向 `side`、买入/卖出额、净额、理由，需 5000 积分。 | fit-fallback-paid: 字段完整，适合 AkShare 缺口校验/补数；受积分和响应速度约束。 |
| efinance (`Micro-sheep/efinance`) | 3704 | 2026-03-18 | MIT | 部分: `ef.stock.get_daily_billboard` 覆盖 EastMoney 龙虎榜汇总、上榜日期、原因、净买额、买入额、卖出额、成交额；未发现 top5 买卖营业部明细接口。 | schema-mismatch: 只能做 summary 备用，不满足 P1 明细表。 |
| baostock (`shimencaiji/baostock` mirror; official repo not found) | 105 | 2019-10-25 | none | 无: fetched mirror examples/API list 未见龙虎榜、营业部、上榜原因接口；`baostock/baostock` GH API 404。 | no-fit: 不覆盖 LHB，且镜像长期未维护。 |
| EastMoney API collection (`minuo/eastmoney-api-collection`) | 0 | 2026-04-01 | none | 无: JS/Vue API notes 未见 LHB 明细；不是 Python wheel。 | no-fit: 可作人工接口线索，不纳入依赖。 |

**Recommendation**: 用 AkShare 做 primary，Tushare `top_list`/`top_inst` 做 paid fallback。AkShare 免费且字段足够接近目标 schema；Tushare 在 top5 营业部明细缺口或 EastMoney 接口漂移时补数。

## Task 2: 北向资金

| 候选 | gh stars | pushed_at | license | HSGT 字段覆盖 | verdict |
|---|---:|---|---|---|---|
| akshare (`akfamily/akshare`) | 19487 | 2026-05-18 | MIT | 大部分: `stock_hsgt_fund_flow_summary_em` 覆盖沪股通/深股通成交净买额、资金净流入、当日资金余额、交易状态；`stock_hsgt_hist_em` 覆盖北向/沪股通/深股通历史净买额、买入/卖出、余额、持股市值，样例自 2014-11-17；`stock_hsgt_hold_stock_em` 和 `stock_hsgt_stock_statistics_em` 覆盖个股持股股数、市值、占比；另有个股详情、板块/机构排行。未在 `stock_hsgt_*` 发现沪/深 top10 成交活跃股。 | fit-primary-partial: 免费且覆盖资金流、余额、持仓；top10 成交活跃股需 Tushare 或直连 EastMoney 补。 |
| tushare (`waditu/tushare`) | 15005 | 2024-03-13 | BSD-3-Clause | 大部分: `moneyflow_hsgt` 覆盖 `hgt`, `sgt`, `north_money`, `south_money`，2000 积分起；`hsgt_top10` 覆盖沪/深股通每日十大成交股、成交额、净额、买入、卖出，页面未明示积分；`hk_hold` 覆盖沪深股通持股数量/占比，120 积分试用、2000 积分正常；`stock_hsgt` 股票列表 3000 积分起。quota 余额未在 `moneyflow_hsgt` 输出中出现。 | fit-fallback-paid: top10 最直接；资金/持仓可兜底，但 quota 余额仍优先 AkShare/EastMoney。 |
| EastMoney API collection (`minuo/eastmoney-api-collection`) | 0 | 2026-04-01 | none | 部分: JS demo 有 `getNorthFund` / `getNorthboundCapital`，偏实时/分时北向资金；未见 Python package、历史 holdings、top10、quota 完整封装。 | research-only: 可逆向接口，不作为 wheel。 |
| efinance (`Micro-sheep/efinance`) | 3704 | 2026-03-18 | MIT | 无/很弱: `get_realtime_quotes` 支持沪股通/深股通行情分类，但未发现北向资金流、quota、持仓、top10 成交接口。 | no-fit: 不满足北向资金 alt-data。 |
| baostock (`shimencaiji/baostock` mirror; official repo not found) | 105 | 2019-10-25 | none | 无: 未见沪股通/深股通/北向资金接口。 | no-fit。 |

**Recommendation**: 用 AkShare 做北向资金 primary，Tushare `hsgt_top10` + `hk_hold` 做 paid fallback。原因是 AkShare 覆盖免费资金流/余额/持仓，Tushare 覆盖 AkShare 缺失的沪深股通 top10 成交活跃股。

## Task 3: Regime detection

| 候选 | gh stars | pushed_at | license | regime 字段覆盖 | verdict |
|---|---:|---|---|---|---|
| hmmlearn (`hmmlearn/hmmlearn`) | 3375 | 2024-10-31 | BSD-3-Clause | 直接覆盖 3-state Gaussian HMM；`score_samples`/`predict_proba` 输出状态后验概率；吃 numpy array，polars 可 `.to_numpy()`。无 `partial_fit`。 | fit-primary-prototype: 用 expanding/rolling refit 保证 PIT；禁止对全历史一次 `predict_proba` 后回填过去。 |
| ruptures (`deepcharles/ruptures`) | 2030 | 2026-04-06 | BSD-2-Clause | 覆盖 PELT/Binseg/Window change-point；不给牛/熊/震荡概率。默认离线分段会看未来。 | fit-as-feature: 只做 rolling as-of change-point alarm / execution guard，不做主 regime label。 |
| pomegranate (`jmschrei/pomegranate`) | 3532 | 2025-03-06 | MIT | HMM 能力更强，`DenseHMM` 有 `forward/backward/forward_backward` 和 `summarize/from_summaries`；依赖 torch，API 比 hmmlearn 重。 | secondary: hmmlearn 不够时再用；P1 不建议先引 torch 依赖。 |
| numpyro / pystan (`pyro-ppl/numpyro`; `stan-dev/pystan`) | 2681 / 365 | 2026-05-17 / 2026-03-12 | Apache-2.0 / ISC | 可手写 Bayesian HMM，能给 posterior uncertainty；需要 JAX 或 Stan 模型/采样，工程复杂度高。 | research-only: 不适合 P1 首轮；若后续要全贝叶斯 regime，再优先 numpyro。 |
| arch (`bashtage/arch`) | 1522 | 2026-04-06 | NOASSERTION | repo tree 搜索未发现 regime/Markov/HMM 模块；核心是 volatility/GARCH 生态。 | no-fit: 不作为 regime-switching wheel。 |

**Recommendation**: 用 hmmlearn 做 primary 3-state HMM，ruptures 做 change-point feature/execution guard。PIT 做法是每个 `as_of` 只用 `available_at <= as_of` 的 CSI300/CSI500/Wind All-A returns 拟合或更新窗口，并只写当日状态概率；离线全序列分段结果不能回填历史。

## Cross-task: 集成路径

5080 raw evidence 可以先落到 `D:/projects/k-atana/.data/lake/raw/{lhb,hsgt}/`，按 source/run_id/trade_date 分区保存原始 JSON/CSV/parquet；promotion 时写入现有 A-share lake 约定的 PIT sidecars，例如 `DATA/Ashare/pit/lhb_daily_pit/`, `DATA/Ashare/pit/lhb_broker_detail_pit/`, `DATA/Ashare/pit/hsgt_flow_daily_pit/`, `DATA/Ashare/pit/hsgt_hold_stock_pit/`, `DATA/Ashare/pit/hsgt_top10_pit/`, `DATA/Ashare/pit/regime_daily_pit/`。PIT 化在 5080 ingest/promote script 内完成：`event_time` 为交易日或源时间戳，LHB/HSGT 日频 `available_at` 保守设为下一交易日 09:30，`source_updated_at` 用源更新时间或抓取时间；5090 只读 PIT/feature view，并统一过滤 `available_at <= as_of`。

## Source links

- GitHub API: `https://api.github.com/repos/{owner}/{repo}`
- AkShare stock docs/source: https://github.com/akfamily/akshare/blob/main/docs/data/stock/stock.md, https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_lhb_em.py, https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_hsgt_em.py
- Tushare docs: https://tushare.pro/document/2?doc_id=106, https://tushare.pro/document/2?doc_id=107, https://tushare.pro/document/2?doc_id=47, https://tushare.pro/document/2?doc_id=48, https://tushare.pro/document/2?doc_id=188, https://tushare.pro/document/2?doc_id=398
- efinance docs/source: https://github.com/Micro-sheep/efinance/blob/main/README.md, https://github.com/Micro-sheep/efinance/blob/main/efinance/stock/getter.py
- Regime repos: https://github.com/hmmlearn/hmmlearn, https://github.com/deepcharles/ruptures, https://github.com/jmschrei/pomegranate, https://github.com/pyro-ppl/numpyro, https://github.com/stan-dev/pystan, https://github.com/bashtage/arch
