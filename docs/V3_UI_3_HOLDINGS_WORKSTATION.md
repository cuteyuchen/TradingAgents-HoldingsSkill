# V3-UI-3 Holdings Workstation

正式将 `/holdings` 迁移为 Quasar V3 持仓工作站，并严格拆开 **持仓快照事实 / 实时行情 / 策略判断 / 执行约束** 四层。

## Route Migration

- 正式路由：`path=/holdings`，`name=holdings`，`component=v3/holdings/V3HoldingsView.vue`，`meta.uiSystem=v3`
- `/upload` 继续 redirect 到 `/holdings?action=update`
- 不建立 `/v3/holdings` 平行正式入口
- Legacy `views/HoldingsView.vue` / Naive drawer 可保留，但正式路由不再加载
- V3 Holdings 不 import 依赖 Naive UI 的 `HoldingsUpdateDrawer.vue` / `HoldingsIdentityTable.vue`

## 三层 Authority

| 层 | 来源 | 不可覆盖 |
| --- | --- | --- |
| Position Authority | latest confirmed `PortfolioSnapshot`：qty / available_qty / cost / snapshot market_value / snapshot pnl / weight / cash / total_assets / snapshot_time | 实时 quote 不得覆盖 |
| Quote Authority | `POST /api/v3/market/instruments/quotes`：last / change / change_pct / prev_close / observed_at / data_basis / quality | quote 失败不得抹掉 snapshot position |
| Strategy Authority | dashboard/today + latest structured decision：holding_action / risk fields | 前端禁止自行产生 BUY/SELL/REDUCE/ADD |

主行情不再依赖旧 Fuyao contribution。

## Triple Timestamp

页面顶部独立显示：

- 持仓快照时间
- 行情时间（`observed_at` / batch `as_of`）
- 策略分析时间（decision_at / strategy_analysis_at）

禁止合并为一个「更新时间」。

## Portfolio Ownership

复用 `usePortfolioContext()` / `setSelectedPortfolio()`。

控制器记录：

- `snapshotOwnerId`
- `dashboardOwnerId`
- `quotesOwnerId` + `quoteGeneration`
- `sparklineGeneration`

A → B 切换时，在 B 数据完成前不展示 A 的 snapshot/assets/holdings/decision/quotes。

## Snapshot Loading

1. `loadPortfolios()`
2. resolve selected portfolio
3. `latest_snapshot_id` 存在则 `getSnapshot(id, signal)`
4. 无 snapshot：显示「还没有确认持仓快照」，不请求不存在资源
5. `identity_status != RESOLVED`：显示身份不完整；unresolved 行不请求 live quote、不打开 InstrumentDetail、不包装为可执行策略

## Batch Quotes

仅对 `resolution_status=RESOLVED` 且有 `canonical_code` 的 holdings 发起一次 `getInstrumentQuotes(codes, signal)`。

Join 优先级：

1. exact `canonical_code`
2. exact response instrument code
3. normalized short code，仅当前组合内唯一

歧义则不关联。missing 显示 `—`，不写 0。

## Polling

`MarketSessionService` 为 cadence authority。Session / quote / strategy poller 独立。

建议：

- MORNING/AFTERNOON/OPEN/CLOSE_AUCTION：quotes 5~8s，strategy 30~60s
- LUNCH_BREAK：quotes 30s
- CLOSED/NON_TRADING_DAY：quotes stop/low，strategy low
- analysis_in_progress：strategy 提高到 ~12s

同 session kind heartbeat 只重排 session 自己；仅 kind 真变化时重排依赖 cadence 的 pollers。

Visibility resume：

1. refresh session
2. refresh quotes
3. refresh strategy
4. reschedule

## Decision Snapshot Binding（Merge Blocker）

Backend additive read-model：

```python
# backend/app/operations/dashboard.py _decision_section latest payload
"portfolio_snapshot_id": latest.portfolio_snapshot_id,
```

绑定规则（fail closed）：

