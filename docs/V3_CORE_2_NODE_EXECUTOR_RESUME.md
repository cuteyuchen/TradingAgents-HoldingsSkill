# V3-CORE-2 Node Executor / Retry / Checkpoint Resume

状态：

- `IMPLEMENTED = YES`（Node Executor + Retry Policy + Checkpoint Resume + Audit 独立事务）
- `TESTED = AUTOMATED_ONLY`
- `MERGED = PENDING`
- `VERIFIED = NO`（真实行情 / 真机 UI / 真实 Provider / 真正 Multi-Agent / ZIP 导出均未验证）
- `MANUAL_UAT = REQUIRED`（前端人工体验需要用户人工验证）

本阶段成功定义：失败、取消、中断的 Analysis 可以在同一 `AnalysisRun` 上恢复；历史 Stage / Node / Attempt / Artifact / Claim 不得删除；节点重试与模型层 Transport / Structured Retry 分离。

## 背景

V3-CORE-1 已经把 `run_analysis_job()` 的阶段落成可查询的 Workflow Audit。但当时 Job Retry 仍会把同一 Run 当成整次重来，并清空子行。审计无法表达 `Attempt #1 -> Attempt #2`，也无法在业务事务回滚后保住失败现场。

V3-CORE-2 在 CORE-1 模型上补齐真正可恢复的执行基础，不拆 7 个 Analyst，不重写 Bull/Bear，不引入三方 Risk Agents，不改 Candidate / Portfolio Gate 语义。

## Baseline

- Repository：`cuteyuchen/TradingAgents-HoldingsSkill`
- Frozen baseline SHA：`80213944da342a68ea3ddc49224641c8d3b5e50c`
- Product branch：`codex/phase-o-frontend-productization`
- Implementation branch：`codex/v3-core-2-node-executor-resume`
- 投资决策语义、Data Quality Gate、Portfolio Decision Gate、Candidate Engine、Hard Constraint 均未改写
- LLM 不得取代确定性交易约束

## 核心修复

### 禁止删除历史审计

`WorkflowAuditRecorder._purge_children()` 现在直接拒绝。Retry / Resume 复用同一 `AnalysisRun` 以及同一 Stage/Node 行，只追加新的 Attempt 和 Artifact。

- 失败恢复：Same AnalysisRun + New Attempt
- 人工重新分析：`force_restart=true`，仍是 Same AnalysisRun，从起点重放节点，不删历史 Attempt
- 新的分析：New AnalysisJob / New AnalysisRun。`job_id` 仍是 1:1 unique

### Audit 独立事务

生产路径 `run_analysis_job()` 使用 `WorkflowAuditRecorder()` 打开独立 SQLAlchemy session。业务 session 回滚不会删除已提交的 Stage/Node/Attempt/Artifact/Claim；审计提交也不会提交业务对象。

测试仍可传入共享 `db` 以保持旧 recorder 单测写法。SQLite `busy_timeout=5000`。

## Node Executor

统一入口：

```text
Workflow Node -> NodeExecutor.execute() -> Attempt -> Result/Error -> Checkpoint
```

`analysis_engine.py` 的 `_audit_simple_node` / `_audit_required_json` / `_audit_optional_json` 以及 Portfolio Decision Gate 都走 Executor。本阶段不把全部交易逻辑搬出 analysis_engine，只把执行/重试/跳过收口。

Node 生命周期：`pending` / `running` / `succeeded` / `failed` / `blocked` / `cancelled` / `skipped` / `retry_waiting`。CORE-1 的 `completed` 仍作为成功别名读取。

## Failure Classification

- `transient`：timeout、connection、502/503/504，自动节点重试
- `structured_output`：现有 `call_model_json` 的 invalid json / schema mismatch / missing fields / truncated output
- `context_overflow`：full -> compressed -> minimal，由 `compress_payload()` 生成更小输入
- `non_retryable`：401/403、model not found、invalid config、missing required data。未知错误默认不可重试，避免把 bug 当流量打

## Retry Policy

结构：

```text
Node Attempt -> Model Call -> Transport Retry -> Structured Retry
```

默认 `max_attempts = 3`，由 `NodeSpec.retryable` / `NodeSpec.max_attempts` 控制。质量门控、Candidate Gate、Portfolio Decision Gate 仍是 fail-closed，不走节点重试。

Criticality：

