# FSC handoff -- pre-live blockers (2026-05-20)

Bridge between human work and FSC (full-self-coding) session for XAR-481/482/483/484.

Items split into:

- `[HUMAN]` -- truly cannot delegate (account/key/hardware/judgment call)
- `[FSC]` -- FSC can execute given the reference below

FSC starts pulling from this doc top-to-bottom. Each `[FSC]` item is self-contained: goal, input contract, steps, acceptance, pitfalls.

---

## [HUMAN] H1. OKX live key 落 D:/keys/.env

OKX live API key 已申请 (你确认). 复制粘贴动作要你做.

```
# D:/keys/.env 追加 (不要 commit, gitleaks 会拦)
KATANA_OKX_LIVE_KEY=<32 char key>
KATANA_OKX_LIVE_SECRET=<base64 secret>
KATANA_OKX_LIVE_PASSPHRASE=<your passphrase>
KATANA_OKX_LIVE_FLAG=1   # 1=live, 0=demo, 0 是当前默认
```

完成后通知 FSC, FSC 接 env loader + smoke ping (见 [FSC] F3).

## [HUMAN] H2. 钉钉/飞书 webhook bot 申请

注册 -> 加群 -> 拿 access_token. FSC 写不了"加群"动作.

落:
```
# D:/keys/.env
KATANA_DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=<token>
KATANA_DINGTALK_SECRET=<sign secret>   # 钉钉加签模式必填
# 或飞书
KATANA_FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/<uuid>
```

完成后通知 FSC, FSC 接 alert router (见 [FSC] F8).

## [HUMAN] H3. 5080 hot standby 硬件 + 网络

- 5080 物理上电 + BIOS Wake-on-LAN 启
- netbird mesh 加 5080 入网, 跟 5090 同 subnet
- SSH 走 netbird tunnel, host key 落 5090 known_hosts (rotate 老的)
- 主电源 UPS (你判断要不要)

FSC 写不了 BIOS / 路由器 / 物理网线动作. FSC 能写的: nssm/WinSW 服务定义 + 应用层心跳 + state replication 协议 (见 [FSC] F10).

## [HUMAN] H4. 风控阈值 + 模型选型最终拍板

FSC 出候选方案 + backtest 数据, 你拍板. 不是 FSC 不能跑, 是阈值需要你的风险偏好.

拍板项:
- 日内 / 5d / 20d 回撤 kill switch 阈值 (% 数字)
- 单股 notional 上限 (绝对值或占组合 %)
- 行业暴露上限
- market impact 模型: Almgren-Chriss vs Kyle vs 经验 sqrt
- drift detection 指标: KL / PSI / Wasserstein 三选一 + 触发阈值

FSC 给你看 backtest 结果 + 候选数字, 你回 "用 X" 就够.

## [HUMAN] H5. 停牌/涨跌停数据源决策

FSC 跑对照 (akshare vs baostock vs TDX flags vs SSE 官方 csv), 出准确率 + 延迟 + 成本对比 (见 [FSC] F4). 你拍板用哪家做生产源.

---

## [FSC] F1. TDX pywinauto 控件树标定

**Linear**: XAR-414  **目标**: 让 `backend/app/trade/account_reader.py::TDXAccountReader` 三方法 (get_positions / get_today_entrusts / get_today_trades) 返回真数据而非 `_uncalibrated()`.

**入参契约**:
- TdxW.exe 已在本机启动 (manual login, 一次性人工操作)
- `scripts/probe-tdx-controls.py` 是 scaffold, 见现状

**步骤**:
1. 读 `D:/projects/k-atana/scripts/probe-tdx-controls.py` -- 已有 scaffold, FSC 补完
2. 用 `pywinauto.application.Application(backend="uia").connect(path="TdxW.exe")` 接管已登录窗口
3. dump 控件树到 `.tmp-port/tdx-control-tree-<timestamp>.txt` (gitignored)
4. 切到 "持仓" / "委托" / "成交" 三个 panel, 各 dump 一次, 落 fixture
5. 写 `TDXAccountReader._extract_positions/_extract_entrusts/_extract_trades` 实现, 输出 schema 对齐 `PositionRecord/EntrustRecord/TradeRecord` (定义在 `account_reader.py` 顶部)
6. 加 unit test fixture: `backend/tests/trade/test_tdx_reader_fixtures.py`, 用录制的 control tree text 做 mock

