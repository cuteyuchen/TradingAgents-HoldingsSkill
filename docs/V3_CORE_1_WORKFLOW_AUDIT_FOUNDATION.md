# V3-CORE-1 Analysis Workflow / Audit Foundation

状态：

- `IMPLEMENTED = YES`（数据契约 + 生命周期 + 审计持久化）
- `TESTED = AUTOMATED_ONLY`
- `MERGED = PENDING`
- `VERIFIED = NO`（真实行情 / 真机 UI / 真实 Provider / 断点恢复均未验证）
- `MANUAL_UAT = REQUIRED`（前端人工体验需要用户人工验证）

本阶段成功定义：每一次 Analysis 不论成功、失败、阻断还是取消，都有一个完整、可查询、持久化的 Workflow/Audit 运行骨架。本阶段不是完整 Workflow Scheduler，也不是真正 Multi-Agent 拆分。

## 背景

当前 `run_analysis_job()` 已经按阶段执行，但中间状态主要存在 Python 内存中的 `workflow` dict。`AnalysisRun` 过去基本在全部 LLM 阶段结束后才落库，中途失败时没有完整可查询的运行对象。

V3-CORE-1 把这次顺序执行升级为可审计的持久化基础：

```text
AnalysisJob
  └── AnalysisRun          # 分析真正开始后立即创建，成为权威运行实体
        ├── AnalysisStage
        │     └── AnalysisNode
        │           └── AnalysisNodeAttempt
        ├── AnalysisArtifact
        └── AnalysisClaim
```

后续阶段明确不在本轮完成：

- V3-CORE-2：Node Executor、节点级 Retry、Checkpoint Resume
- V3-CORE-3：真正独立 Analyst / Bull-Bear / Risk Agents
- V3-CORE-4：ZIP Debug Export

## Baseline

- Repository：`cuteyuchen/TradingAgents-HoldingsSkill`
- Frozen baseline SHA：`c7b005901c7116b6bcc68a414bf00fe6447a75b2`
- Product branch：`codex/phase-o-frontend-productization`
- Implementation branch：`codex/v3-core-1-workflow-audit-foundation`
- 本任务不修改 PR `#7` 本身
- 投资决策语义、Data Quality Gate、Portfolio Gate、Candidate Gate、Hard Constraint 均未改写
- LLM 不得取代确定性交易约束

## 新增数据模型

正式迁移：`backend/alembic/versions/20260905_0021_analysis_workflow_audit.py`

- revision：`20260905_0021`
- down_revision：`20260829_0020`

本地 `init_db()` 仍会对已有 `analysis_runs` 做 lightweight `ALTER`，方便测试和开发库自动补列。生产 / CI 以 Alembic 为准。

### AnalysisRun 扩展

保留全部旧字段，并新增生命周期字段。旧数据允许为空；`status` 默认 `completed`，兼容历史成功 Run。

新增字段：`status`、`started_at`、`completed_at`、`workflow_version`、`skill_version`、`analysis_mode`、`market_snapshot_at`、`resumable`、`interrupted_at`、`last_checkpoint`、`failed_stage`、`failed_node`、`error_code`、`error_message`、`last_artifact_id`。

`job_id` 仍是 1:1 unique。Job Retry 复用同一 `AnalysisRun`，并清空该 Run 的 Stage/Node/Attempt/Artifact/Claim 子行后重新开始。这是 CORE-1 限制，不是历史审计版本链；CORE-2 再改为保留历史 Attempt。

### 子表

- `analysis_stages`：Phase 级运行状态。唯一约束 `analysis_run_id + phase_key`。
- `analysis_nodes`：一个具体执行 Node。唯一约束 `analysis_run_id + node_key`。本阶段允许 legacy Node，不拆真实 Agent。
- `analysis_node_attempts`：一次 Node 的实际执行尝试。唯一约束 `node_id + attempt_no`。
- `analysis_artifacts`：不可变审计对象。插入后不更新；新版本创建新行。
- `analysis_claims`：把现有内存中的 `INV-*` / `RISK-*` normalized claims 落库。不改 Debate 算法。

Attempt 尽量记录：

- `structured_retry_count`：来自现有 `call_model_json().retry_count`
- `transport_retry_count`：来自 `ModelResult.retries`，经 `StructuredModelResult.transport_retry_count` 小范围透出
- `raw_text`：仅在真实存在时写入 `MODEL_RAW_OUTPUT` Artifact，不伪造

`input_tokens` / `output_tokens` 字段已建，当前 runner 未填充，留给 CORE-2。

