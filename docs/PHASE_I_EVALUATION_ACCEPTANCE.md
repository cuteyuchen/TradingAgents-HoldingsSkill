# Phase I Evaluation Acceptance

## Scope

- Branch: `codex/phase-i-decision-evaluation`
- Base commit: `26e3907dc0bd55aedecea0509826dab7fa020d5f`
- Decision Contract: `2.4.0`
- Evaluation Schema: `1.0.0`
- Migrations: `20260827_0015_decision_evaluation` (after `20260826_0014`) and the linear Phase H lease hotfix `20260827_0016_operational_leases`
- Alembic head: `20260827_0016`

## Architecture

Phase I adds append-only `DecisionEpisode`, `EvaluationSnapshot`, `DecisionEvaluationOutcome`, `CandidateEvaluation`, `TriggerEvaluation`, `EvaluationRun`, `PaperObservationRun`, and `PaperObservation` tables. Episodes freeze references to existing Analysis, Memory, Candidate, Trigger, Market, Portfolio, and Portfolio Gate facts; outcomes and observations are appended later and never become decision inputs.

Each snapshot stores source/snapshot identifiers, version, timestamp, `available_at`, payload metadata, and a SHA-256 content hash. Point-in-time validation blocks the run with `LOOKAHEAD_DETECTED` when `available_at > decision_time`; missing facts are labeled `INSUFFICIENT_HISTORICAL_EVIDENCE`. Historical replay defaults to `DENY_EXTERNAL_IO` and exposes `FACT_REPLAY`, `DETERMINISTIC_LOGIC_REPLAY`, and `MODEL_RECOMPUTE`. Current-model replay is labeled `RECOMPUTED_WITH_CURRENT_MODEL` and is excluded from historical metrics. LLM prompt/model metadata is not invented when old records do not contain it.

Forward observation uses the persisted A-share trading calendar for T+1/T+3/T+5/T+10/T+20. It records gross directional return, MFE, MAE, drawdown, price adjustment method, source references, and quality state. No transaction-cost model or benchmark mapping is fabricated; portfolio-level prices remain explicitly unavailable until a reliable portfolio valuation source exists.

Candidate stages remain separate from final decisions (`WATCHLIST`/`READY`/`ACTION` are not trade accuracy). Trigger evaluation measures analysis refresh and decision change, never trigger win rate. `NO_ACTION` remains a first-class outcome and is included in summary metrics. Paper Observation freezes only decisions captured on the real observation date; missing dates are `OBSERVATION_MISSING` / `MISSED_DECISION_CAPTURE`, never backfilled.

## Interfaces

Read-only endpoints:

- `GET /api/v3/portfolios/{id}/evaluation/summary`
- `GET /api/v3/portfolios/{id}/evaluation/episodes`
- `GET /api/v3/portfolios/{id}/evaluation/episodes/{episode_id}`
- `GET /api/v3/portfolios/{id}/evaluation/coverage`
- `GET /api/v3/portfolios/{id}/evaluation/paper-observation`

CLI: `python -m app.evaluation --user-id <id> --portfolio-id <id> <command>` with bounded replay, pending-outcome calculation, coverage, summary, and paper-status commands.

## Data quality and sample rules

Missing prices, calendars, snapshots, uncertain corporate actions, timestamp inversion, ownership mismatch, and missing horizons remain visible. Metrics always include `N` and are marked `INSUFFICIENT_SAMPLE` for N<5, `EARLY_EVIDENCE` for 5<=N<30, and `MATURE_SAMPLE` for N>=30.

## Verification

- Phase I focused tests: `7 passed`.
- Compile check: `python -m compileall -q app` passed.
- Frontend typecheck/build and full regression are run in the release checklist; environment-specific failures are recorded below.

## Limitations and conclusion codes

This phase does not implement auto trading, broker execution, optimizer, parameter search, dynamic risk budget, or automatic strategy mutation. Current evidence is infrastructure-level and observational; stable profitability is not evaluated.

- `PASS_PHASE_I_EVALUATION_FOUNDATION`
- `HISTORICAL_REPLAY_SUPPORTED` (for episodes with complete frozen evidence)
- `PASS_POINT_IN_TIME_INTEGRITY`
- `PASS_PAPER_OBSERVATION_READY` (when a same-day Decision is captured)
- `NOT_EVALUATED_FOR_STABLE_PROFITABILITY`
