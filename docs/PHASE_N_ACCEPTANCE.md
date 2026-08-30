# Phase N Acceptance

验收日期：2026-08-29

## Phase N.1 Integrity Seal

验收日期：2026-08-30

当前结论：`PASS`

本次只收口 Shadow Execution 与 Outcome Integrity：

- `conditional_add` 继续记录 Observation/Outcome，但 V1 标记
  `CONDITIONAL_ACTION_EXECUTION_UNSUPPORTED`，不创建即时普通 BUY Intent。
- Shadow 可卖数量不足时记录 `BLOCKED_BY_SHADOW_SELLABLE_QTY`，不制造
  synthetic partial fill。
- Portfolio Outcome 使用对应 Shadow Account + Generation 的 reference/target
  equity；benchmark 独立计算；`execution_eligible` 由 Intent、future quote、
  fill/status 事实推导；summary 按 target/horizon 分桶。
- 非零持仓缺 mark 时 `SHADOW_MARK_DATA_GAP` fail-close；benchmark 缺失保持
  `None`，不按 0% 累计。
- as-of 估值只接受 `available_at` 非空且不晚于 cutoff 的 DailyBar、LiveQuote
  或 benchmark；`available_at=NULL` 不再被视为历史时点可见。决策日 reference
  equity 优先使用 cutoff 前的合法 LiveQuote，无法证明 mark 可见时保持
  `SHADOW_MARK_DATA_GAP`，不发布 Portfolio outcome。

## Release Identity

| 项目 | 值 |
| --- | --- |
| Baseline SHA | `208936d8ec973e82873054d3c39779aa16f9015e` |
| Branch | `codex/phase-n-live-shadow-validation` |
| Final SHA | `3da5849`（Phase N.1 Final As-Of Mark Seal；Phase N.1 Integrity Seal 实现为 `d566ffd`；Phase N 基线实现为 `29795647225f48075706c85075651db00c002616`） |
| PR | 待创建 |
| Runtime / Decision Contract | `2.4.0` / `2.4.0` |
| Shadow Execution Contract | `shadow-execution-v1` |
| Migration | `20260829_0020_live_shadow_validation.py`，down revision `20260828_0019` |

## Implemented Contract

