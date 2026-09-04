# Fuyao Integration Acceptance

Phase O.2 acceptance scope: production financial-data provider integration and
evidence/context analytics expansion. This document is not an authorization to
enter Phase P, place trades, or change the frozen decision algorithms.

## Runtime contract

- Provider order: all-A `fuyao -> eastmoney_batch -> tencent`; critical quotes
  `fuyao -> tencent -> eastmoney_batch`.
- Fuyao authentication is server-side `X-api-key`; no API key is returned by
  an endpoint, persisted in a business table, or written to diagnostics,
  browser storage, prompts, screenshots, or artifacts.
- Only an HTTP response with `code == 0` is successful. The client records the
  endpoint, latency, attempt count, and sanitized `request_id`.
- Business errors map to `NON_RETRYABLE`, `PERMISSION`, `DATA_MISSING`,
  `RATE_LIMIT`, `UPSTREAM_FAILURE`, or `RETRYABLE`; retry is bounded and
  rate-limit aware.
- An absent key is a degraded configuration, not a startup failure. Existing
  fallback providers remain available and Fuyao is never reported healthy just
  because a portfolio exists.

## Acceptance matrix

| Gate | Expected evidence |
|---|---|
| `FUYAO_PROVIDER` | Client, adapters, aliases, request lineage and contract tests |
| `CORE_QUOTE_ROUTING` | Fuyao-first all-A and critical chains with batch/pagination |
| `CALENDAR` | Fuyao sync into local persisted calendar; scheduler reads local data |
| `HISTORICAL_CORE` | REST K-line and dump bootstrap reuse existing daily-bar foundation |
| `DATA_QUALITY` | freshness, coverage, missing-aware behavior, conflict and fallback tests |
| `PIT_SAFETY` | availability cutoff preserved; finance/valuation PIT remains unknown |
| `EXPANDED_ANALYTICS` | deterministic market brief, fundamental, valuation and contribution tests |
| `BACKEND_REGRESSION` | existing suite retained plus Fuyao tests |
| `FRONTEND_ACCEPTANCE` | typecheck, build and Playwright acceptance |
| `DOCKER` | build, startup, health, login and old/new route smoke without a key |
| `EXACT_HEAD_CI` | backend, frontend, frontend-acceptance and docker for final SHA |

## Frozen boundaries

- Market Score weights and formulas are unchanged.
- All-A Median remains the eligible-universe daily-return median compounded
  from 1000.
- Top5 concentration remains `ceil(N * 0.05)` turnover divided by eligible
  turnover, with numerator, denominator, `N`, and `top_count` retained.
- Candidate Score, Opportunity, Entry, R/R, Fit, Decision Edge, Candidate
  funding, Portfolio Gate, `NO_ACTION`, and Shadow fill semantics are unchanged.
- Fuyao industry, hot-list, anomaly, dragon-tiger, financial, and valuation
  fields are context/evidence only.

## Smoke policy

Contract tests use mocked HTTP and never require a secret. A real Fuyao smoke is
optional and runs only when `FUYAO_API_KEY` is already present in the local
environment; the key must never be sent through chat. Without it, report
`REAL_FUYAO_SMOKE = NOT_RUN / API_KEY_NOT_CONFIGURED` and do not substitute a
fixture for real evidence.

## Readiness policy

Automated implementation and frontend acceptance may pass while
`MANUAL_UAT = REQUIRED`. Live readiness remains `NOT_READY` until authoritative
provider observation, market snapshot/refresh, confirmed portfolio, analysis,
future quote observation, and verified backup gates are actually observed. This
phase ends at `PHASE_O_FINAL = HOLD_FOR_MANUAL_UAT_AND_LIVE_READINESS` unless
those facts are later established by the owner.