**验收**:
- `scripts/probe-tdx-controls.py --panel positions` 不抛错, 输出 >=1 行真数据
- 跑 `pytest backend/tests/trade/test_tdx_reader_fixtures.py -v` 全绿
- `account_reader.py::TDXAccountReader.is_available()` 在 TdxW.exe 运行时返回 True

**雷区**:
- TDX 控件名按版本变化 -- 必须落 fixture, 不能硬编码控件路径
- 持仓 panel 切换需要 wait (用 `pywinauto.timings.wait_until` 不要 sleep)
- 数据是字符串展示的 -- "10,000" -> int(10000), "+5.32%" -> 解析为 float
- pywinauto-recorder + inspect.exe 跑在同一 TdxW 实例会卡死. dump 完关掉

**反例**: 不要写 image recognition (pyautogui screenshot OCR) fallback -- 改版即崩.

## [FSC] F2. QMT / EasyXT / xtquant SDK probe + 三路 reader 矩阵

**Linear**: XAR-414  **目标**: 列出 QMT (迅投) / EasyXT / xtquant 三个 SDK 各能拿到哪些字段, 出统一对照表, 决定 multi-source reader 的 fallback 优先级.

**入参契约**:
- 你已在券商开通 QMT, account_id + password 你提供 (落 D:/keys/.env 的 `KATANA_QMT_*`)
- QMT 客户端已装并 mini-login 一次

**步骤**:
1. `uv pip install xtquant` (是 QMT/EasyXT 共用底层 SDK)
2. 写 `scripts/probe-qmt-fields.py`:
   - connect via `from xtquant import xttrader`
   - 调 `query_stock_positions` / `query_stock_orders` / `query_stock_trades` / `query_stock_asset`
   - 每个 endpoint 调 1 次, dump 字段名 + 类型 + 样例值到 `.tmp-port/qmt-probe-<endpoint>.json`
3. 跟 TDX 输出 (F1) + EasyXT (`easytrader.use("qmt").connect()`) 对照
4. 出 `docs/broker-sdk-matrix.md`, 4 列: field / TDX_有无 / QMT_有无 / EasyXT_有无 / 推荐主源
5. 提交 + commit

**验收**:
- `scripts/probe-qmt-fields.py` 运行不报错, 输出 4 个 json 文件
- `docs/broker-sdk-matrix.md` 至少覆盖 30 个核心字段 (symbol / qty / avg_cost / market_value / available_qty / pending_qty / 委托号 / 成交号 / 委托时间 / 成交时间 / 价格 / 方向 / 委托类型 / 状态 / 拒单原因 / 手续费 / 印花税 / 过户费 / 余额 / 冻结 / ...)
- matrix 顶部写明: 主源 = X 因为 [evidence], 二路验证 = Y

**雷区**:
- QMT 的 mini-login 需要每天手动 (有 token 缓存但不保证 24h)
- xtquant SDK 没有官方 pypi 包, 是 QMT 安装目录里的 `userdata_mini/python/` 局部 import. 必须在 QMT 装好的机器跑
- EasyXT 和 xtquant 不能在同一 Python 进程 import (DLL 冲突). 拆两个脚本跑
- 字段命名 QMT 用驼峰 (volume, available, frozen_volume), EasyXT 用下划线 (volume, available, frozen)

## [FSC] F3. OKX live env loader + smoke ping

**Linear**: XAR-413, XAR-430 follow-up  **目标**: 接 OKX live key 进 config, 跑 read-only smoke (账户余额查询), 不发单.

**入参契约**: H1 已完成, env vars 在 D:/keys/.env

