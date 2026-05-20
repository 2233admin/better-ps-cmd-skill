# Pre-live blocker handoff (2026-05-20)

K-atana 距上钱的全部 blocker, 用 **cascade 控制系统** 视角设计 (方法论 `E:/knowledge/05-Engineering/exp-2026-05-20-abc-task-triage-for-agents.md`).

## 两个正交维度

```
维度 1: 执行路径 (谁当 controller)
  A = FSC autonomous           (controller = code, Curry 不在场)
  B = HUMAN+CODE sensor-bridge (controller = code, Curry 当 missing sensor+actuator)
  C = supervisory control      (controller = Curry, 慢环价值判断)

维度 2: cascade 层 + trigger 类型 (loop 怎么跑)
  L0  战略层 (months loop)        trigger: manual only
  L1  外环 (weeks loop)            trigger: manual / event (drift) / clock (review)
  L2  中环 (days-weeks loop)       trigger: manual / event / clock (任选, 不默认)
  L3  内环 (ms-s loop)             trigger: continuous always-on
```

**Manual push 是合法 controller 设计**, 不是降级 fallback. L1/L2 不必"每日跑", 罕见/价值密集事件 manual trigger 更优.

每项任务下方标 **[桶 | L层 | trigger]** 三元组.

依赖图 + cascade set-point 流向见末尾.

---

## A 桶 -- FSC 自动 (controller = code)

### A1. 停牌/涨跌停数据源对照 (XAR-480)

**[A | L2 | manual or event-on-source-change]** -- 不是 daily scheduler, 是 plant identification (sensor 输出对比), 跑一次出报告即可, 数据源变化或半年 review 才重跑.

**目标**: 跑 akshare / baostock / TDX / SSE 官网四路对照, 出准确率 + 延迟 + 成本表, 给 C1 拍板提供证据. **本质 = sensor calibration**, 给 L1 supervisory controller 提供选 sensor 的依据.

**步骤**:
1. 选 5 个 2026 年实际停牌股 + 5 个一字涨停 + 5 个一字跌停 (查 wind 历史)
2. 写 `scripts/compare-suspension-sources.py`:
   - `ak.stock_zh_a_st_em()` / `ak.stock_zh_a_stop_em()`
   - `bs.query_history_k_data_plus(... fields="tradestatus")`
   - SSE 官网 csv (`https://query.sse.com.cn/commonQuery.do?...`, 加 `User-Agent: Mozilla/5.0` 绕反爬)
3. 出 `docs/suspension-source-comparison.md`, 15 case x 4 source = 60 数据点 + 一致率 + 延迟 + license

**验收**: 报告含对照表 + 推荐主源 + 备份源 + 雷区. baostock 是 T+1 (不能做盘中阻断), 报告里必须标.

**雷区**: akshare 接 eastmoney 镜像偶尔 502, 加 retry; SSE 反爬必加 UA.

### A2. 订单状态机 + persistence (XAR-481)

**[A | L3 | continuous]** -- 状态机本身是 L3 内环 plant 模型, 服务于 pretrade/kill switch 等 L3 controllers. 一次性写好不重跑.

**目标**: 把 `backend/app/trading/adapters/{qmt,crypto,crypto_pipeline}/reconciliation.py` 三个纯函数模块包成正式状态机. **本质 = plant state observer**, 让 L3 多个 controller 共享统一观测.

**状态图** (落 `docs/order-state-machine.md`):
```
draft -> submitted -> {accepted | rejected}
accepted -> {partial_filled | filled | cancel_pending | expired}
partial_filled -> {filled | cancel_pending | expired}
cancel_pending -> {cancelled | filled}    # cancel race
{filled|cancelled|rejected|expired} -> reconciled
```

