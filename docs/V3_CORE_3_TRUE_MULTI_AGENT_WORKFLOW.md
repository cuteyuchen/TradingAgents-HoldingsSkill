# V3-CORE-3 True Multi-Agent Workflow

## Status

- IMPLEMENTED: independent nodes on the existing V3-CORE-2 NodeExecutor.
- TESTED: automated unit, integration and browser acceptance tests; see Validation.
- MERGED: no; this is a stacked change, not authorization to merge.
- VERIFIED: automated evidence only, not production or investment validation.

## Repository State

- Repository: `cuteyuchen/TradingAgents-HoldingsSkill`.
- Frozen CORE-2 baseline: `42b6fe1560424048e5c37a8a33ebf1391dbf13b3`.
- Branch: `codex/v3-core-3-true-multi-agent-workflow`.
- Intended PR base: `codex/v3-core-2-node-executor-resume`.
- The PR diff against that base contains CORE-3 only. Its ancestry includes
  CORE-1 and CORE-2, but their cumulative diffs are not part of this stacked PR.
- Before edits, `git status --short`, `git branch --show-current`,
  `git rev-parse HEAD`, and `git log -5 --oneline` were checked.
- The original CORE-2 checkout had untracked `tmp_*` logs and a temporary
  migration database. Work continued in the independent `-core3` worktree.
  None of those original files were changed or deleted.
- The recorded five commits were `42b6fe1`, `8021394`, `c7b0059`, `4d362aa`,
  and `d3e42b0`. The implementation worktree started from the exact baseline.

## Scope

No second workflow, retry engine, audit schema, candidate engine, or portfolio
engine is introduced. The existing NodeExecutor, retry policy, failure
classification, attempts, artifacts, claims, checkpoints and timeline remain
authoritative. Frontend application views are unchanged.

The frozen Skill remains the role definition:
`skill/tradingagents-holdings-advisor/references/multi-agent-workflow.md`.
Technical analysis belongs to Market Analyst; Hot Money Tracker maps to
Capital Flow Analyst and Lockup Watcher maps to Lockup/Supply Analyst.

## Node Plan

`analysis_workflow/dag.py` builds readable PhaseSpec/NodeSpec plans with explicit
dependencies, parallel groups, conditional nodes and analysis modes.

| Mode | Analyst Nodes | Research and Debate | Typical Model Calls |
| --- | --- | --- | --- |
| Fast | Market, Capital Flow | Reuse prior research; no investment or three-way risk debate | 5 |
| Standard | Market, News, Fundamentals, Policy, Capital Flow | Research, Trader, Risk Manager, Portfolio Manager | 9 |
| Deep | All seven | Four alternating debate turns, Resolver, all managers, three risk agents, Synthesis | 20 |

Counts exclude retries, optional candidate review and the at-most-once Trader
Revision. Fast retains the Trader/Risk/Portfolio constraint-recheck chain. It
does not manufacture new research when no reliable same-snapshot prior run is
available; that case is grade C and cannot increase risk.

Deep topology:

```text
Context -> Market -> Frozen Evidence
  -> [Market | Sentiment | News | Fundamentals | Policy | Capital Flow | Lockup]
  -> Quality Gate
  -> Bull R1 -> Bear R1 -> Bull R2 -> Bear R2 -> Claim Resolver
  -> Research Manager -> Trader -> Risk Manager -> Trader Revision?
  -> [Aggressive Risk | Neutral Risk | Conservative Risk]
  -> Risk Synthesis -> Final Quote Refresh -> Candidate Gate/Review
  -> Portfolio Manager -> Deterministic Portfolio Gate -> Final Decision
```

Each bracketed group uses bounded ThreadPoolExecutor workers. Debate turns are
serial. Risk Synthesis cannot start until every risk sibling has a terminal
result, including explicit failure information. Portfolio Manager consumes
that synthesis. No request asks one model to answer for multiple roles.

## Shared Evidence

`evidence.py` persists one `EVIDENCE` artifact containing:

- Confirmed holdings, market data, portfolio constraints, candidate context,
  history, decision memory and trigger context.