**步骤**:
1. 读 `backend/app/trade/okx_bridge.py`, 找 sandbox key 加载点
2. 加 `KATANA_OKX_LIVE_FLAG` 分流: =1 走 live (api.okx.com), =0 走 demo (www.okx.com/api/v5/...)
3. 用 `python-okx` SDK (vendor-sdk-mandatory rule), 不要自写 REST
4. 写 `scripts/smoke-okx-live.py`: 调 `/account/balance` 一次, 输出资金 + 持仓数, 不发单不撤单
5. 加 gitleaks pre-commit hook 检查 (rule 已有, verify 一次)
6. 跑 smoke, 落日志到 `.tmp-port/okx-live-smoke-<timestamp>.json` (gitignored)

**验收**:
- `python scripts/smoke-okx-live.py` 输出 `{"ok": true, "balance_usd": X, "positions": N}`
- key 不出现在任何 git tracked 文件
- `git log -p` grep 不到 key prefix

**雷区**:
- OKX live `api.okx.com` 跟 demo 是不同 base URL, easy to miss
- passphrase 是创建 key 时设的 6-32 字符, 不是登录密码
- live key 第一次调用必须 IP 白名单 -- OKX 会拒, 报 `50110`. 加 IP 走 OKX dashboard 你那边设
- python-okx 0.1.x 的 trade endpoint 跟 spot 分开两个 client 实例

## [FSC] F4. 停牌/涨跌停数据源对照

**Linear**: XAR-480  **目标**: 给 [HUMAN] H5 提供决策证据.

**入参契约**: akshare / baostock / TDX flags / SSE 官网 csv 都可访问

**步骤**:
1. 选 5 个 2026 年实际停牌过的股票 (查 wind 历史或 cn.investing.com), 比如 *ST 海越 / *ST 凯瑞 / 等
2. 选 5 个一字涨停 + 5 个一字跌停 (近 30 天)
3. 写 `scripts/compare-suspension-sources.py`:
   - akshare: `ak.stock_zh_a_st_em()` (ST), `ak.stock_zh_a_stop_em()` (停牌)
   - baostock: `bs.query_history_k_data_plus()` 看 `tradestatus` 字段
   - TDX: 跑 `account_reader.py` 拿当日 flags
   - SSE 官网: 下载 `https://query.sse.com.cn/commonQuery.do?...` (公开 endpoint)
4. 对照表: 同一 (date, symbol), 4 路给的 status 一致率 / 延迟 (T+0 中午 vs 收盘后)
5. 出 `docs/suspension-source-comparison.md`

**验收**:
- 报告里覆盖 15 个 case x 4 source = 60 个数据点
- 一致率 + 延迟 + license/cost 表
- 推荐生产源 + 备份源 + 雷区列表

**雷区**:
- akshare 接的是 eastmoney 镜像, 偶尔 502, 加 retry
- baostock 数据 T+1 出, 不能做实时停牌阻断
- SSE 官网 csv 有反爬, 加 `User-Agent: Mozilla/5.0`
- TDX flags 在收盘后才更新当日, 盘中不可信

## [FSC] F5. 订单状态机 schema + persistence

**Linear**: XAR-481  **目标**: 把现有零散的 reconciliation 模块包成正式状态机.

**入参契约**:
- 现有: `backend/app/trading/adapters/qmt/reconciliation.py`, `crypto/reconciliation.py`, `crypto_pipeline/reconciliation.py` -- 三个纯函数模块
- F1/F2 完成后, account reader 输出 schema 已固定

**步骤**:
1. 设计状态机 (画 mermaid 落 `docs/order-state-machine.md`):
   ```
   draft -> submitted -> {accepted | rejected}
   accepted -> {partial_filled | filled | cancel_pending | expired}
   partial_filled -> {filled | cancel_pending | expired}
   cancel_pending -> {cancelled | filled}  (cancel race condition)
   {filled | cancelled | rejected | expired} -> reconciled  (terminal + 对账完成)
   ```
