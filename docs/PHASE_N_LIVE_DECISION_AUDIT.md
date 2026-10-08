# Phase N Live Decision Audit

## Audit Scope

本审计基于 Phase M 基线 `208936d8ec973e82873054d3c39779aa16f9015e`。本分支开发时远端 PR #5 仍为 OPEN，`main` 尚未包含该基线，因此 Phase N 从该 SHA 直接创建。

## Production Decision Authority

当前生产分析链由 `AnalysisJob` 驱动 `run_analysis_job()`。模型输出经过结果归一化和 `apply_portfolio_decision_gate()` 后，写入 `AnalysisRun.structured_result_json.result`。该对象中的 `final_rating`、`holdings`、`today_actions`、`candidates` 与 `decision_gate` 是当前最终组合决策的事实来源；Candidate Engine 的 `ACTION` 只能作为候选输入，不能绕过 Portfolio Gate。

`AnalysisRun` 的 `final_rating`、`summary`、`cash_target` 与 `confidence` 是便于列表展示的投影，不替代结构化最终结果。`PortfolioManagerFinal` 仅属于旧的 Archive/Run 上传模型，不是 V2 生产分析的最终持久化对象。

## Existing Durable IDs

| 事实 | 当前对象 / ID | 结论 |
| --- | --- | --- |
| 分析请求 | `AnalysisJob.id` | 有 durable job ID，并带 `checkpoint`、`trigger_type`、`context_json` 和唯一 `idempotency_key` |
| 最终生产报告 | `AnalysisRun.id` | 每个成功 job 一条，绑定 snapshot 和 model profile |
| Candidate 结果 | `CandidateRun.id` / `CandidateScore.id` | 有 calculation key、市场/报价快照和参数 lineage |
| 组合快照 | `PortfolioSnapshot.id` / `HoldingItem.id` | confirmed snapshot 是生产持仓唯一事实来源 |
| 旧 Decision Memory | `DecisionMemory.id` | 已不可变，但属于派生记忆，缺少 Phase N 的最终时间、观察哈希和执行层 |
| Trigger | `TriggerEvent.id` | 通过 `AnalysisJob.context_json` 关联触发原因，可保留多事件及 debounce/cooldown 证据 |

## Checkpoint and Trigger Association

固定检查点由 Phase H `DailyOperationalCheckpoint` 和 `AnalysisJob.checkpoint` 关联，当前固定检查点为 `09:35`、`10:30`、`13:05`、`14:30`、`15:10`。Trigger 由 `TriggerEvent` 关联到 `AnalysisJob.context_json.trigger_event_ids`，不得把 trigger 伪装成固定 checkpoint。

Phase N 的 observation 将同时保存 `decision_kind`、`decision_checkpoint`、`trigger_event_id`、触发优先级、原因和 debounce/cooldown lineage。

## Current Timestamp and Quote Gaps

当前 `AnalysisJob.started_at` 和 `finished_at` 可以提供分析开始/完成时间；`AnalysisRun.created_at` 是报告插入时间，并不明确表示最终决策完成时间。因此 Phase N 使用成功 job 的 `finished_at` 作为 `decision_finalized_at`，并把该时间写入不可变 observation。

当前 `MarketSnapshot` 只持久化快照元数据，报价列表在运行时 payload 中；`NormalizedQuote` 有 provider source timestamp，但没有可检索的逐次持久化表。因而旧链路不能保证找到决策完成之后的第一个合法报价。Phase N 新增 `LiveQuoteObservation`，仅保存与当前持仓、最终决策和 pending shadow intent 相关的报价事实。

若报价没有可证明的精确时间戳，Phase N 不把它用于 Paper Fill。`quote_captured_at` 必须严格大于 `decision_finalized_at`；15:10 决策不得使用当天 close，只能等待下一个交易日的后续报价。

## Reusable Production Data

- confirmed `PortfolioSnapshot` / `HoldingItem`：Shadow Account 初始化来源，但只复制，不建立共享状态。
- `AnalysisRun`、`AnalysisJob`、`CandidateRun`、`CandidateScore`：Decision Observation lineage 和 Candidate Veto 研究来源。
- `MarketScoreSnapshot`、`MarketMetricSnapshot`、`MarketSnapshot`：市场 regime、score 和 source lineage。
- `SecurityMaster`：security type、ETF category、lot size、active/suspended 状态。
- `TradingCalendar` / `TradingCalendarService`：执行过期、T+1 和 1/5/10/20/60D outcome 的交易日事实。
- Phase E `transaction_cost_estimate()`：Paper execution 直接复用 commission / minimum commission / sell tax 规则。
- 真实 `TradeLedgerEntry`：只在存在 code、side、quantity、executed_at 的情况下做事实匹配，不用 holdings diff 猜交易。

## New Phase N Facts

以下事实不与真实交易表混用：

- `LiveDecisionObservation`：不可变生产最终决策观察，带 calculation key 和 SHA-256 observation hash。
- `LiveQuoteObservation`：有限范围的逐次 quote observation，支持严格未来报价证明。
- `ShadowAccount` / `ShadowPosition` / `ShadowLedgerEntry`：独立影子账户、生成代际、可 rebuild 的 append-only ledger。
- `ShadowOrderIntent` / `ShadowFill`：意图与成交分离，Paper Fill 永不调用 broker 或 LLM。
- `ShadowDailySnapshot`：物化的每日净值事实，可由 Shadow Ledger 和收盘 mark 重建。
- `LiveDecisionOutcome`：与 Fill 分离的 1/5/10/20/60 trading-day 结果，NO_ACTION 也有样本。
- `DecisionActualAlignment`：仅记录真实 Trade Ledger 的确定性匹配结果。

## Duplicate and Restart Risks

已有 checkpoint/job idempotency 会防止大部分 scheduler 重复分析，但旧链路没有独立的 live observation 或 shadow intent key。Phase N 以 `(user, portfolio, decision kind/checkpoint or trigger, source analysis job, final decision version)` 生成唯一 observation key；以 `(shadow account, generation, observation, action index, code, side)` 生成唯一 intent key；以 `(intent, quote observation)` 生成唯一 fill execution key。服务重启只恢复 PENDING/PARTIAL intent，不重新生成 observation，也不使用历史报价回填。

## Real Portfolio Comparison

真实 `TradeLedgerEntry` 是用户事实账本，可用于 alignment；它不代表完整券商同步，也不能从 holdings 差异推断成交时间。Shadow ledger 不写入该表，不修改真实 cash、holdings、PortfolioSnapshot 或 broker identifiers。Shadow generation rebase 只建立新代际并保留旧代际历史。

## Findings and Boundary

Phase N 不改变 Phase C/F/M 的评分、阈值或 Runtime/Decision Contract `2.4.0`。它只在成功生产决策之后捕获 observation，并在后续真实持久化 quote 上执行确定性 Paper 规则。Shadow 失败只标记 validation 子系统降级，不阻断生产分析；Paper PnL 始终是样本期模拟结果，不是收益保证。