- 仅当 `latestDecision.portfolio_snapshot_id === currentSnapshot.id` 时，holding actions 才是「当前系统判断」
- `portfolio_snapshot_id = null` 与 mismatch 同等视为 unbound
- unbound：显示「策略基于旧或未绑定持仓快照 · 待重新分析」
- 未重新确认的行 judgment = `DATA_INSUFFICIENT`
- 旧或未绑定的 REDUCE/ADD/EXIT/HOLD 不得继续套用到新 snapshot
- decision missing：所有行 `DATA_INSUFFICIENT`，不得默认 HOLD
- explicit matching `NO_ACTION`：无 per-row action 的持仓显示 `HOLD`，secondary「组合级 NO_ACTION」
- `quality=BLOCKED` / conclusion BLOCKED：组合「策略暂不可执行」，无具体行 action 时 `RISK_BLOCKED`

## Holding Status 枚举（冻结）

```
HOLD | WATCH | CONDITIONAL_ADD | ADD | CONDITIONAL_REDUCE | REDUCE | EXIT | DATA_INSUFFICIENT | RISK_BLOCKED
```

确定性 mapping：

- hold / no_action → HOLD
- watch → WATCH
- conditional_add → CONDITIONAL_ADD
- add / increase → ADD
- conditional_reduce → CONDITIONAL_REDUCE
- reduce / trim → REDUCE
- sell / exit → EXIT
- blocked → RISK_BLOCKED
- missing / unknown → DATA_INSUFFICIENT

禁止根据涨跌幅、盈亏或 risk score 自行转换动作。保留 `rawAction` 供 debug/secondary。

## Action Source Priority

1. latest matching DecisionMemory `holding_actions`
2. dashboard portfolio holdings `holding_action`
3. valid matching portfolio-level NO_ACTION → HOLD
4. otherwise DATA_INSUFFICIENT

Decision snapshot mismatch 压过旧 action。

## T+1 / available_qty

表格同时展示持仓数量与可用数量。

- `available_qty` 直接来自 snapshot
- 禁止 missing available_qty → qty
- `qty > available_qty` 时显示「当前不可用 = qty - available_qty」，注明「由快照数量差计算」
- 不声称一定是今日买入冻结，除非 backend 提供 reason
- 执行约束不改写策略：EXIT + available=0 仍显示 EXIT +「执行约束：当前可用 0 / 不可执行」

## Dense Holdings Table

桌面列：

标的 / 趋势 / 现价 / 今日涨跌 / 成本 / 持仓 / 可用 / 行情估算市值 / 快照仓位 / 浮盈亏 / 系统判断 / 关键条件 / 数据状态

- A 股：涨=红（`market-up`），跌=绿（`market-down`）
- stale/degraded 有 badge
- 行情时间用 `observed_at`，不用 `fetched_at`
- 行情估算市值 = `last * qty`，标「行情估算」
- quote 不可用但 snapshot market_value 有值：显示 snapshot value 并标「快照」
- weight 默认用 snapshot weight

## P&L Semantics

- 优先 snapshot pnl ratio / amount，标「快照」
- live marked estimate 可选，标「按现价估算」
- 当日盈亏：`(last - prev_close) * qty` deterministic estimate，标「按行情估算」
- 覆盖不足主指标 `—`，secondary 显示覆盖率
- 禁止把这些值叫「累计收益 / 历史总收益」

## Sparkline

- `GET /api/v3/market/instruments/{code}/bars?interval=1d&limit=30`
- 仅 resolved code
- lazy visible rows，concurrency ≤ 4
- cache per canonical code + generation
- one-shot，无周期 poll
- SVG only；bars 失败只隐藏该行 sparkline，整表不 error
- Holdings 初始加载不 eager-load InstrumentDetail ECharts

## Unified InstrumentDetail Drawer

- resolved row 打开 `V3InstrumentDetailDrawer`（UI-1 复用）
- 页面仅一个 Drawer instance
- unresolved 行不打开
- Holdings 不重写 quote/kline/order book/capital flow

## Update Workflow

正式组件：

- `V3HoldingsUpdateDrawer`
- `useV3HoldingsUpdate`
- `V3HoldingsIdentityTable`
- `V3SecurityCandidateDialog`