2. 写 `backend/app/trading/state_machine.py`:
   - `OrderState` enum
   - `OrderEvent` dataclass (timestamp, from_state, to_state, payload, source)
   - `ReconciliationStateMachine` class, append-only event log
3. Persistence: SQLite (`data/orders.db`), schema migration via `backend/app/research/pipeline/data_lake.py` 的现有 migration 模式
4. 加 retry / quarantine: 状态卡在非 terminal 超过 N 秒 (config) -> quarantine queue, 出 alert
5. 测试: `backend/tests/trading/test_state_machine.py` 覆盖 5 个状态转移 + 2 个 race condition

**验收**:
- 状态图跟代码 enum 一致 (mermaid 图里每个箭头都在 `_TRANSITIONS` 表里)
- pytest 覆盖率 >=90% 该文件
- SQLite schema 跑一次 `validate-ashare-lake.py` 等价的 invariant check 通过

**雷区**:
- 不要用 python-statemachine / transitions 库 (over-engineering, while+if 够). 见 anthropic-design-philosophy.
- 持久化 timestamp 用 UTC ns int, 不用 datetime (TZ 坑)
- append-only -- 状态不能 update, 只能新事件

## [FSC] F6. Pre-trade 风控规则评估器

**Linear**: XAR-482  **目标**: 在订单循环里阻断 ST/停牌/涨跌停/异常 spread/北交所门槛/两融状态.

**入参契约**:
- F4 完成 -> 知道用哪个 source 当生产源
- 数据从 `backend/app/research/pipeline/data_lake.py` 读 (PIT-safe)

**步骤**:
1. 在 `backend/app/trade/risk.py` (已存在, 看现状) 加 `PreTradeChecker` class
2. 实现规则 (每条独立可测):
   - `check_suspension(symbol, date) -> Reject | Accept`
   - `check_limit_up_down(symbol, intent_side, last_price) -> Reject | Accept`
   - `check_st(symbol) -> Warn | Accept`  (ST 不一定阻断, 看 config)
   - `check_bj_qualification(symbol, account_assets) -> Reject | Accept`  (北交所 50w 门槛)
   - `check_margin_status(symbol, account_type) -> Reject | Accept`  (两融状态)
3. orchestrator: `PreTradeChecker.check_all(intent) -> CheckResult` 串所有规则, 短路返回首个 Reject
4. 加 audit log: 每次 check 落 SQLite (table `pretrade_audit`), 含 reason
5. 测试: `backend/tests/trade/test_pretrade_checker.py`, fixture 触发每条规则一次

**验收**:
- 5 条规则单元测试全绿
- 集成测试: paper 模式跑 1 天行情, 故意提交 5 个会被各规则拒的 intent, 全部阻断 + audit log 写入
- audit log 可以 SELECT 出最近 1000 次 reject reason 分布

**雷区**:
- 涨跌停判断不能用 +/-10% 硬编码 -- ST 是 5%, 创业板 20%, 北交所 30%. 用 symbol 元数据表查
- 一字板涨跌停 + 你想买入: 即使没成交也要算入风控 (避免下单等开盘瞬间)
- 北交所门槛是 "近 20 个交易日均 >=50w + 24 个月经验". 不能只看当日资产
- 阻断信号要带 `should_retry` flag: 涨跌停打开可能解禁, ST 摘帽前不会解

## [FSC] F7. Kill switch + 三路触发

**Linear**: XAR-482  **目标**: 进程文件 / heartbeat / 显式 API 三路触发, 端到端 <=5s 停所有新单 + 撤所有挂单.

**入参契约**: F5 状态机已落

**步骤**:
1. `backend/app/trade/kill_switch.py`:
   - `KillSwitch` 单例, 内部状态 `tripped: bool` + `trip_reason: str` + `trip_ts: int`
   - 触发源 1: 文件存在 `data/KILL_SWITCH` (人工 `touch` 即生效)
   - 触发源 2: heartbeat 模块超时回调 (心跳 5s 无响应 trip)
   - 触发源 3: 显式 API `kill_switch.trip(reason="manual")`
