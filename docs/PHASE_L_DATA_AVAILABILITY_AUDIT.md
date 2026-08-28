# Phase L Data Availability Audit

基线：`ac7500e6e3388827c949f411716eaa38cf5cf760`

本文档在写 Schema 之前完成，依据真实代码审计，不假设 Provider 能力。结论是 Phase L 只建立可验证的 PIT 基础层，不虚构历史数据源。

## 0. Phase L.1 PIT 语义更新

Phase L.1 对 PIT 规则做了三项收紧：

- `source_available_at` 缺失 = 不可见。非 fundamental 的 PIT-required 导入缺少该字段
  时直接拒绝；resolver 对缺失事实返回 `UNKNOWN` / `DATA_GAP`，绝不按 visible 处理。
- A 股日末统一为 `shanghai_end_of_day_to_utc_naive(day)`。SQLAlchemy DateTime 是
  UTC-naive，因此上海日末必须先转 UTC，再与 `source_available_at` 比较。
- Coverage 的 denominator 从“日期有没有一行”改为“该日期应该覆盖的证券状态”。
  lifecycle event 本身无法证明 absence，因此只能 PARTIAL；daily 表按
  security × date 统计，单行无法让 5000 只证券的交易日变成 FULL。

### Final Integrity Patch 补充

- 历史持仓 = as-of 前最近 confirmed snapshot 的 holdings，不是历史 union。
- coverage known 必须与 expected 按证券集合取交集；unexpected rows 单独统计，
  错证券不能把缺失证券的日期凑成 FULL。
- expected Universe 与 PIT resolver 共用 `source_available_at` cutoff；晚于可见日的
  lifecycle 事件不会提前改分母。
- Fundamental 使用 `visible_at = max(published_at, source_available_at)`；
  restatement 无 `source_available_at` 时不可见并 fail-close。

## 1. 审计范围

实际读取：

- `backend/app/market_models.py`
- `backend/app/market_engine_models.py`
- `backend/app/market_runtime_models.py`
- `backend/app/market/providers/*`
- `backend/app/services/market_data.py`
- `backend/app/services/market_identity_sync.py`
- `backend/app/services/market_snapshot_service.py`
- `backend/app/candidates/*`
- `backend/app/research/*`
- `backend/app/portfolio/*`
- `backend/app/memory/*`

## 2. 逐类数据结论

### SecurityMaster / Security Lifecycle

| 维度 | 结论 |
| --- | --- |
| 当前是否持久化历史 | 否，只有当前状态 |
| 历史保存多久 | 不适用 |
| latest-only | 是 |
| trade_date | 否 |
| effective_at | 否 |
| source_available_at | 否，只有 `source_updated_at` |
| captured_at | 否 |
| ingested_at | 否 |
| 区分生效/抓取时间 | 否 |
| as-of 查询 | 否 |
| survivorship bias | 有，今天已退市标的不会进入当前列表 |
| current-master backfill | 存在风险，历史计算不得直接读 `SecurityMaster.status/is_st/is_suspended` |
| 最早日期 | 取决于当前 Provider 列表，无法保证历史 |
| 来源 | Eastmoney 当前全市场列表 |
| 是否允许网络补历史 | 当前接口不提供 listing/delisting 历史 |
| 缺失时当前代码 | Research Manifest 标记 `LEAKAGE_BLOCKED` |

结论：`SecurityMaster` 只能作为当前身份种子，不能作为历史生命周期真相。

### TradingCalendar

| 维度 | 结论 |
| --- | --- |
| 持久化历史 | 是，`TradingCalendar` 按交易日存储 |
| latest-only | 否 |
| trade_date | 是 |
| effective_at | 否 |
| source_available_at | 否，只有 `updated_at` |
| as-of | 按日期查询可用 |
| 最早日期 | 官方离线日历 2025 起；Eastmoney SSE K-line 可推断更早 SSE 交易日 |
| 来源 | `sse_official_schedule` / `eastmoney_sse_calendar` |
| 网络补历史 | 允许且已有显式 sync；日历本身不是投资事实，可用 |

结论：日历可以作为 listing age 的交易日基础，但日历行只提供是否开市，不证明证券状态。

### DailyBarCache