能力保留：

- 上传截图 / 粘贴截图
- preview（replace/unmount 时 `URL.revokeObjectURL()`）
- OCR/vision parse polling（self-schedule ~1.8s，terminal 停止）
- manual entry / edit holdings / available_qty
- identity resolution / ambiguous candidates / rematch
- save parsed / confirm snapshot / validation / retry parse
- analysis job：mode/checkpoint/notify、job polling、cancel、retry、resume from query job

上传 poller 与 analysis job poller 独立。Drawer close/unmount 只停止前端 polling，不 cancel backend job。

## Identity Resolve Race

每行使用 `AbortController + sequence + captured input`：

- 新编辑会 abort 旧 in-flight resolve
- 允许 overlapping 请求，latest seq wins
- late old response 不能覆盖新输入
- `resolveHolding(..., signal)` 支持取消

## Preview Object URL

默认 **preserve draft on close**：

- 普通关闭不 revoke、不清空 `selectedFile` / `previewUrl`
- 仅在替换文件 / clear draft / unmount 时 revoke
- 避免「URL 已 revoke 但仍保留 draft」的不一致状态

## Confirm Snapshot

1. `saveParsed()`
2. `confirmUpload()`
3. 更新 portfolio latest snapshot
4. 清 quote generation
5. reload snapshot
6. re-batch quotes
7. refetch dashboard decision
8. 因 latest decision 往往仍基于旧 snapshot：立即进入 stale-strategy / DATA_INSUFFICIENT

## Failure Isolation

独立模块：snapshot / dashboard strategy / batch quotes / row bars / upload / identity resolve。

- quote 失败：snapshot table 正常，quote cells = `—`
- quote refresh 失败且有 last-success：保留旧 quote + stale
- strategy 失败：judgment DATA_INSUFFICIENT
- snapshot 首次失败：显示「持仓快照加载失败 + retry」

## Data Quality

行可表达：VALID / DEGRADED / STALE / MISSING / IDENTITY_INCOMPLETE / STRATEGY_STALE。

风险/status 使用独立 token，不与 A 股涨跌色混用。

## Mobile

- 375×720：document 无横向 overflow
- summary 可读
- dense table 自身横向滚动
- update drawer / instrument drawer 可用

## Backend Changes

唯一必须 change：

- `dashboard/today` `decisions.latest.portfolio_snapshot_id` additive read-model 字段
- 无 DB migration
- 无策略 / Agent / Hard Cap / Provider 修改

Backend test：`test_daily_operations.py` 断言 latest `portfolio_snapshot_id` 与真实 `DecisionMemory` 一致；decision missing 时 `latest=null`。

## Acceptance

新增 `frontend/e2e/v3-holdings.spec.ts`，全 deterministic `page.route()`。

覆盖：

- route migration / V3 shell / no Naive Holdings DOM
- `/upload` alias
- normal workstation + triple timestamps
- A 股颜色
- available_qty / T+1 / EXIT + available 0
- decision snapshot match / mismatch
- explicit NO_ACTION → HOLD
- decision missing → DATA_INSUFFICIENT
- BLOCKED → 策略暂不可执行 / RISK_BLOCKED
- batch quotes 单次请求
- partial quote / stale quote
- portfolio switch ownership / B 500 不回退 A
- sparkline fail isolation
- unified drawer resolved only
- mobile 375

Legacy tests 已迁移 selectors 到 V3 semantic/testid，保持原业务意图。

## Bundle

Holdings 初始 route 使用 async `V3InstrumentDetailDrawer`，不 eager-load ECharts。Row sparkline 使用 SVG。

## Known Limitations

- 真实 broker intraday P&L 非 authoritative
- 历史 lifetime P&L 非 authoritative
- Analysis / History / Settings 仍为 Legacy
- 盘中长时间人工稳定性未完整验证
- 人工完整视觉/移动端业务流未完整验证
- 不执行真实交易
- authoritative target exposure range 尚无 structured contract，前端不推算，显示 `—`