**步骤**:
1. `backend/app/trading/state_machine.py`: `OrderState` enum, `OrderEvent` dataclass (ts, from, to, payload, source), `ReconciliationStateMachine` class (append-only event log)
2. SQLite (`data/orders.db`), WAL mode, schema migration 走现有 `data_lake.py` 模式
3. retry/quarantine: 非 terminal 卡 > N 秒 -> quarantine + alert
4. `backend/tests/trading/test_state_machine.py` 覆盖 5 状态 + 2 race condition

**验收**: pytest >=90% 该文件覆盖率; mermaid 图每箭头都在 `_TRANSITIONS` 表里.

**雷区**: 不用 python-statemachine/transitions 库 (over-engineering, while+if 够); timestamp 用 UTC ns int 不用 datetime.

### A3. Pre-trade 风控评估器 (XAR-482)

**[A | L3 | continuous]** -- 实时 disturbance rejection. set-point 从 C1 (数据源) + C2 (阈值) cascade 下来.

**目标**: 订单循环里阻断 ST/停牌/涨跌停/北交所门槛/两融状态. **本质 = L3 控制器**, 干扰 (ST/停牌等) 出现时 reject 订单, 跟踪 "合规态" set-point.

**前置**: A1 完成 (知道用哪个数据源) + C1 拍板.

**步骤**: `backend/app/trade/risk.py` 加 `PreTradeChecker`, 5 条规则各独立可测 (`check_suspension/limit/st/bj_qualification/margin`), `check_all` 短路返回首个 Reject + 落 audit log (SQLite `pretrade_audit`).

**验收**: 5 规则单测全绿; paper 跑 1 天故意触发 5 类 reject 全部阻断 + audit 写入.

**雷区**:
- 涨跌停不是 +/-10% 硬编码 (ST 5%, 创业板 20%, 北交所 30%) -- 查 symbol 元数据
- 一字板未成交也要算入风控 (避免开盘瞬间下单)
- 北交所是 "近 20 日均 >=50w + 24mo 经验", 不是当日资产
- 阻断要带 `should_retry` (涨跌停可能打开, ST 摘帽前不解)

### A4. Kill switch 三路触发 (XAR-482)

**[A | L3 | continuous + event]** -- 三路 actuator: 文件 (manual override), heartbeat-event, 显式 API. reset 必须 manual (避免自动 spurious recovery).

**目标**: 文件 / heartbeat / 显式 API 三路触发, <=5s 停所有新单 + 撤所有挂单. **本质 = L3 emergency actuator**, supervisory (Curry) 直接 manual override 入口.

**前置**: A2 状态机已落.

**步骤**: `backend/app/trade/kill_switch.py` 单例 + SQLite 持久化, trip 拦截 `Executor.submit_order` + broker bridge `send`, async cancel-all-pending, 解除仅显式 API + 文件删除 (heartbeat 恢复不自动解).

**验收**: touch 文件 -> 5s 内全 reject + cancel 已发起; 进程重启后状态从 SQLite 恢复.

**雷区**: 不用 threading.Event (跨进程要 IPC, SQLite 单行 + 5s poll 够); heartbeat-trip 必须人工 ack, 否则网抖自动恢复 = 危险.

### A5. Heartbeat 四路 (XAR-483)

**[A | L3 | continuous]** -- L3 sensor (liveness), 喂 L3 actuator (kill switch) + L1 alert router.

**目标**: 进程 / 交易桥 / 行情桥 / 风控 四路心跳, 掉线 -> alert + kill switch trip. **本质 = L3 liveness sensor**, 干扰 (进程死/网抖) 检出 -> 触发内环 disturbance rejection.

**前置**: A4 kill switch + A7 alert router (alert 可后挂).

**步骤**: `backend/app/ops/heartbeat.py`, 每路 1s 写 SQLite `heartbeat` table, 监控 task 1s 扫表, last_ts > 5s -> trip + alert. 接入点: app 主循环 + 每 broker bridge send-complete 后 + 行情每 tick + risk check 后.