- `mandatory`：终端失败阻断 Run
- `important`：记录降级并继续
- `optional`：跳过并记录 warning（节点状态 `skipped`）

Legacy `_audit_required_json` 在节点重试耗尽后仍 `fail_closed=True`，所以投资辩论等 required Skill phase 失败不会静默产出可执行建议。这保持 CORE-1 / Phase O.2 的失败语义；真正按 IMPORTANT 降级只用于 optional 节点和后续 CORE-3 显式可选 Agent。

## Checkpoint Resume

失败 / 中断 / 取消且 `last_checkpoint != FINALIZED` 的 Run：`resumable=true`。

恢复时读取 CHECKPOINT Artifact 中的 `completed_nodes` / `input_hashes` / `output_hashes`，跳过已成功节点，从失败节点追加 Attempt。

Resume 安全：若 CHECKPOINT 里已有 `portfolio_snapshot` 或 `evidence_pack` hash，当前输入不一致则拒绝恢复（`ResumeRejected` / `resume_input_hash_mismatch`）。不要求实时 `market_snapshot` hash 匹配；跳过的 market stage 从 Artifact 重载。

Job Retry：

- 默认：`POST /api/v2/analysis/jobs/{id}/retry` 恢复现有 Run
- 人工重放：`?force_restart=true` 写入 `job.context_json.force_restart`

## Token / Model Audit

Attempt 尽量记录 `provider` / `model` / `request_id` / `input_tokens` / `output_tokens` / `latency_ms` / `transport_retry_count` / `structured_retry_count` / `failure_class`。只从真实 `ModelResult.raw.usage` 读取 token 与 request id，不伪造。拿不到就保持 null。

## 数据库迁移

- revision：`20260905_0022`
- down_revision：`20260905_0021`
- 变更：`analysis_node_attempts.failure_class VARCHAR(32) NULL`
- `init_db()` 对已有表做 lightweight ALTER

旧 AnalysisRun / CORE-1 审计行继续可读。`succeeded` 与 `completed` 都视为节点成功。

## 兼容策略

- 现有 jobs/runs API 保持兼容
- `structured_result_json` 继续双写
- 前端不改；retry 新查询参数默认为 false
- 后续 V3 UI 仍冻结为 Quasar：不继续扩大 Naive UI + 自写 CSS + Tailwind 混合体系；涨红跌绿是金融语义，必须与 success/error/risk color 分离。本阶段不实施迁移

## SQLite 隔离

生产 `run_analysis_job()` 把 `business_db` 交给独立 audit session。审计提交前如果业务 session 只有只读事务，会 `rollback()` 释放 SQLite shared lock；如果业务 session 有 dirty 对象则不碰，避免审计提交误带上 Job 状态。

独立 audit 连接使用 `PRAGMA busy_timeout=15000`。Acceptance 仍可能用 `DELETE` journal，因此这是 SQLite 上实现“业务失败不删审计”的必要卫生，不是把审计写回业务事务。

## 测试结果

见最终报告。本阶段至少覆盖：

- Node transient retry 保留 Attempt #1/#2
- Structured output 失败后新的 Node Attempt（与 `call_model_json` 的 structured budget 分离）
- 401/config 不重试
- CONTEXT_OVERFLOW：full -> compressed -> minimal
- Resume 跳过已成功节点并给失败节点新 Attempt
- Retry 禁止 purge
- force_restart 同 Run 从起点重放且保留历史 Attempt
- Interrupted Run 可恢复
- input hash mismatch 拒绝恢复
- Audit session 与业务 rollback/commit 隔离
- Job retry 默认 resume；`force_restart=true` 重放
- CORE-1 success/failure/blocked/cancel/authorization 回归

## 明确未做

- 真正 7 个 Analyst Agents
- 真正多轮 Bull/Bear
- 三方独立 Risk Agents
- ZIP Debug Export
- Quasar / 首页 / 持仓 / 分析页重构
- 完整并行 Multi-Agent Workflow（V3-CORE-3）

## 后续 CORE-3 接口

CORE-3 应直接建立在 NodeExecutor + NodeSpec + Checkpoint 上：

- 把 `analyst_team_legacy` 拆成独立 Analyst Node
- 把 `investment_debate_legacy` / `risk_debate_legacy` 拆成可并行的发言 Node
- 继续禁止 purge 历史 Attempt
- 继续让 Quality / Portfolio / Candidate Gate 做 fail-closed 的确定性节点
