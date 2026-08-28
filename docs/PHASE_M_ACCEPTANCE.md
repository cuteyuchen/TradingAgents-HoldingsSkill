# Phase M Acceptance

基线：`1c004badf1ff0056b006a6885b7cd7057c9737e0`

分支：`codex/phase-m-pit-deterministic-recompute`

Phase M 把 `DETERMINISTIC_RECOMPUTE` 从“名字存在但 fail-close”变成真正的
确定性历史重算：从当时可见的 PIT 原始事实重新走生产 Market / Candidate /
Portfolio 决策算法，不读取 persisted CandidateRun / CandidateScore /
MarketScoreSnapshot 作为输入。

## 0. Recompute Audit

`docs/PHASE_M_RECOMPUTE_AUDIT.md` 已记录生产函数审计。摘要：

- 直接复用生产纯计算核心：Market metrics/components/score/smoothing/
  hysteresis/coverage，Candidate opportunity/entry/RR/fit/decision-edge/
  stage/ranking，Portfolio constraints/gate，Phase E TransactionCostModel。
- 不复制第二套 backtest-only score engine。
- 强依赖 live provider 的路径（live quote fetch、live portfolio context、
  provider K-line）在 recompute 中从不调用。
- 唯一需要的抽取是 PIT universe resolver 的纯事实版本
  `resolve_equity_universe_from_facts`，用于批量日期内存重建。

## 1. Pure Core Extraction

`backend/app/recompute/` 只做 PIT 适配和编排，算法权威仍是生产代码：

- `market.py` 调用 `backend/app/market/engine/*` 的 metrics/components/score。
- `candidate.py` 调用 `backend/app/candidates/service.py::_score_row`、
  `score_stock_candidate`、`score_etf_candidate`、`rank_candidates`、
  `take_stage_limits`。
- `portfolio.py` 调用生产 Portfolio constraints / decision gate。
- 输入由 `dataset.py` 一次性批量 materialize 成 `RecomputePitDataset`，
  日期窗口、warmup、fundamental visibility、bars cutoff 都在内存索引中执行。

## 2. Market Recomputed

每个交易日在 15:10 Shanghai EOD checkpoint（UTC-naive `07:10`）下：

1. 从 PIT lifecycle/trading/ST 事实重建 MARKET_SCORE universe；
2. 用历史 bars 重算 breadth/trend/liquidity/profitability/diffusion/
   crowding/tail-risk 组件；
3. raw score → smoothing → regime/hysteresis 按时间顺序重放；
4. 记录 warmup_start / warmup_days / warmup_complete；
5. coverage < 0.95 时按生产语义 MISSING/freeze。

Persisted `MarketScoreSnapshot` 只用于对比验证，不是 recompute input。
Smoothing 不偷用 persisted prev smoothed score。

## 3. Candidate Recomputed

CANDIDATE scope 从 PIT universe 重新扫描：

- STOCK：SSE/SZSE ordinary A，exclude BSE/ST/停牌/退市/历史持仓/
  上市 <60 交易日。
- ETF：active exchange-traded ETF，exclude held/inactive/delisted/
  suspended；constituent lookthrough 缺失时明确 PARTIAL。
- 每个候选真实重算 Opportunity / Entry / R/R / Portfolio Fit /
  Held Comparison / Transaction Cost / Decision Edge / Ranking /
  Watch/Ready/Action stage。
- 输出 `factor_audit`：factor_name/value/available/source/coverage/
  effective_weight/missing_reason。

Flow / Industry 历史缺失时保持 unavailable，`effective_weight=0`，绝不填 0
当作真实因子，绝不 backfill current/future。

## 4. Portfolio Decision Recomputed

PORTFOLIO_DECISION 同时重算 Market + Candidate + Portfolio gate：

- 历史持仓来自 as-of 前最近 confirmed snapshot；
- cash 缺失时 fail-close（`INSUFFICIENT_CASH_DATA`），不推算余额；
- Candidate ACTION 仍必须过 Portfolio Gate，最终只输出 NO_ACTION / ACTION；
- NO_ACTION 是 first-class 结果，Action=[] 正常。

## 5. Capability Matrix

| Scope | Phase M V1 支持 | 默认 capability | 可 FULL 条件 |
| --- | --- | --- | --- |
| MARKET EOD | 是 | PARTIAL_PIT_RECOMPUTE | Industry 历史 FULL + warmup 完整 |
| CANDIDATE_STOCK EOD | 是 | PARTIAL_PIT_RECOMPUTE | Flow + Industry 历史 FULL |
| CANDIDATE_ETF EOD | PARTIAL/DIAGNOSTIC | PARTIAL_PIT_RECOMPUTE | ETF constituent history FULL |
| PORTFOLIO_DECISION EOD | 是 | PARTIAL_PIT_RECOMPUTE | 上游全部 FULL + portfolio PIT FULL |
| 任何 intraday checkpoint | 否 | UNSUPPORTED | Phase M 无 intraday PIT |