**验收**: kill 行情桥, 5s 内 kill switch trip + alert 到位; heartbeat table TRUNCATE 1h 旧数据.

**雷区**: 心跳间隔 1s 时检测窗口必须 >= 5s 否则临界态误报; numba JIT 启动期 grace 30s.

### A6. Alert router (XAR-483)

**[A | L1-L3 bridge | event]** -- 跨层 alert 通道. L3 event 触发 -> Curry (L0/L1 supervisory controller) 感知通道. dedup/cooldown 是 sensor 噪声滤波.

**前置**: B2 webhook 已落 .env.

**目标**: 三档 (critical/warn/info) 路由, dedup + cooldown. **本质 = L3 -> L0/L1 上行 sensor**, 让 supervisory controller (Curry) 在慢环里看到内环异常.

**步骤**: `backend/app/ops/alert.py`, `Alert(level, source, key, message, ts)` dataclass, `AlertRouter.send` 异步, cooldown critical 1min/warn 5min/info 30min. 钉钉用 `dingtalkchatbot` SDK, 飞书直 `requests.post` (无成熟 SDK, schema 简单, 这条是 vendor-sdk rule 例外). 失败 3 次指数退避后落 `data/alert_failure.log`.

**验收**: 同 key 1min 内连发 10 条 critical 只发 1 条; webhook 500 重试 3 次后落 log; alert message 必须 redact key/passphrase/account_no.

**雷区**: 钉钉加签 secret 在 webhook URL 外面别拼错; 飞书 webhook 5/min 限流加 token bucket; dedup key 不能含 timestamp.

### A7. 风控阈值候选 (XAR-482, 喂 C2)

**[A | L1 | manual or event]** -- 不是 daily, 是 plant identification 一次性跑 + 季度 review 或 drift 报警时重跑. Curry 主动推 / 策略变化触发 / 半年 review trigger 三选一.

**目标**: walk-forward backtest 5 年出阈值分布, 给 C2 拍板提供数字. **本质 = L1 supervisory controller 的 sensitivity 报告**, "set-point 改 X% -> output 改 Y%".

**步骤**:
1. Backtest engine 跑 2021-2025, 出每日 PnL 序列
2. 算分布: 日 PnL 5th/1st/0.1st percentile (= 日内回撤阈值候选), 5d/20d 滚动 max drawdown, 单股 notional 占组合权重 max, 行业暴露 (sw_industry l1) max
3. 出 `docs/risk-threshold-candidates.md`, 每阈值 3 档候选 (松/中/紧) + "选紧 = 历史误拦 X 次/年" + 推荐组合
4. 给推荐组合做 backtest 模拟阻断, 算 "5 年内 N 次 trip, 累计避免损失 X"

**验收**: 报告含 backtest stat + 3 档表 + 推荐. 阈值来自实际分布不拍脑袋.

**雷区**: backtest 注入风控会让 PnL 变, 别用同份 PnL 既算分布又算阻断效果, hold-out; ST 影响分布 -- 分两版报告 (含/不含); 流动性差 single position 极端值大, 流动性 filter 后再算.

### A8. Market impact 三模型对比 (XAR-484, 喂 C3)

**[A | L1 | manual or event-on-fill-accumulation]** -- 不是 daily, 是 plant identification + adaptive recalibration trigger. 实盘 fill 数据积累到一定量 / drift 报警 / Curry 主动推, 才重跑.

**目标**: 实现 Almgren-Chriss / Kyle / Sqrt 三模型 + 历史回灌, 出 R^2 表 + 残差图. **本质 = L1 controller synthesis evidence**, plant 辨识 (impact 模型) + 让 supervisory 选 controller 类型.

**步骤**:
1. `backend/app/research/methodology/market_impact.py`:
   - **AC**: `impact_bp = eta * (Q/V)^0.5 + gamma * Q/V`
   - **Kyle**: `impact_bp = lambda * Q`
   - **Sqrt**: `impact_bp = c * (Q/V)^0.5`
   - 每模型独立 `fit(history) -> params` + `predict(intent) -> bp`
