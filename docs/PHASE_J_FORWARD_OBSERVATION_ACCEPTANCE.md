# Phase J Forward Observation Acceptance

## Baseline

- Branch: `codex/phase-j-forward-observation`
- Base: `76f4fbc` (Phase I final)
- Decision Contract: `2.4.0`
- Evaluation Schema: `1.0.0`
- Migration: `20260827_0017_forward_observation`
- Final SHA: `5033d34`

## Scope

Phase J adds a bounded evidence campaign for real-time advisory observation. It does not change the Decision Contract, Candidate stages, Trigger meaning, Portfolio Gate authority, or `NO_ACTION` semantics. It does not add broker execution, auto-trading, optimization, parameter search, dynamic risk budgets, new factors, threshold mutation, or an LLM report.

## Data Model

- `ObservationCampaign`: portfolio-scoped window with lifecycle `PLANNED`, `ACTIVE`, `PAUSED`, `COMPLETED`, `BLOCKED`, version/config metadata, and coverage counters.
- `DailyObservationCoverage`: one immutable-by-day projection per Campaign and trading date.
- `DailyEvidenceSeal`: append-only daily digest containing episode IDs, manifest hashes, coverage hash, contract/schema versions, and evidence SHA-256.

Historical replay modes are isolated from Campaigns. Only `REAL_TIME_PAPER_OBSERVATION` is included in forward queries.

## Daily Processing

After the existing 15:30 Daily Review maintenance, the scheduler invokes deterministic Campaign close processing. It records coverage, creates an idempotent daily seal, and matures only horizons whose persisted A-share trading dates have arrived. No new LLM or external evaluation call is made.

Coverage is `COMPLETE` only when a frozen Episode, valid Snapshot Manifest, hash validation, and Paper Observation are present. Missing capture is recorded as `MISSED_DECISION_CAPTURE`; a later run cannot manufacture a historical real-time Decision.

## Integrity and Outcomes

`EpisodeIntegrityAuditor` checks freeze state, ownership, Analysis/Decision/Trigger references, source cutoff, versions, Candidate stage, snapshot hash, `available_at` lookahead, and exclusion of Outcome inputs. Hash or lookahead failures block evidence; hashes are never repaired in place.

Outcome maturity uses T+1/T+3/T+5/T+10/T+20 trading days and explicit states for pending, computed, missing market data, uncertain corporate actions, and blocked data quality. Late market data may complete an existing horizon. Corporate-action uncertainty remains excluded from high-confidence aggregates.

## Interfaces

Read-only API projections include Campaign list/detail, coverage, integrity, and `source: FORWARD_ONLY` summary. Explicit POST operations are available for create, start, pause, resume, complete, daily seal, and maturity maintenance. CLI equivalents are exposed through `python -m app.evaluation`.

The Evaluation view remains read-only and now displays Campaign status, coverage, integrity failures, forward decision distribution, `NO_ACTION`, Candidate funnel, Trigger effectiveness, forward outcomes, and sample maturity separately from historical replay.

## Verification Checklist

- Phase J focused tests: `5 passed`
- Phase I focused tests: `8 passed`
- Phase H daily operations tests: `32 passed`
- Python compileall: passed
- Frontend `vue-tsc --noEmit`: passed
- Runtime JSON validation: passed
- Full backend, Alembic clean upgrade/twice-upgrade, frontend build, Docker build, and skill validator: run in release environment before merge

## Canary and Limitations

This phase provides the engineering foundation for a canary Campaign. Current real forward sample count and completed T+20 count are environment data, not fabricated by development tests. No stable profitability or strategy validation conclusion is allowed until sufficient real forward evidence exists.

Allowed conclusion codes:

- `PASS_PHASE_J_FORWARD_OBSERVATION_FOUNDATION`
- `PASS_FORWARD_EVIDENCE_INTEGRITY`
- `FORWARD_OBSERVATION_NOT_STARTED`
- `FORWARD_OBSERVATION_ACTIVE`
- `INSUFFICIENT_FORWARD_SAMPLE`
- `NOT_EVALUATED_FOR_STABLE_PROFITABILITY`
