# Phase L Acceptance

基线：`ac7500e6e3388827c949f411716eaa38cf5cf760`

Phase L 建立 Point-in-Time 历史数据基础层，不新增投资算法、不做自动学习、不自动交易。

## 0. Phase L.1 PIT Integrity Final Seal

Phase L.1 不新增 migration、不新增表、不开始 Phase M，只封堵会直接破坏 PIT 正确性的
5 个问题：

1. `source_available_at` 缺失时 fail-close，不再视为 historical visible。
2. 统一 `shanghai_end_of_day_to_utc_naive()`，A 股日末按 Asia/Shanghai 转 UTC-naive，
   不再直接使用本地 23:59:59 造成跨交易日 8 小时 look-ahead。
3. Coverage 改为 security × date 级统计；单日单行不能代表 FULL。
4. `DETERMINISTIC_RECOMPUTE` 在真正 recompute engine 落地前继续 fail-close；
   PIT 输入就绪只叫 `PIT_INPUTS_READY`，不叫“已确定性重算”。
5. 同一 `source_ref` 的 PIT 修订追加 revision，保留旧 row，不原地覆盖历史事实。

附带修复：`SecurityTradingStatusDaily` / `SecurityClassificationDaily` 改为
`trade_date == as_of` 精确匹配；当日缺失不会 forward-fill 前一天状态。

## 0.1 Phase L Final Integrity Patch

Phase L Final Integrity Patch 不新增 migration、不新增表，只收口三个仍然存在的
PIT correctness blocker：

1. `resolve_historical_holdings()` 只读取 as-of 前最近一份 confirmed
   PortfolioSnapshot，不再把“历史上曾持有过的股票并集”当成当时持仓。
2. Daily coverage 改用 `known ∩ expected` 计算 security × date 覆盖；错证券不能
   凑成 FULL。expected universe 与正式 PIT resolver 使用相同的
   `source_available_at` visibility cutoff。
3. Fundamental revision 统一使用 `visible_at = max(published_at,
   source_available_at)`；restatement 缺少 `source_available_at` 时 fail-close，
  未来修订不能倒灌旧 as-of。

## 1. Migration / Schema

新增 migration：`20260828_0019_historical_data_foundation`（`0018 -> head`）。

新增表：

- `security_lifecycle_events`
- `security_trading_status_daily`
- `security_classification_daily`
- `security_valuation_daily`
- `fundamental_reports`
- `etf_metadata_history`
- `price_basis_metadata`
- `historical_data_sync_runs`

不修改 migration `0014`～`0018`。

## 2. Data Audit

详见 `docs/PHASE_L_DATA_AVAILABILITY_AUDIT.md`。

Provider 现状：

- Eastmoney：当前证券列表、SSE K 线推断日历、日 K QFQ；不提供 listing/delisting/ST/suspension/历史估值历史。
- Tencent：仅当前 quote。
- 仓库未接入 mootdx，不假装支持。

Phase L 不创建虚假历史 provider。lifecycle / trading status / ST / valuation / fundamentals / ETF metadata / price basis 的同步在 V1 中由 operator/import 显式写入；无 provider adapter 时 `HistoricalDataSyncRun` 标 `UNSUPPORTED`，Backtest 不联网。

## 3. Historical Semantics

- `effective_date` 是事实作用于市场的时间。
- `source_available_at` 是数据源角度可用时间。
- `captured_at` 是系统抓取时间，`ingested_at` 是写入时间。
- `source_available_at = NULL` 的 PIT-required 事实不可见、不可导入、不计入 known
  coverage；fundamentals 由 `published_at` 承担同一职责。
- A 股交易日截止时间统一为上海 23:59:59 对应的 UTC-naive 值
  （`2026-01-02 23:59:59 Asia/Shanghai == 2026-01-02 15:59:59 UTC`）。