2. 取 paper 阶段 100+ 笔 fill, 实际 slippage_bp = (fill - intent)/intent * 10000
3. fit 三模型, hold-out 集算 R^2 + RMSE
4. `docs/market-impact-comparison.md` + 残差图 `.tmp-port/impact-residuals-<model>.png`

**验收**: 三模型 train/test 跑得通, 推荐模型 + 参数, 等 C3 拍板.

**雷区**: A 股 sqrt 经验上比 linear 拟合好但不一定显著, 别只看 R^2; 大单 (Q/V > 5%) 数据少, 这段标 "外推不可信"; partial fill 算 slippage 按 VWAP 不是 fill price.

---

## B 桶 -- HUMAN+CODE sensor/actuator bridging

Curry 当 missing sensor + actuator, 把 plant 状态喂给 controller (code) / 把 controller 输出写到 plant. Curry 不当 controller, 只当桥. **B 桶 trigger 都是 manual (一次性消耗 Curry 时间)**.

### B1. OKX live env loader + smoke (XAR-413)

**[B | L3 setup | manual one-shot]** -- OKX key 是 L3 controller 的认证 actuator 配置. 一次性贴 + smoke, 不重跑 (除非 key rotate, 那时 event-triggered).

**为什么 B**: OKX live key 你已申请, 但贴进 `D:/keys/.env` 是你的物理动作 (FSC 没 actuator 写本机 keys 文件).

**Curry 现场步骤**:
1. 你登 OKX 后台拿 32-char key + base64 secret + passphrase
2. 落:
   ```
   KATANA_OKX_LIVE_KEY=<...>
   KATANA_OKX_LIVE_SECRET=<...>
   KATANA_OKX_LIVE_PASSPHRASE=<...>
   KATANA_OKX_LIVE_FLAG=1
   ```
3. OKX dashboard 加本机出口 IP 白名单 (否则首次调用返 `50110`)

**Claude/Codex 写**:
1. 读 `backend/app/trade/okx_bridge.py` sandbox key 加载点, 加 `KATANA_OKX_LIVE_FLAG` 分流 (=1 走 `api.okx.com`, =0 走 demo)
2. 用 `python-okx` SDK 不自写 REST (vendor-sdk-mandatory rule)
3. `scripts/smoke-okx-live.py` 调 `/account/balance` 一次, read-only, 不发单
4. gitleaks 验证零泄漏

**验收**: `python scripts/smoke-okx-live.py` 输出 `{"ok": true, "balance_usd": X, "positions": N}`; `git log -p | grep` 找不到 key prefix.

### B2. 钉钉/飞书 webhook 申请 (XAR-483)

**[B | L1-L3 bridge setup | manual one-shot]** -- alert router 的物理 actuator (Curry 收 alert 的通道). 一次性配, 不重跑.

**为什么 B**: 申请 bot + 加群是你的物理动作, FSC 没你的钉钉/飞书账号 (无可控 actuator 进入这些 closed-system).

**Curry 现场步骤**: 注册 bot -> 加群 -> 拿 token + 加签 secret -> 落 .env:
```
KATANA_DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=<token>
KATANA_DINGTALK_SECRET=<sign secret>
# 或
KATANA_FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/<uuid>
```

**Claude/Codex 写**: 见 A6 (alert router), B2 完成后 A6 解锁.

### B3. TDX pywinauto 控件树标定 (XAR-414)

**[B | L3 sensor setup | manual one-shot per TDX version]** -- TDX 是 L3 plant 的 sensor (账户/持仓/委托). Curry 提供登录态, agent 抓控件树 = sensor calibration. 重跑 trigger = TDX 改版.

**为什么 B**: TdxW.exe 登录态对 FSC 不可观 (无 sensor 接入此进程), 必须 Curry 当 missing sensor (登录 + 切 panel).

