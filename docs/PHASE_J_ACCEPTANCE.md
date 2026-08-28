# Phase J Parameter Governance Acceptance

## 1. Release Identity

- Baseline: `b92c3452f357f012428e876af049734bcea9ce2d` (Phase I formally PASS).
- Branch: `codex/phase-j-parameter-governance`.
- Runtime Contract / Decision Contract: `2.4.0`, unchanged.
- Alembic chain: `... -> 20260827_0017 -> 20260828_0018`.
- Current head: `20260828_0018` (`parameter_governance`).
- Published migrations `0014` through `0017` were not edited.

## 2. Governance Model

Phase J turns Phase I `CalibrationReport` evidence into a versioned manual
change pipeline:

```text
Offline Research: Historical Facts -> PIT Replay -> Backtest -> CalibrationReport
Governance:       CalibrationReport -> ParameterChangeProposal
                  -> Human Review -> APPROVED ParameterSetVersion
                  -> Deterministic Validation -> Explicit Manual Activation
                  -> ACTIVE Production Parameter Set
Rollback:         Historical Version Snapshot -> Rollback Proposal
                  -> Human Review -> New Version -> Activate
```

New persistence:

- `parameter_set_versions`: immutable complete snapshots with version, status,
  parent, proposal source, config hash, contracts, activation and rollback
  metadata. One ACTIVE version at a time; old rows are never reactivated.
- `parameter_change_proposals`: `CALIBRATION_REPORT` / `MANUAL` / `ROLLBACK`
  sources with locked base version, current/proposed values, evidence, risk,
  validation, reason, and review audit fields.
- `parameter_governance_events`: append-only audit timeline with no update or
  delete path.

Production lineage columns were appended to `MarketScoreSnapshot`,
`CandidateRun`, `AnalysisRun`, `DecisionMemory`, and `BacktestRun`. Historical
rows without governance lineage remain `LEGACY_PRE_GOVERNANCE`; no historical
row is retroactively rewritten.

## 3. Parameter Registry and Resolver

`backend/app/governance/registry.py` is the single registry with stable
business keys (not Python module paths):

- `CALIBRATABLE`: Market regime bounds/hysteresis, Candidate Watch/Ready/Action
  thresholds, RR, Decision Edge, NO_ACTION thresholds.
- `PROTECTED`: Watchlist/Ready/Action caps, stock 20% and sector/theme ETF 30%
  hard caps, held-exclusion, data-quality fail-close, no-auto-trade, weight
  families.
- `OPERATIONAL`, `EXTERNAL`, `DERIVED`: classified, not part of investment
  parameter governance.

The resolver is the single production authority:

- `get_active_parameter_set()`: active immutable snapshot, rejects two ACTIVE
  rows with `MULTIPLE_ACTIVE_PARAMETER_SETS`.
- `resolve_production_parameters()`: active snapshot or
  `LEGACY_PRE_GOVERNANCE` before first bootstrap; 15-second TTL cache,
  invalidated on activation.
- `bootstrap_parameter_set()`: idempotent ACTIVE v1 from the exact Phase J
  baseline constants; history without ACTIVE is `BLOCKED`, never guessed.

Market Engine, Candidate Engine, Analysis Engine, Decision Memory, and Backtest
creation resolve the snapshot once at run start and keep it for the whole run.

## 4. Proposal and Activation Contract

- Only `CONSIDER_CHANGE` can create a standard proposal. `KEEP_CURRENT`,
  `INSUFFICIENT_EVIDENCE`, `REJECT_CHANGE`, factor ablation, and weight
  perturbation cannot create a standard proposal.
- Proposals lock `base_parameter_set_version_id` at creation. Approval fails
  with `STALE_BASE_VERSION` when the ACTIVE version changed; no automatic
  rebase.
- Approval creates an immutable `APPROVED` version and leaves the current
  ACTIVE untouched. Activation is a separate explicit step with
  `expected_active_version_id` concurrency protection.
- Pre-activation validation deterministically checks registry type/range,
  protected invariants, Candidate gate ordering, Market boundary ordering,
  hysteresis consistency, NO_ACTION ordering, and contract versions. Any
  `BLOCKED` prevents activation.
- A-share trading-session activation is blocked by default
  (`BLOCKED_TRADING_SESSION`). `emergency_override=true` requires an
  authenticated actor, an explicit reason, and an audit event.
- Activation is one short transaction: old ACTIVE -> `SUPERSEDED`, new
  APPROVED -> ACTIVE, timestamps written, cache invalidated.
- Rollback creates a new version whose snapshot equals the target snapshot and
  records `rollback_from_version_id`; the target row is never reactivated.

## 5. Production Wiring

- `MarketEngine` uses governance lower bounds/hysteresis and writes lineage.
- `scan_candidates()` builds `CandidateConfig` from the ACTIVE snapshot and
  writes lineage.
- `run_analysis_job()` freezes one parameter context per job; candidate
  sub-steps reuse it and `AnalysisRun` records it.
- `capture_decision_memory()` inherits lineage from its `AnalysisRun`.
- `create_backtest_run()` freezes ACTIVE version, hash, and full snapshot;
  later activation cannot change the frozen run.
- `current_production_config()` returns the ACTIVE governance snapshot when
  present and re-raises `GovernanceBlockedError` instead of silently falling
  back.
- Dashboard Data Health adds a Parameter Governance component with
  `OK` / `DEGRADED` / `BLOCKED` states (`NO_ACTIVE_PARAMETER_SET`,
  `MULTIPLE_ACTIVE_PARAMETER_SETS`, `CONFIG_HASH_MISMATCH`,
  `PARAMETER_DEFAULT_DRIFT`). DB ACTIVE remains authoritative on default drift.

## 6. API

`/api/v3/governance` exposes parameter registry, parameter-set list/detail/active,
proposal list/detail, from-calibration, manual, submit, approve, reject,
validate, activate, rollback-proposal, events, and health. There is no
`PATCH /active-config`, no direct constant writer, and no auto-trade path.

## 7. Frontend

`/governance` shows the current ACTIVE version, CONSIDER_CHANGE calibration
suggestions, pending proposals with old/new values and base version, validation,
separate approve/activate steps, version history, rollback proposal creation,
and the append-only audit timeline. Buttons are named Create Proposal /
Approve / Activate; there is no Apply button.

## 8. Skill

`runtime.json` and `SKILL.md` now state that calibration is evidence only,
approval and activation are separate, only an explicitly activated version
changes future production behavior, the model cannot approve/activate/rollback,
rollback creates a new audited version, and no stable profitability is claimed.
Contract remains `2.4.0`.

## 9. Verification

- Governance tests: `16 passed`.
- Full backend suite: `307 passed`.
- `compileall`: passed.
- Frontend `npm run typecheck`: passed.
- Frontend `npm run build`: passed.
- Alembic fresh -> head, `0017 -> head`, head -> head: passed.
- `git diff --check`: passed.

## 10. Explicitly Not Implemented

Auto parameter application, auto factor learning, auto threshold learning,
reinforcement learning, strategy optimizer, automatic rollback by performance,
auto trading, broker execution, dynamic risk budget, optimal position sizing,
full PIT fundamental provider, full historical ETF constituents, and full
enterprise multi-person approval remain out of scope.