2. 拦截点: 所有 `Executor.submit_order` 入口 + 所有 broker bridge 的 send 入口, trip 时立即返回 Reject
3. 自动撤单: trip 后异步发起 cancel-all-pending, 走每个 broker bridge 的 `cancel_all()` 方法
4. 解除: 仅显式 API `kill_switch.reset(operator_token=...)` + 文件删除. heartbeat 恢复不自动解
5. 测试: `backend/tests/trade/test_kill_switch.py` 三种触发各一遍 + 撤单 race condition

**验收**:
- 端到端测试: touch 文件 -> 5s 内 `Executor.submit_order` 全 reject + 所有 pending cancel 已发起
- audit log: 每次 trip 落 `kill_switch_audit` table
- 进程重启后 kill switch 状态从 SQLite 恢复 (避免漏检)

**雷区**:
- 不要用 threading.Event 单独管 state -- 多 broker bridge 跨进程要 IPC. 用 SQLite 单行表 + 5s poll 够了
- heartbeat 触发的 trip 不能 reset by heartbeat 恢复 -- 必须人工 ack. 否则网抖一下自动恢复 = 危险
- cancel-all 失败时 (broker 返错) 要 alert 但不阻塞 trip 状态

## [FSC] F8. Alert router (钉钉/飞书)

**Linear**: XAR-483  **目标**: 三档 (critical/warn/info) alert 路由, 含 dedup + cooldown.

**入参契约**: H2 已完成, webhook URL 在 D:/keys/.env

**步骤**:
1. `backend/app/ops/alert.py`:
   - `Alert(level, source, key, message, ts)` dataclass. `key` 是 dedup 标识, 同 key 在 cooldown 期内不重发
   - `AlertRouter.send(alert)` 异步 (asyncio queue)
   - cooldown: critical 1min, warn 5min, info 30min. 可 config.
2. 钉钉 client: 用 `dingtalkchatbot` 库 (pip install). 加签模式签名走 SDK.
3. 飞书 client: 用 `requests.post` 直 hook (飞书没成熟 SDK, 此处例外, schema 简单)
4. 失败重试: 3 次指数退避, 仍失败落 `data/alert_failure.log`
5. 测试: mock webhook endpoint, 验 dedup + cooldown + 失败 fallback

**验收**:
- 同 key 1 分钟内连发 10 条 critical, 只发 1 条到 webhook
- webhook 返 500, 重试 3 次后落 log
- alert message 不含敏感字段 (key/passphrase/account_no -- 在 send 前 redact)

**雷区**:
- 钉钉加签 + 关键字 双校验, 加签模式 secret 在 webhook URL 外面, 别拼错
- 飞书 webhook 限流 5/min, 多账户跑 alert 可能撞, 加 token bucket
- dedup key 不能含 timestamp -- 否则永不 dedup

## [FSC] F9. Heartbeat 监控四路

**Linear**: XAR-483  **目标**: 进程 / 交易桥 / 行情桥 / 风控 四路心跳, 掉线 -> alert + kill switch.

**入参契约**: F7 kill switch + F8 alert router 已落

**步骤**:
1. `backend/app/ops/heartbeat.py`:
   - 每路心跳: 独立 asyncio task, 每 1s 写 SQLite `heartbeat` table (component, last_ts)
   - 监控 task: 每 1s 扫表, 任一路 last_ts > 5s 前 -> 调 `alert_router.send(critical)` + `kill_switch.trip(reason="hb_dead:<component>")`
2. 接入点:
   - 进程主循环: app 启动加 hb tick
   - 交易桥 (qmt/okx/tdx): 每次 send 完成或 keep-alive ping 后 tick
   - 行情桥: 每条 tick 收到后 tick (volume-based)
   - 风控: pre-trade checker 每次 check 后 tick
3. 测试: kill 行情桥进程 -> 5s 内 kill switch 自动 trip + 飞书/钉钉收到 critical