**Curry 现场步骤**:
1. 启 TdxW.exe + 登录券商账户
2. 切到 持仓 panel (一次), 让 Claude/Codex dump
3. 切到 委托 panel, dump
4. 切到 成交 panel, dump

**Claude/Codex 写**:
1. 跑 `scripts/probe-tdx-controls.py` (scaffold 已存在) -- 用 `pywinauto.Application(backend="uia").connect(path="TdxW.exe")` 接管
2. dump 控件树到 `.tmp-port/tdx-control-tree-<panel>-<ts>.txt` (gitignored)
3. 写 `TDXAccountReader._extract_positions/_extract_entrusts/_extract_trades`, 输出 schema 对齐 `account_reader.py` 顶部的 `PositionRecord/EntrustRecord/TradeRecord`
4. fixture 测试: `backend/tests/trade/test_tdx_reader_fixtures.py` 用录制的 control text 做 mock

**验收**:
- `scripts/probe-tdx-controls.py --panel positions` 不抛错, 输出 >=1 行真数据
- `pytest backend/tests/trade/test_tdx_reader_fixtures.py -v` 全绿
- `TDXAccountReader.is_available()` 返 True

**雷区**:
- TDX 控件按版本变, 必须落 fixture 不硬编码路径
- panel 切换用 `pywinauto.timings.wait_until` 不要 sleep
- 数据是字符串 ("10,000" -> int, "+5.32%" -> float)
- pywinauto-recorder + inspect.exe 同时跑 TdxW 会卡死, dump 完关掉
- 不要写 OCR fallback -- 改版即崩

### B4. QMT / EasyXT / xtquant SDK probe (XAR-414)

**[B | L3 sensor setup | manual one-shot]** -- QMT 是 L3 plant 备路 sensor + actuator. 同 B3, Curry 提供登录态, agent probe SDK = sensor 字段对齐.

**为什么 B**: xtquant 是 QMT 安装目录局部 import, 券商账户登录态对 FSC 不可观.

**Curry 现场步骤**:
1. 装 QMT 客户端 + mini-login 一次 (用券商账户)
2. 落:
   ```
   KATANA_QMT_ACCOUNT_ID=<...>
   KATANA_QMT_USERDATA_PATH=<QMT 安装目录>/userdata_mini/
   ```

**Claude/Codex 写**:
1. `scripts/probe-qmt-fields.py`:
   - `from xtquant import xttrader`
   - 调 `query_stock_positions / query_stock_orders / query_stock_trades / query_stock_asset`
   - dump 字段名 + 类型 + 样例值到 `.tmp-port/qmt-probe-<endpoint>.json`
2. EasyXT 同上 (`easytrader.use("qmt").connect()`), 拆独立脚本 (DLL 冲突)
3. 跟 TDX (B3) 输出对照, 出 `docs/broker-sdk-matrix.md`, 4 列: field / TDX / QMT / EasyXT / 推荐主源, 至少 30 字段

**验收**: 4 个 probe json 落地; matrix doc 顶部写明 "主源 = X 因为 [evidence], 二路验证 = Y".

**雷区**:
- QMT mini-login 每天 manual (token 缓存不保证 24h)
- xtquant 无 pypi 包, 是 QMT 局部 import, 必须本机
- EasyXT + xtquant 同进程 import DLL 冲突, 拆两脚本
- QMT 字段驼峰 (volume, available, frozen_volume) vs EasyXT 下划线 (volume, available, frozen)

### B5. 5080 hot standby 硬件 + state replication (XAR-483)

**[B | infra layer | manual one-shot setup + continuous rsync]** -- 物理硬件配置 = Curry 一次性, 之后 state replication 是 L3 continuous (rsync 5s). takeover trigger = event (5090 死) + manual ack.

**为什么 B**: 5080 上电 + netbird 入网 + BIOS 是物理动作, FSC 无 actuator 进物理层.