- `published_at <= as_of` 是基本面/估值可见性硬门槛；`published_at` 缺失 → `MISSING_PUBLICATION_TIME`，不得视为 0 或 FULL。
- restatement/revision 保留历史版本，as-of 查询只看到当时已发布版本。
- Fundamental `visible_at` 是版本真正可用的 PIT 时间；同 `published_at` 的修订必须
  提供更晚的 `source_available_at`，否则 resolver/coverage 都不可见。
- 当前 SecurityMaster 状态不参与历史 lifecycle truth；历史缺失为 `UNKNOWN`/`DATA_GAP`。
- ETF 历史 category 不做 current-backfill。
- Price basis 记录 provider basis/version，RAW/QFQ mismatch 继续 fail-close。
- `SecurityTradingStatusDaily` / `SecurityClassificationDaily` 只读取 `trade_date == as_of`
  的当日事实；当日无 row 即为 `UNKNOWN`，不继承昨日状态。
- 历史持仓只来自 as-of 前最近一份 confirmed snapshot；更早 snapshot 中已卖出的
  股票不得继续排除 Candidate Universe。

## 4. PIT Universe Resolver

`backend/app/history/universe.py`：

- `resolve_security_state(code, as_of)`
- `resolve_special_treatment(code, trade_date)`
- `resolve_valuation(code, trade_date)`
- `resolve_fundamental(code, as_of)`
- `resolve_etf_metadata(code, as_of)`
- `resolve_historical_holdings(portfolio_id, as_of)`
- `resolve_equity_universe(as_of, purpose)`

Universe 规则：

- MARKET_SCORE：SSE/SZSE 普通 A，exclude ETF/BSE/ST/停牌/退市/上市 <20 交易日。
- CANDIDATE_STOCK：SSE/SZSE 普通 A，exclude BSE/ST/停牌/退市/历史持仓/上市 <60 交易日。
- CANDIDATE_ETF：仅 active 交易所 ETF，exclude held/停牌/退市。
- 上市年龄按 TradingCalendar 交易日计算。
- 历史 current holdings 只来自当时 PortfolioSnapshot。

返回 `eligible_codes`、`excluded_counts`、`exclusions`、`known_count`、`unknown_count`、`coverage`、`status`、`universe_version=pit-universe-v1`、source lineage。

## 5. Coverage / Sync

- `historical_data_coverage(db, start_date, end_date, data_type)` 对 daily 表使用
  security × date 覆盖率；分母来自 historical lifecycle 重建的预期 Universe
  （trading_status 为全部证券、ST/valuation/fundamentals 为股票 Universe、
  price_basis 为已有 DailyBarCache 证券集合）。known 与 expected 必须按证券集合
  交集计算；5000 只证券只有 1 行状态、或 expected={A} 而只有 B 的 row 时都不能
  FULL。
- event 表不因行存在就声称 FULL；lifecycle 持续保持 PARTIAL/LEAKAGE_BLOCKED，
  这是“不知道事件缺失”的保守语义。
- expected Universe 的 lifecycle 事件必须同时满足 `effective_date` 与当日
  `source_available_at` cutoff；未来才可见的 DELISTED 不能在可见前移出分母。
- 事件表在区间前生效的事实仍可在区间内使用，`effective_date <= end_date` 即可计数。
- `python -m app.history.cli sync ...` / `POST /api/v3/history/sync` 提供幂等导入：同一 `source_ref` 重复导入不翻倍，修订新增 `source_ref` 保留历史版本。
- 非 fundamental 的 PIT-required 导入缺少 `source_available_at` 时直接拒绝；
  同一 natural key 的内容修订追加新 revision row，旧 as-of 继续看到旧版本。
- `HistoricalDataSyncRun` 使用 generation CAS reclaim；旧 generation 不能写新 generation。应用启动时会 reclaim 过期 RUNNING，startup recovery report 统计 `stale_history_syncs`。
- 无 provider 历史能力时标 `UNSUPPORTED`；dry-run 可预览计数。
- 大 backfill 前检查 Phase K disk health；`DISK_CRITICAL` 时 `run_history_sync()` 直接 `DISK_CRITICAL_HISTORY_BACKFILL_BLOCKED`。

## 6. Phase I Integration