Artifact SHA-256 计算对象是 redaction 之后、canonical JSON 之后的实际存储内容。Secret 不会进入 hash 前的持久化原文。`structured_result_json` 中的 claims 继续双写保留。

## Run 生命周期

状态使用项目既有小写字符串，并集中在 `RunStatus`：`queued` / `running` / `completed` / `blocked` / `failed` / `cancelled` / `interrupted`。

1. Snapshot / 用户 / portfolio / 运行参数明确后，立即 `AnalysisRun(status=running)`。
2. 成功：`completed`，继续写 `structured_result_json` + markdown。
3. 失败：保留 Run，`failed`，记录 failed stage/node、last artifact、error summary。
4. Quality / Hard Gate 明确阻断：Run = `blocked`。为保持现有前端兼容，AnalysisJob 仍为 `succeeded`，并继续写观察-only 的 structured result / markdown。
5. 用户取消：Run = `cancelled`，已完成审计记录保留。
6. `GET /api/v2/analysis/runs` 只返回 `completed` / `blocked`（以及旧数据 `status IS NULL`），避免 AnalysisView 自动打开空失败 Run。按 ID 查询失败 Run 的 workflow/timeline 仍然允许，且必须用户隔离。

`WorkflowAuditRecorder` 每次写审计后立即 `commit()`。后续 `db.rollback()` 不能删掉已经落库的审计行。

## Stage / Node / Attempt 契约

中央定义在 `backend/app/analysis_workflow/constants.py`，禁止散落字符串。

Recorder API：`start_run` / `finish_run` / `fail_run`，`start_stage` / `finish_stage` / `fail_stage`，`start_node` / `finish_node` / `fail_node`，`start_attempt` / `finish_attempt` / `fail_attempt`，`record_artifact` / `record_claims` / `checkpoint`。

`run_analysis_job()` 以最小侵入 Adapter 方式，在现有 `_job_stage()` 附近调用 Recorder。交易逻辑不搬迁。LLM Node 通过 `_audit_required_json()` / `_audit_optional_json()` 包装现有 `call_model_json()`。不另写第二套 model retry。

条件 Node：`trader_revision` 仅在 `risk_manager` 要求 revise 时执行；`candidate_llm_review` 仅在存在 deterministic ACTION 候选时执行。

## Artifact 契约

类型：`INPUT`、`EVIDENCE`、`PROMPT_TEMPLATE`、`RENDERED_PROMPT`、`MODEL_RAW_OUTPUT`、`STRUCTURED_OUTPUT`、`QUALITY_GATE`、`CLAIMS`、`CHECKPOINT`、`ERROR`、`FINAL_DECISION`、`MARKET_SNAPSHOT`、`PORTFOLIO_SNAPSHOT`。

序列化：`canonical_json()` 使用 `sort_keys=True` 和稳定 separators；`sha256_content()` 对规范化后的存储内容计算。先走现有 `redact_object()` / `redact_text()`，再按 secret 字段名 blanking。API Key / Authorization / Cookie / Secret / webhook 不得原文持久化。

普通 API 默认只返回 Artifact metadata。内容走 `GET /api/v2/analysis/runs/{run_id}/artifacts/{artifact_id}`。

## Claim 契约

状态：`open` / `addressed` / `resolved` / `unresolved` / `accepted` / `rejected` / `partially_accepted`。

legacy debate 完成后调用 `record_claims()`，保存 claim id、speaker、stance、statement、evidence、confidence、status、target ids。

## Criticality

统一概念：`mandatory` / `important` / `optional`。本阶段只保存和映射，不实现自动降级 Scheduler。

| Node | Criticality | 理由 |
| --- | --- | --- |
| `context_loader` / Portfolio Snapshot | mandatory | 无确认快照不得分析 |
| `market_snapshot_collector` | mandatory | 无市场快照不得进入决策 |
| `quality_gate` | mandatory | 质量门是硬阻断 |
| `analyst_team_legacy` | important | 当前仍是聚合证据包，不是独立 Analyst |
| `investment_debate_legacy` | important | 当前是 legacy 单次辩论 |
| `research_manager` | mandatory | 研究裁决是后续交易输入 |
| `trader` | mandatory | 交易方案输入 |
| `risk_manager` | mandatory | 风控硬约束入口 |
| `trader_revision` | important | 条件修正，不是每次必有 |
| `risk_debate_legacy` | important | 当前是 legacy 三方摘要 |
| `final_quote_refresh` | mandatory | Gate 必须看到刷新后的价格事实 |
| `deterministic_candidate_gate` | mandatory | 确定性 Candidate Gate，不可被 LLM 取代 |
| `candidate_llm_review` | optional | 只评审已通过确定性门的候选；失败不阻断 |
| `portfolio_manager` | mandatory | 组合结论 |
| `portfolio_decision_gate` | mandatory | 硬约束；失败走 fail-closed，不输出危险可执行建议 |
| `report_renderer` | mandatory | 成功/阻断 Run 仍需可查询报告骨架 |