| 维度 | 结论 |
| --- | --- |
| 持久化历史 | 是，按 market/code/trade_date/adjustment 去重 |
| latest-only | 否 |
| trade_date | 是 |
| effective_at | 否 |
| available_at | 有，但语义由写入方决定 |
| captured_at | 否 |
| fetched_at | 是 |
| as-of | 可按 `available_at` 过滤，但未统一断言发布语义 |
| survivorship | 取决于同步时 universe，历史计算需自行绑定 universe |
| basis | `QFQ`，来自 Provider 声明，系统未自行计算 corporate action |
| 来源 | Eastmoney push2his K-line |
| 网络补历史 | 允许且已有 `sync_daily_bar_cache`，但 Backtest 禁止联网 |
| 缺失时当前代码 | `DIAGNOSTIC_ONLY` |

结论：DailyBar 可作 `PARTIAL_PIT_RECOMPUTE` 的价格输入，但必须配合 `price_basis_metadata` 说明 basis/version。

### Historical ST / Suspension / Delisting

当前无任何历史表。Eastmoney SecurityProvider 只返回当前列表，字段为 `f12/f14`，没有 ST/suspension/delisting 历史。Tencent 只返回当前 quote。

结论：Phase L 建立独立历史表，网络同步标记 `UNSUPPORTED`，只能通过 operator/import 写入已验证历史记录。名称中的 ST 只允许作为 `DERIVED` 辅助信号。

### Historical Valuation

当前无持久化历史估值。`SecurityMaster.raw_metadata_json` 和当前 quote 都不是历史估值。Provider 层无历史 PE/PB 接口封装。

结论：`security_valuation_daily` 为新增基础表；没有可靠数据源时不能 current-backfill。

### Fundamental Publication

当前无 `published_at` 的历史基本面表。Provider 只有公告搜索接口，未解析财报发布时间。

结论：`fundamental_reports` 必须带 `published_at`，缺失时不能视为 FULL PIT。

### ETF Historical Metadata

当前 `SecurityMaster` 只有 `etf_category` 当前值。没有历史 ETF category/benchmark 表。

结论：`etf_metadata_history` 只接受带 `effective_date` 的导入数据；今天 category 不得回填历史。

### Historical Industry / Flow

当前没有历史 industry mapping 或历史资金流表。`fetch_fund_flow` 只取当前最新一行。

结论：Phase L 不新增 industry/flow 表；Candidate 全等价重算保持 `PARTIAL_PIT_RECOMPUTE`，直至后续 Phase M 数据扩展。

## 3. 网络与 Provider 政策

- Backtest 永远不联网；`historical_replay_network_policy` 继续作为硬约束。
- 历史 sync 是独立、显式、手工触发动作，不写入 Backtest 路径。
- 当前只有 Eastmoney 日 K、SSE 日历、当前证券列表可网络获取。
- `mootdx` 不在仓库中，审计结论为“未接入”，不新增假 adapter。

## 4. PIT 规则基线

- `effective_date` 是事实作用于市场的时间。
- `source_available_at` 是数据源角度可用时间。
- `captured_at` 是系统抓取时间。
- `ingested_at` 是写入数据库时间。
- `source_available_at = NULL` 时事实不可见；PIT-required 导入直接拒绝该 row。
- `published_at <= as_of` 是基本面/估值可见性的硬门槛。
- Fundamental revision 的可见性使用 `visible_at = max(published_at,
  source_available_at)`；`published_at` 不变但 `source_available_at` 更晚的修订，
  在 `source_available_at` 之前不可见。
- restatement 缺少 `source_available_at` 时导入 fail-close，resolver 不返回该版本。
- 缺失历史状态必须是 `UNKNOWN`/`DATA_GAP`，不能当作 `NORMAL`/`0`。
- Trading/Classification 是当日状态：`trade_date == as_of`，当日缺失不 forward-fill。
- 同一 `source_ref` 的内容修订追加 revision row，旧 as-of 继续读取旧版本。
- A 股日末使用 Asia/Shanghai 转 UTC-naive；禁止用本地 23:59:59 直接比较 UTC 字段。

## 5. Phase L 后的能力

- 历史 lifecycle / trading status / ST / valuation / fundamental publication / ETF metadata / price basis 有独立表。
- `resolve_equity_universe` 只使用历史事实构建 as-of universe。
- `historical_data_coverage` 报告 security × date coverage，不把“日期有一行”写为 1.0。
- `pit_recompute_gate()` 在输入就绪时返回 `PIT_INPUTS_READY`；
  `DETERMINISTIC_RECOMPUTE` 在 recompute engine 实现前固定 fail-close。
- Candidate 全市场 full-equivalence 仍未完成，明确标 `PARTIAL_PIT_RECOMPUTE`。