`build_recompute_capability_manifest` 按 scope 输出 required/available/
partial/missing inputs、coverage、parameter_version、config_hash、
universe_version、price_basis、limitations。

## 6. PIT Input Manifest

Market required inputs：

- historical_security_state
- historical_trading_status
- historical_st_state
- daily_bars
- price_basis

Stock Candidate 追加：

- historical_valuation
- fundamental_publication

ETF Candidate 用 etf_metadata 替代 fundamental；Portfolio Decision 需要
portfolio snapshot / holdings，cash 缺失时 gate fail-close。

## 7. Parameter Lineage

- BacktestRun 创建时冻结 governance snapshot、version、hash；
- 执行 recompute 使用 frozen `baseline_config_json` /
  `parameter_set_version` / `parameter_set_hash`，不重新读取 current ACTIVE；
- 无 governance 历史时使用 `LEGACY_PRE_GOVERNANCE` + 可证明的 code-default
  snapshot；
- Calibration challenger 只作为实验，绝不写回 ACTIVE。

## 8. Source Lineage / Frozen Source Integrity

`RecomputePitDataset.source_ids()` 生成稳定 typed source references：
calendar/lifecycle/classification/status/valuation/fundamental/etf/basis/
bars/benchmark/portfolio snapshot/holdings。

- BacktestRun 冻结 `frozen_source_ids` + `frozen_source_set_hash`；
- 执行前 source set 变化 → `SOURCE_SET_CHANGED` / INVALIDATED；
- 旧 run 不 silent 使用后来新增 revision；
- 新 Backtest 可以使用新 revision。

## 9. No Network / No LLM

`execute_backtest_run` 全程包在 `historical_replay_network_policy()` 内；
任何 provider/model client 调用都会触发
`historical_replay_external_io_blocked`。Recompute 只读 frozen local PIT facts。

## 10. Full vs Partial

- `FULL_PIT_EQUIVALENT`：所有 production-required input + 历史参数 +
  历史 portfolio + 相同 production algorithm path 全满足。
- `PARTIAL_PIT_RECOMPUTE`：PIT 输入真实，但 Flow / Industry / ETF
  constituent / quote proxy 等缺失或降级；必须明确 missing factors、
  available weights、coverage 和 limitations。
- `DIAGNOSTIC_ONLY` / `DATA_GAP` / `LEAKAGE_BLOCKED` / `UNSUPPORTED`：
  不能作为正式 Calibration 证据。

PARTIAL 永远不显示成“完整回测”。

## 11. Calibration Eligibility

- FULL 可以作为强 Calibration evidence；
- PARTIAL V1 默认 diagnostic only，不能直接 `CONSIDER_CHANGE`；
- DIAGNOSTIC_ONLY 不允许创建 Standard Proposal；
- Phase M 不绕过 Phase J governance，无 Auto Apply。

## 12. Deterministic Hash

`DeterministicRecomputeResult.deterministic_hash` 使用 canonical JSON +
SHA-256，排除 runtime timestamp、DB auto id、query_count。

- 所有集合显式 stable sort；
- 同 DB snapshot + 同日期 + 同参数运行两次 hash 相同；
- `cohort_recompute_summary.deterministic_hash` 对逐日 hash 再稳定聚合。

## 13. Feature / Outcome Cutoff

- 决策特征只使用 `available_at <= EOD 15:10 cutoff` 的 bars；
- future bar 不进 feature 输入、不进 frozen source_ids；
- outcome 评估单独使用 future bars/benchmark，与 feature cutoff 严格分离。

## 14. Phase I Backtest Integration

`load_replay_facts()` 的 DETERMINISTIC_RECOMPUTE 分支不再抛
`ENGINE_NOT_IMPLEMENTED`：

- 先跑 `pit_recompute_gate` + capability gate；
- 满足 capability 时真实调用 `recompute_deterministic_scope`；
- recompute cases 进入 `_load_replay_rows`，Market/Candidate outcome 照常评估；
- DETERMINISTIC_RECOMPUTE 不受 persisted top-subset censoring 限制；
  `censored_sample=False`，Calibration 不再自动按 CANDIDATE 判定 censored。

## 15. Runner Stages