**Curry 现场步骤**:
1. 5080 上电 + BIOS Wake-on-LAN 启
2. `netbird up --allow-server-ssh=false` 加入 mesh, 跟 5090 同 subnet
3. SSH 走 netbird tunnel, 5090 rotate `~/.ssh/known_hosts` 5080 entry
4. UPS (你判断)

**Claude/Codex 写**:
1. 协议设计 `docs/hot-standby-protocol.md`:
   - 主 5090 每 5s rsync `data/orders.db` + `data/kill_switch_state.db` 到 5080:`data/standby/` (走 netbird ssh)
   - 副 5080 watchdog 进程 ping 5090 主进程 HTTP `/health`
   - 5090 心跳超 60s 死 -> 5080 watchdog 发飞书 alert "请确认切换", 人 ack 才接管 (manual 避免脑裂)
2. SQLite WAL mode + 每 5s `BEGIN IMMEDIATE; ...; COMMIT`
3. nssm/WinSW 服务定义 (process supervision, 见 C4)
4. takeover 脚本 `scripts/take-over.ps1` (5080 上跑, 人 SSH 进去手动触发)
5. 演练: 拔 5090 网线 -> 60s 内 5080 收到 alert -> 模拟 ack -> 5min 内接管 -> 跑 1 笔 paper order 验状态一致

**验收**: rsync 一次 < 1s; 拔网线场景全流程通过; 切换后持仓状态一致.

**雷区**:
- SQLite over SMB/NFS 会 corrupt -- 必须本地盘 + rsync, 别共享
- WAL 文件 (`-wal`, `-shm`) 也要 rsync, 漏了等于没复制
- netbird udp 偶尔丢包, rsync 走 ssh 自带重传
- ack 切换不用钉钉点按钮, 用 SSH 进 5080 跑脚本 (审计 trail 清晰)

---

## C 桶 -- supervisory control (controller = Curry)

Curry 当 controller 本体, 设 set-point. Code 无法替代. **C 桶 trigger 必须显式声明** (manual / event-on-drift / clock-review), 不默认 clock.

### C1. 停牌/涨跌停数据源选型

**[C | L1 | manual one-shot + event-on-source-change]** -- 选 sensor 是 controller synthesis 决策. 一次定型, 数据源变化 (akshare 维护停 / 新增 vendor) 或半年 review 才重选.

**前置**: A1 完成, 看 `docs/suspension-source-comparison.md`.

**拍板**: 生产主源 / 备份二路 / 阻断延迟容忍度 (盘中 vs 收盘后).

**输出形式**: 你回 "用 akshare 主, baostock 二路, 盘中容忍 5min" 之类的一句话.

### C2. 风控阈值最终值

**[C | L1 | manual + event-on-drift + quarterly review]** -- set-point 选择. Trigger: ① 实盘启动前 manual, ② drift detector 报警 event, ③ 季度 review clock. 不是 daily.

**前置**: A7 完成, 看 `docs/risk-threshold-candidates.md`.

**拍板**:
- 日内 / 5d / 20d 回撤 kill switch 阈值 (% 数字)
- 单股 notional 上限 (绝对值或占组合 %)
- 行业暴露上限
- ST 是 warn 还是 reject

**输出形式**: 直接在 candidates doc 旁边写一份 `docs/risk-threshold-locked.md` 或者回一句话让 Claude 写.

### C3. Market impact + drift 模型选型

**[C | L1 | manual + event-on-fill-accumulation]** -- controller type selection. Trigger: ① 实盘启动前 manual, ② 实盘 fill 数据积累到 calibration window 时 event.

**前置**: A8 完成 + 一个独立的 drift detection 三指标对比 (KL/PSI/Wasserstein, 任务可后开).

**拍板**:
- impact 模型 (AC vs Kyle vs Sqrt)
- drift 指标 (KL/PSI/Wasserstein) + 触发阈值

### C4. Process supervision 选型