optional Node 失败只记录 audit，并由现有 legacy `phase_errors` 兼容。

## Checkpoint

Checkpoint 是数据契约，不是 Resume Executor。名称：`CONTEXT_READY`、`MARKET_READY`、`ANALYSTS_DONE`、`QUALITY_GATE_DONE`、`DEBATE_DONE`、`RESEARCH_DONE`、`TRADER_DONE`、`RISK_DONE`、`CANDIDATES_DONE`、`PORTFOLIO_DONE`、`FINALIZED`。

每个 Checkpoint Artifact 记录 `run_id`、`checkpoint`、`completed_nodes`、`input_hashes`、`output_hashes`、`created_at`。`AnalysisRun.last_checkpoint` 同步更新。`FINALIZED` 后 `resumable=false`。

当前 Adapter 主要写入 output hash。`bind_input_hash()` 已提供，但 legacy runner 尚未普遍调用，因此 `input_hashes` 目前可能稀疏。这是有意留给 CORE-2 的执行器填充，而不是伪造输入 hash。

## Resume Contract

Helper 已存在，不执行恢复：`is_run_resumable(run)` 与 `resume_from_checkpoint(run, db=None)`。

只在 `failed` / `interrupted`、`resumable=true`、且 `last_checkpoint` 存在且不是 `FINALIZED` 时返回可恢复。返回结构包含 completed nodes、hashes 和 `executor=None`。

只读 API：`GET /api/v2/analysis/runs/{run_id}/resume-contract`

## Legacy Phase Mapping

```text
context_loading
  └── context_loader
market_collecting
  └── market_snapshot_collector
analysts_running
  └── analyst_team_legacy
quality_gate
  └── quality_gate
investment_debate
  └── investment_debate_legacy
research_verdict
  └── research_manager
trader_proposal
  └── trader
risk_revision
  ├── risk_manager
  └── trader_revision (conditional)
risk_debate
  └── risk_debate_legacy
final_quote_refresh
  └── final_quote_refresh
candidate_screening
  ├── deterministic_candidate_gate
  └── candidate_llm_review (conditional)
portfolio_synthesis
  └── portfolio_manager
portfolio_decision_gate
  └── portfolio_decision_gate
report_rendering
  └── report_renderer
```

未拆 Technical / Sentiment / News / Fundamental / Policy / Capital Flow / Lockup。未改 `investment_debate_legacy`、`risk_debate_legacy`。

## 兼容策略

双写：旧 `structured_result_json` + markdown 格式保持不变；新 Stage / Node / Attempt / Artifact / Claim 成为审计权威面。

失败 Run 允许 `summary = null`、`final_rating = null`、`markdown_text = ""`，但仍可查询 workflow / stages / nodes / attempts / artifacts / errors / timeline。

旧 Analysis API 继续工作：jobs 的 create/get/cancel/retry/events，以及 runs 的 list/get/markdown/comparison。

新增只读审计 API，全部用户隔离，不返回 secret：

- `GET /api/v2/analysis/runs/{run_id}/workflow`
- `GET /api/v2/analysis/runs/{run_id}/stages`
- `GET /api/v2/analysis/runs/{run_id}/nodes`
- `GET /api/v2/analysis/runs/{run_id}/attempts`
- `GET /api/v2/analysis/runs/{run_id}/artifacts`
- `GET /api/v2/analysis/runs/{run_id}/artifacts/{artifact_id}`
- `GET /api/v2/analysis/runs/{run_id}/claims`
- `GET /api/v2/analysis/runs/{run_id}/timeline`
- `GET /api/v2/analysis/runs/{run_id}/resume-contract`

前端仅做 API 兼容最小修复：`AnalysisView` 只在 job `succeeded` 时自动 `selectRun`。不改 Dashboard / Holdings 主流程，不引入 Quasar。

`build_analysis_timeline(run_id)` 按 Stage / Node / Attempt / Artifact / Claim / Run 时间戳生成稳定事件流。本阶段不做 ZIP 导出。

