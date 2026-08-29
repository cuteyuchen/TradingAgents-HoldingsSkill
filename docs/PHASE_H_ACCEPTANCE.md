# Phase H.1 Operational Acceptance

## 1. Release identity

- Branch: `codex/phase-h-daily-workbench`
- Phase H baseline commit: `259ab84` (`feat: add phase h daily operating workbench`)
- Required historical baseline: `e18e4ad` is an ancestor of the branch.
- H.1 / Final Seal scope: operational hardening, migration continuity, and
  restart-safe worker recovery only.
- Decision Contract: `2.4.0`, unchanged.

## 2. Changed files in H.1

- `backend/app/operations/models.py`: durable checkpoint claims and notification records.
- `backend/app/operations/workflow.py`: database-backed checkpoint ownership and claim outcomes.
- `backend/app/operations/notifications.py`: durable notification claim, retry, cooldown, read model, and read state.
- `backend/app/operations/dashboard.py`: section-level `ERROR` projection for partial failures.
- `backend/app/memory/review.py`: review lease lifecycle and atomic SQL refresh counter update.
- `backend/app/services/scheduler.py`: review lease reclaim and bidirectional fixed-checkpoint deduplication.
- `backend/app/config.py` and `backend/app/operations/config.py`: bounded claim leases.
- `backend/alembic/versions/20260826_0014_review_refresh_metadata.py`: published review metadata migration, unchanged in responsibility.
- `backend/alembic/versions/20260826_0015_operational_claims.py`: published durable checkpoint and notification claim migration, restored as a standalone revision.
- `backend/alembic/versions/20260827_0016_operational_leases.py`: lease columns and indexes appended after the published Phase H head.
- `backend/app/services/analysis_lease.py`: worker heartbeat, lease renewal, and stale running-job recovery.
- `backend/tests/test_daily_operations.py`: concurrency, boundary, restart, heartbeat, running-crash, read-only, partial-failure, and notification retry coverage.
- `backend/tests/test_phase_h_migrations.py`: deployed `0015 -> 0016` upgrade and repeated-head regression.
- `skill/tradingagents-holdings-advisor/runtime.json`: complete notification severity contract.

## 3. Checkpoint idempotency

`daily_operational_checkpoints` has a database unique constraint on
`(portfolio_id, trade_date, checkpoint_name, workflow_version)`. The workflow
claims a row inside a savepoint before running a fixed checkpoint. A uniqueness
collision is resolved as an already-owned checkpoint and surfaced as
`REUSED`/`CHECKPOINT_ALREADY_CLAIMED`; it does not abort the outer scheduler
transaction. The existing `AnalysisJob.idempotency_key` remains the execution
job guard.

Checkpoint semantics use `Asia/Shanghai` and the configured 15-minute window:
exact time, +14:59, and +15:00 are due; +15:01 is `MISSED`. A persisted
terminal state always wins over a later missed-window calculation. No whole-day
replay is performed, and claims survive service restart. Non-terminal claims
carry a bounded lease; expired claims are reclaimed in place and increment
`attempt_count`. A checkpoint `completed_at` is written only for terminal
statuses. Checkpoint-backed AnalysisJobs renew their lease from a separate
worker heartbeat. A live heartbeat prevents reclaim beyond the initial lease;
when a worker crashes, the stale claim atomically moves the same running job to
`retrying`, and the same job id is re-dispatched without creating a second job.

## 4. Trading calendar and monitor

Scheduler, workflow, and Dashboard reuse `TradingCalendarService` and persisted
CN calendar rows. Missing calendar data fails closed. Non-trading days do not
run checkpoints and actively stop a leftover monitor. During a trading day the
monitor resumes in morning/afternoon sessions, pauses at lunch, and stops at
15:00. The existing process-wide monitor single-flight lock prevents duplicate
polling in one service process.

## 5. Review stale refresh

Late mature Outcomes, revised Outcome sources, and execution revisions mark the
same `DailyReviewRun` stale. Refresh is in place and preserves the ReviewRun id.
`refresh_count` is incremented with an atomic SQL expression so concurrent
refreshers cannot lose increments. Review metadata records stale reasons,
source marker, detection/mark time, refresh time, and count in the existing
review and operational state JSON.

