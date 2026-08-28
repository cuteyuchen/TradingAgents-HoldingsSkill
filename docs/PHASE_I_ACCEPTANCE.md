# Phase I Historical Research Acceptance

## 1. Release Identity

- Baseline: `59957bf` (Phase I architecture baseline).
- Branch: `codex/phase-i-1-integrity-hardening`.
- Runtime Contract / Decision Contract: `2.4.0`, unchanged.
- Alembic chain: `20260826_0014 -> 20260826_0015 -> 20260827_0016 -> 20260827_0017`.
- Current head: `20260827_0017` (`backtest_calibration`).
- Published migrations `0014`, `0015`, and `0016` were not edited.

## 2. Research Namespace

Phase I adds only research-owned persistence:

- `backtest_runs`: immutable, reproducible research run metadata and evidence.
- `backtest_metric_slices`: aggregate metrics; no per-security-per-day observation table.
- `calibration_reports`: human-review proposals with no Apply operation.

Backtest output never enters `DecisionMemory`, `DecisionOutcome`, `TradeLedgerEntry`, or `PortfolioSnapshot`. Simulated execution is always labelled `NEXT_OPEN_PROXY`; it is not a confirmed broker fill.

## 3. Availability and Point-in-Time Contract

`build_replay_availability_manifest()` audits the current database without network access and emits row count, distinct trade dates, earliest/latest supported timestamp, coverage, source fields, availability field, lineage field, capability map, data hash, and known limitations. Earliest/latest values are calculated from the stored rows; missing history is reported as `null`, never backfilled.

Current schema capabilities are intentionally conservative:

| Source | Supported result |
|---|---|
| TradingCalendar | `FULL` when present; trading-day horizons use it, not natural days |
| MarketScoreSnapshot / MarketMetricSnapshot | Persisted production replay, with range coverage shown |
| AllAMedianIndexDaily | Persisted daily benchmark only; missing rows become `DATA_GAP` |
| DailyBarCache | `BAR_ONLY_DIAGNOSTIC` full capability; production replay is partial |
| SecurityMaster lifecycle / historical universe | `LEAKAGE_BLOCKED`; current status is not used as historical status |
| CandidateRun / CandidateScore | Partial, censored production top-subset; not full-universe calibration |
| PortfolioSnapshot / PortfolioRiskSnapshot | Confirmed portfolio-specific replay when rows exist |
| DecisionMemory / DecisionOutcome | Persisted memory replay with mature outcomes only |
| TradeLedgerEntry | Execution evidence only; never a simulated research source |
| Fundamentals / valuation / ETF constituents | `UNSUPPORTED` without publication-time historical tables |
| SourceLineage / industry classification | Partial or static proxy; never claimed as full PIT history |

`available_at` is used only as the stored visibility field. The manifest explicitly records that DailyBar ingestion time is not asserted to be source publication time. No ingestion timestamp is rewritten into an effective timestamp. Fundamental and valuation values without `report_period` plus `published_at/available_at` are blocked.

Replay validates timestamp ordering and rejects future-visible facts. It never uses current SecurityMaster, current fundamentals, current valuation, or current ETF constituents to reconstruct the past. Survivorship is surfaced as `CURRENT_UNIVERSE_ONLY` / `LEAKAGE_BLOCKED`.

## 4. Replay, Outcomes, and Metrics

- `PRODUCTION_REPLAY`: reads persisted Market, Candidate, Portfolio, and Memory facts as they existed at the historical point.
- `DETERMINISTIC_RECOMPUTE`: fail-closes until an explicit complete PIT preparation set exists; no LLM.
- `BAR_ONLY_DIAGNOSTIC`: evaluates pure bar factors and never claims a complete Candidate Engine backtest.
- Modes are kept separate in every run and metric slice.
- Market outcomes use All-A Median Index forward return and drawdown, with score buckets `0-20`, `21-40`, `41-60`, `61-80`, and `81-100`, plus regime breakdown.
- Market daily calibration uses a canonical close-window snapshot between `14:45` and `15:00`; morning or after-close snapshots are omitted, never promoted to a daily close observation.
- Candidate outcomes expose raw, benchmark, excess, directional, MFE, MAE, transaction-cost, quality, execution-basis, and reason-code fields.
- Candidate prices accept only explicit server/trusted quote ownership; model entry/trigger prices cannot become the reference price.
- Reference basis must match every path bar adjustment. RAW/QFQ mixing returns `PRICE_BASIS_MISMATCH` and no high-confidence return.
- Limit-up, limit-down, suspension, missing open, and missing target close are explicitly non-executable or blocked.
- Same-day/intraday paths exclude the same-day daily high/low and mark unavailable intraday benchmark evidence as degraded.
- Transaction costs snapshot the authoritative Phase E portfolio-ledger broker settings at run creation and reuse the same commission/minimum-commission/sell-tax calculation. No synthetic slippage is added: `slippage_bps` is `None`, `slippage_not_modeled` is true, and slippage is excluded from returns.

All replay source rows are bulk loaded by category. No day-by-security SQL loop or production write is used.