**验收**:
- 端到端: 跑 paper, manual kill 行情桥, 5s 内 kill switch trip + alert 到位
- heartbeat table 不无限增长 (TRUNCATE 旧数据, 保留最近 1h)

**雷区**:
- 心跳间隔 1s vs 检测窗口 5s -- 别把心跳设 5s 同时检测窗口也 5s, 临界态会误报
- 长期 GC pause 会假死 -- Python 不太可能, 但若用 numba JIT 编译阶段会卡 >5s, 启动期 grace 30s
- heartbeat tick 不能阻塞 -- 用 fire-and-forget INSERT, 别在交易关键路径加 await

## [FSC] F10. 5080 hot standby state 复制协议

**Linear**: XAR-483  **目标**: 主 5090 挂掉, 5080 5 分钟内接管, 持仓 + 订单状态 + kill switch 状态从 SQLite + audit log 重建.

**入参契约**: H3 完成 (5080 上电 + netbird), F5/F7 状态都落 SQLite

**步骤**:
1. 设计 state replication 协议 (落 `docs/hot-standby-protocol.md`):
   - 主 5090 每 5s 把 `data/orders.db` + `data/kill_switch_state.db` rsync (走 netbird) 到 5080:`data/standby/`
   - 副 5080 跑一个 watchdog 进程, ping 5090 主进程心跳 (走 HTTP `/health`)
   - 5090 心跳超 60s 死 -> 5080 watchdog 触发 takeover: rename `data/standby/` -> `data/active/`, 启 main app
2. 主从切换是 manual ack (不自动) -- 5080 watchdog 检测到 5090 死后, 发飞书/钉钉 alert "请确认切换", 人 ack 后才接管. 避免脑裂
3. SQLite 用 WAL mode (并发读不冲突) + 每 5s `BEGIN IMMEDIATE; ...; COMMIT` 保 atomic
4. 测试: 拔 5090 网线, 5080 watchdog 接收 alert (60s+5s), 模拟你 ack 后启动接管, 验持仓状态一致

**验收**:
- rsync 一次耗时 < 1s (含 netbird 延迟)
- 拔网线场景: 60s 内 5080 收到 alert, ack 后 5min 内全部 service up
- 切换后跑 1 笔 paper order, 状态机走通, 不漏不重

**雷区**:
- SQLite over network filesystem (e.g. SMB) 会 corrupt -- 必须本地盘 + rsync, 不要 nfs/smb 共享
- WAL 文件 (`-wal`, `-shm`) 也要 rsync, 漏了等于没复制
- netbird 走 udp 偶尔丢包 -- rsync 走 ssh 自带重传, 没事
- "ack 切换" 不能用钉钉点按钮 -- 写一个 5080 上的 `take-over.ps1` 脚本, 人 SSH 进 5080 跑

## [FSC] F11. Risk threshold 候选生成 (for H4)

**Linear**: XAR-482  **目标**: 跑 backtest 出风控阈值候选, 给 [HUMAN] H4 提供拍板证据.

**入参契约**:
- 历史回测引擎 (`backend/app/research/backtest/engine.py`) 已能跑
- 至少有 1 个 ship-gate 过的策略 (factor framework 出的)

**步骤**:
1. 跑 walk-forward backtest 5 年 (2021-2025), 输出每日 PnL 序列
2. 算分布:
   - 日 PnL 5th / 1st / 0.1st percentile (= 日内 drawdown 阈值候选)
   - 5d 滚动最大回撤分布
   - 20d 滚动最大回撤分布
   - 单股 notional 占组合权重 max
   - 行业暴露 (sw_industry l1) max
3. 出 `docs/risk-threshold-candidates.md`:
   - 每个阈值给 3 档候选 (松/中/紧), 标 "选紧 = 历史误拦 X 次/年"
   - 推荐组合 (松-中-紧 中各选一)
4. 给你看, 你拍板