- Mode, checkpoint, parameter lineage, Skill metadata, model profile identity,
  system-prompt hash, prompt manifest and node plan.
- Exact evidence reference paths for structured conclusions and claims.

Every model input binds `analysis_run_id`, `evidence_snapshot_id`,
`evidence_hash`, `market_snapshot_at`, and `portfolio_snapshot_id`. Independent
agents receive JSON copies, not a shared mutable dictionary. Agents never
fetch quotes, news or candidate data. Retry and Resume use the same evidence
artifact and contract.

Only Final Quote Refresh can obtain new quotes. Its result is separate from
the frozen research evidence and has its own node, attempt and artifact.
Failed refreshes cannot silently fall back to old prices. Resume rejects an
expired final quote snapshot instead of refetching evidence behind completed
nodes. The final gate also checks refresh age using the existing 90-second
market freshness threshold; stale advice fails closed.

## Output and Prompts

Analyst outputs have a stable schema: role, scope, summary, findings,
portfolio_risks, data_gaps and quality_grade. Findings distinguish fact,
inference, rumor and provider-derived evidence. Capital Flow findings must
identify themselves as provider-derived. Data tables and missing checklist
fields support the existing Skill quality checks.

Each agent records role, version and template_id, plus prompt-template and
rendered-prompt artifacts. Structured output rejects unknown evidence
references, nonexistent opposing claims and unsolicited fields. Context
compression retains evidence identity, confirmed holdings, hard constraints
and actual opponent claims.

Only public conclusions, evidence, claims and rationale summaries are
designed for audit. There are no hidden chain-of-thought or scratchpad fields.
Provider/model/request metadata and token usage come from actual model
responses; unavailable usage remains null.

## Claims

The four debate turns are separate calls. Bear R1 reads Bull R1, Bull R2 reads
Bear R1, and Bear R2 reads Bull R2. The validator requires a response to an
actual opposing claim when one is present.

The server assigns IDs such as `INV-BULL-001`, `INV-BEAR-001`,
`INV-BULL-101`, and `INV-BEAR-101`. Subsequent attempts use an attempt suffix
so previously persisted claims are not overwritten. Risk claims use their
own role-specific prefixes.

The independent Resolver must address all supplied claims, using ACCEPTED,
PARTIALLY_ACCEPTED, REJECTED or UNRESOLVED. It changes only resolution status;
authorship, statement, evidence, targets and originating node remain intact.
Original claim artifacts and separate resolution artifacts remain available.

## Isolation and Failure

The run-scoped coordinator alone aggregates results and checkpoints. Each
worker opens its own SQLAlchemy Session, recorder and NodeExecutor context.
A run-local RLock serializes short audit transactions, never an entire node
or model request. Attempt IDs and artifact ownership stay node-local.

- Analyst IMPORTANT failure: preserve failed attempts, expose status and
  warning, degrade quality. Missing Market or multiple important analysts
  reaches D and blocks downstream debate/advice; one other failure reaches C
  and prohibits risk increases.
- Lockup OPTIONAL failure: SKIPPED with warning, normally grade B. Resume
  preserves this terminal skip, including its failure classification.
- Debate turns, Resolver, Synthesis and required managers are MANDATORY:
  terminal failure stops admission of dependent work.
- Risk-agent IMPORTANT failure: successful siblings are retained; Synthesis
  receives failures explicitly and the final gate prohibits risk increases.
- In-flight siblings drain and persist their own results. An IMPORTANT or
  OPTIONAL model failure does not cancel other siblings.
- 429, timeout and connection failures use existing transient classification.
  Structured and transport retry budgets remain distinct from node attempts.

Quality Gate consumes agent_results, agent_statuses, agent_failures and
data_gaps. Report length, checklist, table and missing-data checks can further
degrade model-supplied grades. A completed phase never implies every agent
succeeded.

## Retry, Resume and Cancellation

Every node retries only itself. A manual Resume receives a bounded retry
budget while lifetime attempt_no keeps increasing. The coordinator records
AGENT_PROGRESS checkpoints after durable worker results. Resume merges those
checkpoints with committed node state, including siblings completed just
before an interruption.