## 5. Chronology and Calibration

Chronological splits never shuffle dates. The final 63 trading dates are a single `GLOBAL_FINAL_HOLDOUT`; every walk-forward fold uses only earlier train/validation dates, and no train, validation, or selection row may reuse a global test date. Calibration selects local challengers from each fold Train, evaluates them on each fold Validation, aggregates validation evidence, fixes one final challenger, and only then reads the global holdout exactly once. Date-block bootstrap resamples whole trade dates so cross-sectional rows are not treated as independent observations. Every result exposes case count, trade-date count, coverage, confidence interval, baseline, challenger, fold directions, validation-isolation overlap lists, and known limitations.

Calibration supports Market Score/regime thresholds by replaying the production hysteresis state machine in date order (`previous regime -> today score -> production hysteresis transition -> simulated regime`), Candidate opportunity/entry/RR/Portfolio Fit/Decision Edge thresholds, factor ablation, one-factor weight perturbation, action frequency, robustness plateau, and baseline comparison. Market eligibility is never reduced to `score >= threshold`. Threshold variants rerun the complete production eligibility predicate with exactly one override; they are not single-field filters. Candidate selection reads train/validation only; the global holdout is evaluated after selection and cannot choose the challenger.

Calibration rebuilds the completed Run's replay rows and compares the current source IDs plus source-set hash against `data_manifest_json.frozen_source_ids` and `result_summary_json.source_lineage.source_set_hash`. Any mismatch fails closed with `CALIBRATION_SOURCE_SET_CHANGED` and no CalibrationReport is created, so a Calibration cannot silently consume replay sources that arrived after the Backtest was frozen.

Recommendations are limited to:

- `KEEP_CURRENT`
- `CONSIDER_CHANGE`
- `INSUFFICIENT_EVIDENCE`
- `REJECT_CHANGE`

Minimum sample and date requirements, validation/test degradation, tail risk, fold direction, fragile peaks, BacktestRun quality, availability-manifest capability, censored production samples, replay capability, and leakage status all fail closed. Factor ablation and weight perturbation are `DIAGNOSTIC_ONLY` and can never recommend `CONSIDER_CHANGE` until a full PIT universe permits complete downstream recomputation. Threshold changes that expand a censored candidate sample cannot recommend `CONSIDER_CHANGE`. The report never emits `OPTIMAL_PARAMETER` and cannot mutate any production configuration.

## 6. API and Worker Contract

Research endpoints are under `/api/v3/research`: availability, backtest list/detail/create, cancel, calibration list/detail/create. There is no public heartbeat endpoint; client heartbeats cannot represent worker liveness. Calibration POST accepts only a `backtest_run_id` and requires that Run to be `COMPLETED`; it never starts a Backtest synchronously. Endpoints use current-user ownership checks for Runs, Reports, and Portfolios. Client-supplied outcomes, returns, scores, source IDs, and historical rows are rejected; all outcome and lineage fields are server-owned.

Backtest POST creates a durable queued Run; a server-owned worker claims it with CAS, runs it outside the HTTP request, renews the lease with a server heartbeat tied to `attempt_count`, and persists completion/failure. Startup and scheduler ticks reclaim expired `RUNNING` Runs with `attempt_count` CAS fencing and redispatch the same Run without creating a second Run. Every durable worker write (stage, data hash, invalidate, `COMPLETED`/`INSUFFICIENT_DATA`, and `FAILED`) uses `WHERE attempt_count = generation` CAS. A stale worker that lost its lease cannot renew the new generation's lease, cannot write stages or final states, and its exception handler cannot mark the reclaimed Run `FAILED`.

## 7. Frontend and Skill

`/research` provides Data Availability, Backtest Run, Evidence, and Calibration Report views. It contains export/cancel controls but no Apply button. The page states that historical research is for research and calibration and does not represent future returns.

The Skill keeps live analysis unchanged. Historical research is offline system evaluation, not Alpha Memory retrieval, not live Decision context, and not a promise of learning or stable profitability. Research metrics are never injected into the live prompt automatically.

## 8. Verification

- Phase I + Phase I.1 + Final Seal research tests: `32 passed`.
- Full backend suite: `291 passed`.
- `compileall`: passed.
- Frontend `npm run typecheck`: passed.
- Frontend `npm run build`: passed.
- Fresh Alembic upgrade and deployed `0015 -> head` upgrade: passed; repeated head upgrades are idempotent.
- `git diff --check`: passed before final metadata cleanup.

## 9. Explicitly Not Implemented

Auto factor learning, auto threshold application, strategy optimization, reinforcement learning, auto trading, broker execution, dynamic risk budgets, optimal position sizing, full ETF historical look-through, and full historical fundamental-provider replay remain out of scope. Production parameter mutation requires a separate explicitly approved governance phase.

Current data limitations remain visible: no effective-dated SecurityMaster lifecycle, no historical ST/suspension/delisting universe, no PIT fundamentals/valuation, no historical ETF constituents, and Candidate production snapshots are censored top-subsets.