不得重写：AnalysisJob / AnalysisRun 旧字段、Model Client Transport Retry 与 Structured Output Retry、`redact_text()` / `redact_object()`、Candidate Engine、Portfolio Engine、Portfolio Decision Gate、Data Quality Gate、Decision Memory、PIT Historical Data、Shadow / Backtest、Provider Pipeline、Security Identity、Trading Calendar。

## 后续 V3 UI 冻结约束

本任务只记录，不实施迁移。后续 V3 UI 统一采用 Quasar 作为主 UI Component Framework。

- 不继续扩大 Naive UI + 自写 CSS + Tailwind 的混合体系
- 新 V3 页面禁止随意手搓 Button / Card / Dialog / Drawer / Table / Tabs 等框架已有组件
- 样式通过统一 Design Tokens / Theme 管理
- 红涨绿跌是金融语义：涨红跌绿；市场颜色必须与 success/error/risk color 分离
- 桌面 / 移动统一组件体系，但允许响应式信息架构不同
- K 线等专业图表使用专业 Chart Library，不要求 Quasar 自己绘制

## 测试结果

命令与结果以本轮自动验证为准。未覆盖项不得标记 VERIFIED。

### 新增

| Command | Result |
| --- | --- |
| `python -m pytest tests/test_analysis_workflow_audit.py tests/test_analysis_workflow_audit_integration.py -q --tb=line` | `11 passed in 21.48s` |

覆盖：AnalysisRun lifecycle；Stage / Node / Attempt uniqueness；Artifact SHA-256；Claim persistence；migration upgrade / downgrade；Recorder start/finish/fail + artifact/claims/checkpoint；Redaction（`api_key` / `Authorization` / Bearer Token / cookie / password / secret / webhook）；成功分析 integration（COMPLETED + stages/nodes/attempts/artifacts/claims + 旧 JSON/markdown）；`research_manager` 失败（Job failed，Run FAILED，此前 Stage/Node 保留，timeline 可查）；Data Quality Gate blocked（Run BLOCKED，不输出危险可执行建议）；cancelled job 审计行不清除；User A 不能访问 User B 的 workflow/node/attempt/artifact/claim/timeline。

### 回归

| Command | Result |
| --- | --- |
| 相关回归 `test_v2_portfolio_analysis.py` / `test_analysis_workflow.py` / `test_structured_retry.py` / `test_phase_h_migrations.py` / `test_skill_runtime.py` | 17 passed（实现期间） |
| `pytest tests -q --tb=line` | 实现期间先出现 `1 failed, 513 passed`；失败项为全局 Fuyao provider health registry 被污染。integration 增加 isolation fixture 后，`audit + integration + test_fuyao_provider.py` 共 19 passed。全量 suite 未在 isolation 之后再跑一遍，避免无产品代码变更的 6 分钟重复。 |
| `npm.cmd run typecheck` | passed |
| `npm.cmd run build` | passed，`vite built in 7.80s` |
| `npm.cmd run e2e:acceptance` | 第一次：`30 passed, 1 failed`（`visual.spec.ts` 在 logout/navigation 后收集到 `GET /api/v2/analysis/runs/8` 的 401 残留，属于 visual smoke 竞态，不是 audit API 断言失败）。第二次：`31 passed (1.1m)`，`Phase O.2 acceptance passed`。 |
| `docker compose build` | passed，镜像 `daily-holdings-trading-advisor-advisor` 构建成功 |

## 后续 CORE-2 接口

CORE-2 应直接建立在本阶段模型上，不要另起一套表。

- Node Executor：按 `LEGACY_PHASES` / `NodeSpec` 调度，而不是继续加厚 `analysis_engine.py`
- Node Retry Policy：消费 `retryable` / `max_attempts` / Attempt `retryable`
- Resume Executor：读取 `last_checkpoint` + CHECKPOINT Artifact，跳过 `completed_nodes`
- 保留 Job Retry 的历史 Attempt，不再 purge 子行
- 补齐 raw output、token usage、input hash
- 真正节点失败后的 optional 降级策略，但仍不得让 LLM 越过 Hard Gate

明确未做：7 个独立 Analyst Agents；真正多轮 Bull/Bear；三方独立 Risk Agents；完整 Node-level Retry Scheduler；完整 Resume；ZIP 日志导出；Quasar / K线 / 盘口 UI。

## Not Verified

以下不得标记为已验证：真实券商截图；真实市场盘中运行；真实 Provider 长时间稳定性；人工 UI 体验；移动端 UI；真正 Multi-Agent 并发；Node Resume；完整 ZIP Debug Export。