执行阶段沿用 BacktestRun stage contract：DATA_AUDIT → LOADING →
REPLAY → OUTCOME → METRICS → FINALIZING；持续 CAS lease + heartbeat +
cancellation + stale reclaim。

## 16. Frontend

Research 页最小增量：

- Replay Mode 为 DETERMINISTIC_RECOMPUTE 时显示 capability preview：
  capability、missing/partial inputs、parameter version、config hash、
  universe version、checkpoint、limitations；
- Run Evidence 显示 actual capability、input coverage、candidate cases、
  action count、no-action rate、deterministic hash、limitations；
- PARTIAL / DIAGNOSTIC_ONLY / DATA_GAP 明确不是 FULL 等价回测。

API：新增 `GET /api/v3/research/recompute-capability`，其余复用现有
Research endpoints。

## 17. Performance Benchmark

真实本地 benchmark（脚本 `backend/scripts/benchmark_phase_m_recompute.py`）：

`backend/benchmarks/phase_m_recompute_500x60_market.json`：

- Scope：MARKET deterministic recompute
- 规模：500 symbols × 60 decision dates
- Warmup：130 trading days（65,000 QFQ bars）
- Seed 数据：30000 classification + 30000 trading status + 30000 valuation +
  30000 price basis + 65000 bars
- SQL SELECT 数：191（固定规模，不随 N×D 增长）
- Dataset query count 总和：420（60 dates × 7 次固定 dataset 查询）
- Wall time：293.1s（含内存采样线程，Windows / Python 3.13）
- RSS peak：约 2008.7 MB
- Capability：`PARTIAL_PIT_RECOMPUTE`
- Deterministic hash：`e76d7c5decaa8a2fde1390ad64563bdd8a7d1ecd28195548cbcf257df68ab554`

两次独立运行 hash 相同。完整测试还验证了 20×10 / 50×30 / 120×60 的
SELECT 数均约为 191，证明查询数不随 securities × dates 爆炸。

已知性能边界：CANDIDATE 全 universe 评分（500×60）在本地开发机上需要
数十分钟，属于 CPU-bound 的 in-memory 因子重算，不是 SQL N+1；V1 通过
batch preload + 内存索引保持查询数恒定，Web worker 仍适合较小 cohort 或
异步长任务。

SQL query count 固定规模，不随 securities × dates 爆炸；dataset 内部
query count 也在结果中记录。完整测试已证明 20×10 / 50×30 / 120×60 均为
约 191 条 SELECT。

## 18. Tests

新增 `backend/tests/test_phase_m_recompute.py`，覆盖：

- ignored persisted CandidateRun/CandidateScore/MarketScoreSnapshot；
- deterministic hash 稳定 + frozen parameter version/hash；
- 历史持仓按 confirmed snapshot 排除/重新进入；
- future fundamental revision 发布前不可见；
- missing flow/industry：unavailable != 0、capability != FULL；
- warmup 不完整不能 FULL；
- future bar 不进 feature cutoff / source_ids；
- portfolio gate 在 cash 缺失时 blocking；
- today holdings 不影响历史快照；
- 大数据集 SQL 查询数有界。

既有回归：

- `tests/test_history_pit.py`：34 passed；
- `tests/test_research_phase_i.py` + `test_research_phase_i_1.py`：
  32 passed；
- 全量 backend pytest：395 passed（Phase M 完成后重新执行确认）。

## 19. Migration / Runtime

- 零新增 migration；
- Runtime / Decision Contract 保持 `2.4.0`；
- 不修改 live 投资决策语义，Research 只复用 production deterministic core。

## 20. Known Limitations

这些是 Phase M 的诚实 capability 边界，不是失败：

- Historical Flow 不可用；
- Historical Industry 不完整；
- ETF constituent / lookthrough 历史不可用；
- Intraday PIT 不完整，V1 只支持 EOD / 15:10；
- Historical quote 使用 EOD close proxy；
- Slippage not modeled；
- 历史 broker cash 缺失时 fail-close。

## 21. 未实现

- Auto Factor Learning / Auto Threshold Learning
- RL / AutoML / Strategy Optimizer
- Auto Apply / Auto Trading / Broker Execution / Paper Broker
- Full Level2 / Tick / News NLP replay
- Full Historical Flow / Industry / ETF Constituents
- Full Intraday PIT
- PostgreSQL / Distributed Data Warehouse

## 22. PASS 结论

Phase M 核心目标已实现：`DETERMINISTIC_RECOMPUTE` 真正从 PIT 事实重走生产
算法，所有 PARTIAL / DATA_GAP / LEAKAGE_BLOCKED 均诚实标注，不伪造 FULL。