**验收**:
- 报告含 backtest stat 表 + 3 档阈值表 + 推荐
- 阈值数字来自实际分布, 不是拍脑袋
- 推荐组合的 backtest 模拟跑过 (注入风控规则), 显示 "若用这套阈值, 5 年内 N 次 trip, 累计避免损失 X"

**雷区**:
- backtest 注入风控会让 PnL 变, 别用同一份 PnL 既算分布又算阻断效果, 要 hold-out
- ST 股 / 退市股影响分布 -- 阈值表分两版 (含/不含 ST)
- 流动性差的股票 single position 极端值很大 -- 把流动性 filter 后再算

## [FSC] F12. Market impact 模型对比 (for H4)

**Linear**: XAR-484  **目标**: 实现 3 个 impact 模型 + 历史 tick 回灌, 出 R^2 对比, 给 [HUMAN] H4 拍板.

**入参契约**:
- L2 tick 数据 (你 L2 RE 工作产出, 见 docs/level2/)
- 历史成交 fixture (paper 阶段的 fill price vs intent price 差额)

**步骤**:
1. 实现 3 个模型在 `backend/app/research/methodology/market_impact.py`:
   - **Almgren-Chriss**: linear + sqrt term, `impact_bp = eta * (Q/V)^0.5 + gamma * Q/V`
   - **Kyle**: linear, `impact_bp = lambda * Q`
   - **Sqrt empirical**: 单 sqrt, `impact_bp = c * (Q/V)^0.5`
   - 每个模型独立 `fit(history) -> params` + `predict(intent) -> bp`
2. 历史回灌: 取 paper 阶段 100+ 笔 fill, 算实际 slippage_bp = (fill - intent) / intent * 10000
3. fit 三个模型, 算 R^2 + RMSE 在 hold-out 集
4. 出 `docs/market-impact-comparison.md`: 三模型参数 + R^2 表 + 残差图 (matplotlib)
5. 给你看, 你拍板用哪个

**验收**:
- 三模型在 train/test 集都跑得通
- 残差图存 `.tmp-port/impact-residuals-<model>.png`
- 推荐模型 + 推荐参数, 你拍板后 wire 到 backtest engine 替换 const bp

**雷区**:
- A 股市场 sqrt 比 linear 对历史拟合更好 (实证文献), 但有时不显著. 别只看 R^2
- 大单 (Q/V > 5%) 数据少, 拟合不稳, 这块给警告 "外推不可信"
- 单笔成交 partial fill 时, 算 slippage 要按 VWAP, 不是 fill price

---

## 启动顺序 (FSC pick-up 第一份指令)

依赖图:

```
H1 (OKX key)        -> F3 (OKX smoke)
H2 (webhook)         -> F8 (alert)         -> F9 (heartbeat) -> F7 (kill switch)
H3 (5080 硬件)       -> F10 (hot standby)
F1 (TDX) || F2 (QMT) -> F5 (state machine) -> F6 (pretrade)
F4 (停牌源)          -> F6
F11/F12 (候选)       -> H4 (拍板)          -> F6/F7 finalize
```

并行:
- 立即可起: F1 (TDX), F2 (QMT), F4 (停牌对照), F11 (风控阈值), F12 (impact 对比)
- H1 后: F3
- H2 后: F8 -> F9 -> F7
- H3 后: F10
- F1+F2 后: F5 -> F6
- F11/F12 + H4 后: F6/F7 终版

FSC 第一票建议: F1 + F2 + F4 三路并行 (都是 read-only probe, 不互相阻塞).

---

## 验收门 (FSC 全完后人复核)

- [ ] 所有 [HUMAN] 项 H1-H5 完成
- [ ] 所有 [FSC] 项 F1-F12 通过自己的验收契约
- [ ] paper 模式跑 1 周, alert / heartbeat / kill switch / pretrade 全部 wired 且触发过
- [ ] 5080 standby 切换演练通过 1 次
- [ ] gitleaks pre-commit 验证零 key 泄漏
- [ ] 你最终 ack "可以上钱"