**[C | infra layer | manual one-shot]** -- infra controller type. 一次定型.

**前置**: B5 中段.

**拍板**: nssm vs WinSW vs Windows Service. Claude 给候选 + 维护成本对比, 你选.

---

## Cascade set-point 流向 + trigger 启动顺序

### Cascade (set-point 自上而下)

```
L0 战略 (Curry)               上钱 / 资金规模 / 风险预算
   |
   v set-point
L1 supervisory (Curry, C 桶)  C1 数据源选 / C2 阈值 / C3 模型选型 / C4 supervisor
   ^                          ^ evidence
   | sensor calibration       |
   |                          | sensitivity 报告
L2 plant identification (A)   A1 停牌对照 / A7 阈值候选 / A8 impact 三模型
   |                          
   | controller params        
   v                          
L3 实时控制 (A)              A2 状态机(observer) / A3 pretrade / A4 kill switch / A5 heartbeat / A6 alert
   ^                          ^ alert event 上行
   | sensor input             |
   |                          
plant 物理层 (B 桶 bridge)    B1 OKX key / B2 webhook / B3 TDX / B4 QMT / B5 5080
```

### Trigger 启动顺序 (按 trigger 类型, 不按 layer)

**Manual one-shot (Curry 一次性消耗)**:
- B1 OKX key 贴 .env (15min)
- B2 钉钉/飞书 webhook 申请 (15-30min)
- B3 TDX 登录 + 控件树标定 (30-60min, Curry 切 panel)
- B4 QMT mini-login + SDK probe (30-60min)
- B5 5080 上电 + netbird 入网 + UPS (1-2hr)

**Manual or event triggered (FSC 主动跑 / 你推一下就跑)**:
- A1 停牌源对照 (一次跑, 半年 review 重跑)
- A7 风控阈值 backtest (上钱前一次, drift 报警时重跑, 季度 review)
- A8 market impact 三模型 (上钱前一次, 实盘 fill 积累时重跑)

**Continuous (always-on, 配置完成即跑)**:
- A2 状态机 (持久化 observer)
- A3 pretrade (订单循环阻断)
- A4 kill switch (三路触发 always-on)
- A5 heartbeat (1s tick + 5s 检测)
- A6 alert router (event-driven 上行)
- B5 standby rsync (5s tick)

**Supervisory decision (Curry 拍板, event-driven)**:
- C1 数据源选 (A1 报告 ready 时)
- C2 阈值最终值 (A7 报告 ready 时)
- C3 impact + drift 选型 (A8 报告 ready 时)
- C4 supervisor 选型 (B5 中段时)

### 并行调度

**Now (互不阻塞, 立刻启)**:
- B1 + B2 (你 15min 解锁两条 actuator)
- A1 + A2 + A7 + A8 (FSC 4 路独立跑)

**B1 完成 -> A6 拿到 alert recipient (但 A6 也可后接, 不阻塞)**

**B2 完成 -> A6 拿到 webhook -> 启 A5/A4/A6 链**

**B3/B4 完成 -> 启 A2 wire 真 broker sensor -> 启 A3 pretrade**

**A1/A7/A8 报告 ready -> C1/C2/C3 拍板**

**C1/C2/C3 拍板 -> A3/A4 finalize 参数 + L3 全部 reload set-point**

**B5 完成 -> 5080 接管演练 -> C4 拍板 supervisor 工具**

---

## 最终验收门 (上钱前)

- [ ] A 桶 8 项全过自己的验收契约
- [ ] B 桶 5 项全部 Curry-in-loop 完成
- [ ] C 桶 4 项 Curry 拍板落 doc
- [ ] paper 1 周跑通: alert / heartbeat / kill switch / pretrade 全部 wired 且触发过
- [ ] 5080 hot standby 切换演练通过 1 次
- [ ] gitleaks 验证零 key 泄漏
- [ ] Curry 最终 ack "可以上钱"
