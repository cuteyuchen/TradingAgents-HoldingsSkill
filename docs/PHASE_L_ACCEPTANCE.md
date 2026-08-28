# Phase L Acceptance

基线：`ac7500e6e3388827c949f411716eaa38cf5cf760`

Phase L 建立 Point-in-Time 历史数据基础层，不新增投资算法、不做自动学习、不自动交易。

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
- `published_at <= as_of` 是基本面/估值可见性硬门槛；`published_at` 缺失 → `MISSING_PUBLICATION_TIME`，不得视为 0 或 FULL。
- restatement/revision 保留历史版本，as-of 查询只看到当时已发布版本。
- 当前 SecurityMaster 状态不参与历史 lifecycle truth；历史缺失为 `UNKNOWN`/`DATA_GAP`。
- ETF 历史 category 不做 current-backfill。
- Price basis 记录 provider basis/version，RAW/QFQ mismatch 继续 fail-close。

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

- `historical_data_coverage(db, start_date, end_date, data_type)` 对 daily 表使用交易日日期覆盖率；event 表不因行存在就声称 FULL。
- 事件表在区间前生效的事实仍可在区间内使用，`effective_date <= end_date` 即可计数。
- `python -m app.history.cli sync ...` / `POST /api/v3/history/sync` 提供幂等导入：同一 `source_ref` 重复导入不翻倍，修订新增 `source_ref` 保留历史版本。
- `HistoricalDataSyncRun` 使用 generation CAS reclaim；旧 generation 不能写新 generation。应用启动时会 reclaim 过期 RUNNING，startup recovery report 统计 `stale_history_syncs`。
- 无 provider 历史能力时标 `UNSUPPORTED`；dry-run 可预览计数。
- 大 backfill 前检查 Phase K disk health；`DISK_CRITICAL` 时 `run_history_sync()` 直接 `DISK_CRITICAL_HISTORY_BACKFILL_BLOCKED`。

## 6. Phase I Integration

- `build_replay_availability_manifest()` 在历史表存在时输出 `historical_security_state`、`historical_trading_status`、`historical_st_state`、`historical_valuation`、`fundamental_publication`、`etf_metadata`、`price_basis`，并更新 `point_in_time_universe` / `factor_point_in_time` / `survivorship` / `known_limitations`。
- `DETERMINISTIC_RECOMPUTE` 通过 `pit_recompute_gate()`：required PIT inputs 未 FULL 时 fail-close（`DATA_GAP` / `LEAKAGE_BLOCKED`），完整输入才可执行。
- Backtest 仍不联网；历史数据准备与回测分离。

## 7. Frontend

`/system` 页面新增 Historical Data 区块：data type、status、coverage、earliest/latest、last sync，并提供 Run Sync（data type/date/provider）与 sync runs 状态列表（QUEUED/RUNNING/COMPLETED/FAILED/UNSUPPORTED、progress、inserted/updated/skipped）。

## 8. Tests

- `backend/tests/test_history_pit.py`：lifecycle PIT、no current backfill、unknown ST、suspension、listing age（交易日）、delist survivorship、fundamental publication、missing publication、restatement、valuation PIT、ETF metadata、price basis、混合 universe、历史持仓、coverage 0.90、half-range PARTIAL、sync idempotency/revision、sync unsupported、generation CAS、startup recovery stale sync 统计、DETERMINISTIC_RECOMPUTE fail-close/unlock、bulk query 常量 SQL 数。
- `backend/tests/test_history_api.py`：History API surface。
- 既有 Research / System / Backup / Migration 测试同步到 head `20260828_0019`。

## 9. Known Limitations

- Flow/Industry 历史没有可靠来源，Candidate 全市场 full-equivalence 仍为 `PARTIAL_PIT_RECOMPUTE` / `DIAGNOSTIC_ONLY`。
- Full ETF constituent history 未实现（`UNSUPPORTED`）。
- 历史 sync 当前为显式 operator 动作，无自动日级增量回填。
- Historical MarketScore 的重算使用历史参数快照或 `LEGACY_PRE_GOVERNANCE`，本阶段只建立输入基础。

## 10. 未实现

Auto Factor Learning、Auto Threshold Learning、RL / AutoML、Strategy Optimization、Auto Apply、Auto Trading、Broker Execution、Full Level2/Tick History、Full News NLP Replay、Full ETF constituent history、Full Industry/Flow history、Distributed Data Warehouse、PostgreSQL migration。