Resume restores normalized phase outputs, not merely the earlier raw model
JSON. The effective Trader proposal is separately persisted after risk
revision so downstream hashes remain stable.

Changing confirmed holdings, mode, checkpoint, model identity/parameters,
Skill, prompt contract, or parameter lineage rejects Resume. Changed
downstream inputs cannot reuse incompatible completed results. Create a new
job/run when research inputs must change. `force_restart=true` does not bypass
the frozen evidence contract or delete previous attempts and claims.

Cancellation stops admission and signals cooperative model cancellation at
node, retry and stream boundaries. Already completed results remain durable.
Blocking HTTP reads may take until their configured timeout to exit; Python
threads are not forcibly killed. Manual retry returns 409 while the local
old worker is still active. User-cancelled jobs are not automatically resumed.
Service shutdown is recorded as INTERRUPTED rather than as user cancellation.

## Deterministic Authority

Portfolio Manager produces a draft, never an executable authority. The final
portfolio gate still owns data-quality blocking, T+1/available quantities,
hard caps, prices, suspension/limit checks and cash constraints.

This stage tightens quantity consistency: blocked actions clear quantity and
proposed_qty; sells respect available balances and whole-lot rules, including
the remaining odd balance; adds are rounded to lots and share one cash budget.
A supplied server-owned target-exposure bound is honored. No universal
exposure percentage or cash floor is invented where the Skill has none.

Candidate Engine thresholds and stages are not relaxed. Candidate lists remain
0-3, held-symbol additions are excluded, and candidates cannot bypass the
portfolio gate. NO_ACTION with an empty candidate_actions list is normal
successful execution. Candidate probe weights remain simulations, not orders.

## Configuration and Compatibility

```text
TRUE_MULTI_AGENT_WORKFLOW_ENABLED=true
ANALYSIS_MAX_PARALLEL_AGENTS=3
```

Parallelism is conservatively clamped to 1-8. Existing model settings are
reused; no per-role configuration is required.

The default workflow_version is `v3-core-3`. Explicitly disabling the feature
runs the preserved `v3-core-2` compatibility path and records
`legacy_fallback_used=true`. There is no silent fallback. An existing run
cannot change workflow versions during Resume.

The existing analysis API and audit endpoints remain compatible. Audit adds
evidence identity, fallback metadata and parent_claim_id. UI work is limited
to automated tests, not application redesign.

## Migration

No new migration or table is required. Existing AnalysisRun JSON and the
CORE-1/CORE-2 Stage, Node, Attempt, Artifact and Claim columns hold the added
metadata. Alembic remains at `20260905_0022`. Existing history remains readable.

## Validation

Focused coverage includes barrier-proven analyst/risk concurrency; separate
sessions and artifact ownership; single-node timeout/429/context retry;
IMPORTANT/OPTIONAL failure; actual alternating claims and independent
resolution; early and late Resume; one Trader Revision; cancellation and
shutdown; changed-input rejection; stale final quotes; mode call counts;
NO_ACTION; and malicious hard-cap/cash/T+1/lot/price drafts.

Browser acceptance uses individual deterministic provider responses for each
agent. Its malformed-output test runs Deep, not Fast, and verifies independent
audit nodes, structured retries and absence of legacy nodes. The real Deep
seed may legitimately produce zero candidates; tests do not force a candidate
past the production thresholds.

Final local and exact-head CI results are recorded in the delivery report.
Local diagnostics under `output/core3-validation` and
`output/playwright/acceptance` are ignored and must not be committed.

## Not Verified

- Real broker screenshots, live intraday data and long-term provider stability.
- Production Resume, distributed worker fencing and production load.
- Manual UI acceptance and mobile devices.
- ZIP/debug artifact export and actual investment returns.

Residual risks include provider throttling, long blocking HTTP reads, and
conservative data-quality blocking when required real evidence is absent.
Automated acceptance is not proof of profitability or execution readiness.

## Follow-up

V3-CORE-4: Artifact Export / Standard + Debug ZIP. Then continue the separate
Market and UI phases. This change does not include Quasar migration, dashboard,
holdings or analysis-center redesign, market detail UI, real trading, or
automated live execution.