## 6. Dashboard read-only proof

The three Dashboard APIs remain read-only projections. Tests monkeypatch
analysis, candidate scan, market calculation, review, and notification dispatch
side effects and prove that a Dashboard GET invokes none of them. MISSING and
STALE sections are rendered as state only; no read-through refresh occurs.

Each section is isolated. Market, Candidate, Analysis, and Notification errors
return `status=ERROR` for that section while unrelated sections continue;
error text is bounded and no stack trace or secret is returned. Ownership is
always derived from the authenticated user plus portfolio id.

Freshness remains server-owned: `FRESH`, `STALE`, `FROZEN`, and `MISSING` are
derived from persisted timestamps, TTLs, quality, and lifecycle state. A stale
Candidate snapshot cannot be displayed as actionable `ACTION`.

## 7. Durable notifications

`operating_notifications` persists one material event per
`(user_id, portfolio_id, dedupe_key)` and a stable notification id. Dispatch is
at-least-once with durable claim states: `DISPATCHING`, `SENT`,
`DASHBOARD_ONLY`, `FAILED`, `COOLDOWN`, and `RETRY`. A process restart cannot
forget dedupe state. Failed channel delivery is retryable after cooldown; an
expired `DISPATCHING` lease is reclaimed with a compare-and-set update, so
duplicate workers receive `ALREADY_CLAIMED` or `DEDUPED` while a crashed
worker can retry. Notification failure
does not roll back Analysis, Candidate, Monitor, Decision, or Ledger facts.

Severity is `INFO`, `IMPORTANT`, `ACTION_REQUIRED`, or `CRITICAL`. Regime changes, Candidate
promotion/demotion, confirmed P0/P1 Triggers, provider degradation/outage, and
explicit `NO_ACTION` resolutions are material events. 20:30 remains a
critical-event hook only. Candidate `ACTION` and Trigger confirmation never
become broker or final portfolio instructions.

## 8. Migration clean install

The published migration lineage is preserved as
`20260826_0014 -> 20260826_0015 -> 20260827_0016`. Revision `0014` owns only
review refresh metadata; revision `0015` owns the durable operational claim
tables; revision `0016` adds lease columns and indexes. A database initialized
at deployed revision `0015` upgrades to `head` (`0016`) and can repeat the
`head` upgrade safely. Existing local databases remain compatible via the
`create_all`/lightweight migration path.

## 9. Decision Contract regression

Existing contract tests continue to assert runtime synchronization with
`decision_contract_payload()` and version `2.4.0`. H.1 adds no Candidate,
Trigger, Portfolio Gate, or `NO_ACTION` semantics. WATCHLIST/READY/ACTION are
unchanged; `ACTION` is not a final buy recommendation, Trigger is a
re-analysis reason rather than a trading signal, and Portfolio Gate retains
final authority.

## 10. Verification

- Final Seal operations and migration tests: `35 passed` (including claim,
  boundary, restart, heartbeat, running-crash, and deployed-upgrade cases).
- Backend full suite: `259 passed, 16 warnings`.
- Backend `compileall`: passed.
- Skill tests: `13 passed`.
- Skill validator: passed (`Skill is valid!`).
- Frontend `npm run typecheck`: passed.
- Frontend `npm run build`: passed.
- Alembic deployed upgrade `20260826_0015 -> head` plus repeated `head`:
  passed; current head `20260827_0016`.
- `git diff --check`: passed; only repository line-ending warnings remain.
- Docker Compose build: passed.

Remaining warnings are existing Starlette/httpx deprecation notices, test JWT
key-length warnings, and the existing Vite large-chunk warning.

## 11. Explicitly out of scope

Auto Trading, Broker Execution, Dynamic Risk Budget, Optimal Position Sizing,
new Candidate Factors, Auto Factor Learning, Full Backtest, Reinforcement
Learning, Strategy Optimizer, Full ETF Look-Through, and multi-service
architecture remain unimplemented.

## 12. Conclusion

After the Final Seal commit, the acceptance code is:

`PASS_PHASE_H_FINAL_SEAL`

The final commit SHA and final clean working-tree state are recorded in the
delivery response.