| 项目 | 实现结论 |
| --- | --- |
| Production Final Decision | `AnalysisRun.structured_result_json["result"]` 经 Portfolio Decision Gate 后的最终动作 |
| Observation | `LiveDecisionObservation`，不可变，记录 checkpoint/trigger、snapshot、market、parameter、runtime、prompt、model lineage |
| Observation Key / Hash | calculation key 唯一；canonical JSON + SHA-256 observation hash |
| Duplicate / Restart | observation、intent、fill 分别使用唯一键；pending intent 可继续处理，重复 quote 不重复 fill |
| New Tables | `live_decision_observations`、`live_quote_observations`、`shadow_accounts`、`shadow_positions`、`shadow_order_intents`、`shadow_fills`、`shadow_ledger_entries`、`shadow_daily_snapshots`、`live_decision_outcomes`、`decision_actual_alignments` |
| Shadow Account | 显式创建；从 confirmed `PortfolioSnapshot` 复制一次现金和持仓 |
| Generation / Rebase | 每次 rebase 增加 generation；旧事实保留，不自动同步真实组合 |
| Real Portfolio Isolation | Shadow ledger/state 不写真实 snapshot、holding、broker cash 或 Trade Ledger |
| Intent | 只跟随最终 `ACTION`；Candidate ACTION 或 `NO_ACTION` 不绕过最终 Gate；`conditional_add` 在 V1 不创建普通 Intent |
| Fill | Intent 与 Fill 分离；没有 broker API、手工 paper order 或 LLM fill path；V1 不主动制造 partial fill |
| Future Quote | 只接受持久化 `LiveQuoteObservation`，`captured_at > decision_finalized_at`、`EXACT`、合法质量和价格 |
| EOD / Intraday | 15:10 决策推迟到下一个交易日开盘；盘中只看完成之后的 quote |
| Quote Persistence | 复用现有 monitor 采集相关持仓、决策标的和 pending intent 标的，不新增第二行情线程 |
| Stale / Outage | 无未来 quote、时间不精确、质量非法或 provider outage 时 pending/expired，不使用 stale/reference fallback |
| Suspension / Limits | 停牌等待；BUY 涨停和 SELL 跌停阻断 |
| Cash / Reserve | Shadow 自有现金；reserve 与 spendable 不混用，现金不足独立阻断 |
| T+1 / ETF | Stock T+1；未知 ETF 采用 `T_PLUS_1_CONSERVATIVE` |
| Lot / Costs | 复用 SecurityMaster lot size；成本复用 Phase E `transaction_cost_estimate()` |
| Slippage | `slippage_not_modeled=true`；只显示 execution delay/drift，不伪造滑点 |
| Ledger / State | append-only ledger；materialized state 可按 generation rebuild |
| Daily Snapshot | 每日收盘快照，记录 cash/equity/return/drawdown/benchmark/basis；持仓缺 mark 时 fail-close |
| Benchmark | Phase C All-A Median Index |
| Outcomes | 1/5/10/20/60 trading days；Portfolio return 来自 Shadow equity，benchmark 独立；Outcome 与 Fill 分离 |
| NO_ACTION | 独立 observation/outcome；可计算市场下跌回避，不以收益 0 代替 |
| Candidate Veto | Candidate ACTION 被 Final NO_ACTION 否决时记录 `CANDIDATE_VETO` outcome，不创建 intent |
| Actual Alignment | 只匹配确认 Trade Ledger timestamp，不从 holdings diff 猜测 |
| Cohort | parameter hash、decision/runtime contract、shadow generation 分 cohort；target/horizon 独立统计，不混算回测 |
| Scheduler / Worker | 接入现有唯一 scheduler；分析完成、quote 更新和 EOD maintenance 使用独立 session |
| Failure Isolation | Shadow 失败记录日志并降级，不阻断生产分析/风险决策 |
| Shadow Health / Diagnostics | System Health 增加 Shadow 汇总状态；诊断包包含 active generation、pending/blocked、过期意图、失败 evaluation、最近快照/验证时间，不暴露持仓明细或 broker identifier |
| Privacy / Diagnostics | 不新增 broker 写入口；保留用户/组合 ownership filter，诊断不暴露完整敏感明细 |
| Frontend | 新增一级 Shadow 页面；顶部显示“模拟 / SHADOW / 不会真实下单”，拆分 Decision / Execution / Outcome |

## Verification

| 检查项 | 当前状态 |
| --- | --- |
| Phase N专项测试 | 已通过 `19 passed`：既有 Phase N 回归 + N.1 条件加仓、卖出阻断、Portfolio equity/benchmark、future quote eligibility、mark fail-close、benchmark fail-close、target/horizon summary，以及最终 as-of mark visibility 回归 |
| Existing backend regression | 已通过 `422 passed`、58 warnings；其中 migration/backup 断言已更新为跟随真实 `code_head`，并增加 Shadow health 缺表/已安装 schema 聚合测试 |
| Alembic current/upgrade | 已通过：`20260829_0020 (head)`；`upgrade head` 幂等成功 |
| Frontend typecheck | 已通过 `npm run typecheck` |
| Frontend build | 已通过 `npm run build`；仅有 Vite 大 chunk warning |
| Docker build | 已通过 |
| GitHub CI | PR 创建前尚无 workflow run，待远端 PR CI 确认 |

## Explicit Non-Features

本 Phase 不实现 Real Broker Order、Auto Trading、半自动实盘、Auto Apply
Calibration、Auto Factor/Threshold Learning、RL/AutoML、Strategy Optimizer、
Level2/Order Book impact、full slippage、margin、futures/options、full tick
database、第二 Scheduler、Redis/Kafka/Celery、broker account synchronization。

## Known Limitations

历史盘中 quote 可能不完整；V1 不建模盘口队列、完整滑点或冲击；ETF T+0 元数据
不完整时保守按 T+1；实际用户对齐依赖 Trade Ledger 完整性；Shadow 会与真实
Portfolio 自然分叉；Live evidence 需要真实交易日持续积累，样本不足时必须显示
`INSUFFICIENT_LIVE_EVIDENCE`；不得用 Phase M 回测样本冒充 Live evidence。