- `build_replay_availability_manifest()` 在历史表存在时输出 `historical_security_state`、`historical_trading_status`、`historical_st_state`、`historical_valuation`、`fundamental_publication`、`etf_metadata`、`price_basis`，并更新 `point_in_time_universe` / `factor_point_in_time` / `survivorship` / `known_limitations`。
- `pit_recompute_gate()` 返回 `DATA_GAP` / `LEAKAGE_BLOCKED` / `PARTIAL` /
  `PIT_INPUTS_READY`；lifecycle PARTIAL 会进入 `partial_inputs`，不能仅因 daily
  表 FULL 就宣称 PIT 完整。
- `DETERMINISTIC_RECOMPUTE` 在 Phase L.1 中继续 fail-close：gate 非
  `DATA_GAP`/`LEAKAGE_BLOCKED` 时固定抛出
  `DETERMINISTIC_RECOMPUTE_ENGINE_NOT_IMPLEMENTED`；Phase L 只交付
  `PIT_INPUTS_READY`，不会把 persisted CandidateRun/MarketScore 冒充为确定性重算。
- Backtest 仍不联网；历史数据准备与回测分离。

## 7. Frontend

`/system` 页面新增 Historical Data 区块：data type、status、coverage、earliest/latest、last sync，并提供 Run Sync（data type/date/provider）与 sync runs 状态列表（QUEUED/RUNNING/COMPLETED/FAILED/UNSUPPORTED、progress、inserted/updated/skipped）。

## 8. Tests

- `backend/tests/test_history_pit.py`：lifecycle PIT、no current backfill、unknown ST、suspension、listing age（交易日）、delist survivorship、fundamental publication、missing publication、restatement、valuation PIT、ETF metadata、price basis、混合 universe、历史持仓、coverage 0.90、half-range PARTIAL、sync idempotency/revision、sync unsupported、generation CAS、startup recovery stale sync 统计、DETERMINISTIC_RECOMPUTE fail-close/engine-not-implemented、bulk query 常量 SQL 数。
- Phase L.1 专项：`source_available_at=NULL` fail-close、上海次日 01:00 在前一日不可见、
  5000 只证券单行状态 != FULL、全市场 fundamental coverage 按股票 Universe 统计、
  lifecycle PARTIAL 阻止 PIT gate 达 FULL、same `source_ref` 修订保留旧 row、
  当日 trading status 缺失返回 UNKNOWN。
- Final Integrity Patch 专项：快照 1 持有 A、快照 2 已卖 A 持 B，as-of 快照 2 只返回
  {B}；expected={A} 只有 B row 时 coverage 非 FULL；DELISTED effective=1/10、
  available=1/20 时 1/15 denominator 仍含该证券；两版 fundamental 同为 3/20
  published_at 时，4/1 只看到旧版、5/2 才看到新版。
- `backend/tests/test_history_api.py`：History API surface。
- 既有 Research / System / Backup / Migration 测试同步到 head `20260828_0019`。

## 9. Known Limitations

- Flow/Industry 历史没有可靠来源，Candidate 全市场 full-equivalence 仍为 `PARTIAL_PIT_RECOMPUTE` / `DIAGNOSTIC_ONLY`。
- Full ETF constituent history 未实现（`UNSUPPORTED`）。
- 历史 sync 当前为显式 operator 动作，无自动日级增量回填。
- Historical MarketScore 的重算使用历史参数快照或 `LEGACY_PRE_GOVERNANCE`，本阶段只建立输入基础。
- Deterministic Recompute Engine 尚未实现；`DETERMINISTIC_RECOMPUTE` 仍 fail-close，
  待 Phase M 真正接入 Market/Candidate recompute 后再开放。

## 10. 未实现

Auto Factor Learning、Auto Threshold Learning、RL / AutoML、Strategy Optimization、Auto Apply、Auto Trading、Broker Execution、Full Level2/Tick History、Full News NLP Replay、Full ETF constituent history、Full Industry/Flow history、Distributed Data Warehouse、PostgreSQL migration。
