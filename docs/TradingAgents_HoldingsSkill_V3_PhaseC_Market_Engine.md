# TradingAgents-HoldingsSkill V3 Phase C Market Engine

## 1. Scope

Phase C adds a deterministic, shared A-share market-state engine on top of the
Phase B identity, calendar, quote, quality, provenance, and provider-health
contracts. It calculates market metrics, component scores, the aggregate
Market Score, confidence, quality state, and regime without asking an LLM to
perform numerical work.

The engine is read-only with respect to portfolios and trading decisions. It
does not create candidates, alter positions, execute trades, or invoke the V2
analysis workflow.

## 2. Configuration authority

The only authoritative numerical configuration is
`backend/app/market/engine/config.py`.

The following objects define the production contract:

- `MARKET_ENGINE_VERSION` and `UNIVERSE_RULE_VERSION`
- `COMPONENT_WEIGHTS`
- the component sub-weight mappings exported by that module
- coverage thresholds
- smoothing parameters
- regime thresholds and hysteresis parameters

This document deliberately does not repeat those numerical values. API
responses and persisted snapshots carry the applicable engine/config/universe
versions so historical results remain explainable after a configuration
change.

## 3. Data flow

1. `SecurityMaster` and `TradingCalendar` produce the eligible market universe.
2. The Phase B provider and quality layer supplies normalized current quotes.
3. The history access layer supplies `NormalizedDailyBar` rows. Business logic
   never reads provider-specific field names.
4. The universe service records included and excluded counts by reason.
5. Pure metric functions calculate cross-sectional and historical raw metrics.
6. Historical normalization converts eligible metrics to comparable scores.
7. Component scoring combines available normalized metrics using the single
   configuration authority.
8. State scoring applies available-component reweighting, coverage gates,
   confidence, smoothing, and regime hysteresis.
9. Metric, score, and median-index snapshots are persisted idempotently and
   exposed through authenticated market APIs.

Every calculation is bounded by its requested `trade_date` and `captured_at`.
Bars or quotes whose `available_at` is later than that boundary are excluded to
prevent look-ahead.

## 4. Market Score universe

The core universe contains active `STOCK` securities on `SSE` or `SZSE` only.
It excludes:

- ETF and BSE securities
- ST and `*ST` securities
- suspended securities
- delisting-board or delisted securities
- securities with fewer than 20 open trading days since listing
- securities without a canonical `SecurityMaster` identity

The listing-age rule uses `TradingCalendar`; it is not a natural-day
calculation. Each run persists `universe_total`, `included_count`,
`excluded_count`, exclusion-reason counts, and `universe_rule_version`.

Universe membership is fixed for a coherent calculation snapshot. Turnover
concentration, breadth, and coverage may not mix rows from another requested
universe or another `captured_at` batch.

## 5. Historical bars and metrics

`NormalizedDailyBar` is the sole daily-bar contract. It includes canonical
identity, OHLC and previous close, volume, amount, turnover rate, adjustment,
provider timestamps, availability time, and quality state.

Moving averages use 5/10/20/60/120/250 valid trading-day windows. A security
without enough history is omitted from that metric's denominator instead of
being treated as below its moving average. New-high/new-low metrics compare
today's close with the highest/lowest close in the preceding N valid trading
days. MA60 direction uses the configured recent endpoint/slope window.

The All-A median return is the median daily return of the eligible core
universe. The All-A Median Index starts at 1000 on its first valid date and
compounds the daily median return. Daily index writes are idempotent and allow
ordered historical recomputation.

Turnover concentration is calculated for Top 1/3/5/10/20 percent of the same
coherent universe, using `ceil(universe_count * ratio)` for group size.

Board-aware price-limit rules distinguish main-board and ChiNext/STAR limits.
They must not classify every security with a single percentage threshold.

## 6. Components and score

The engine produces seven directionally aligned component scores, where a
higher score means the market is more suitable for bearing risk:

- Breadth
- Trend
- Liquidity
- Profitability
- Diffusion
- Crowding
- Tail Risk (`100` means lower tail risk)

Each component stores raw metrics, normalized metrics, score, eligible
denominators, missing reasons, and quality. Component and subcomponent weights
come only from the mappings named in section 2.

Rolling historical percentile normalization supports multiple lookback windows
with three years as the preferred default. Positive metrics use their
percentile rank; inverse metrics use one minus that rank. Insufficient history
is explicit and lowers confidence; it is never filled with fabricated samples.

When a component is unavailable, the aggregate score renormalizes the weights
of available components. It does not score the missing component as zero.

## 7. Display state

The raw score is the deterministic weighted result. The display score applies
the configured exponential smoothing rule. Regime derives from the configured
five-band thresholds and uses hysteresis against the previous reliable regime
to prevent boundary oscillation.

Confidence reflects coverage, historical sample depth, component availability,
universe completeness, and metric quality. Confidence is not a substitute for
the score and is persisted separately.

Coverage gates have three outcomes defined by configuration:

- normal: calculate and publish the current state
- `DEGRADED`: calculate with reduced confidence and explicit quality flags
- `FROZEN`: return the previous reliable display score and regime with
  `is_frozen=true` and a machine-readable freeze reason

A provider outage therefore cannot manufacture a zero score or a false
`STRONG_RISK_OFF` state. If no reliable prior state exists, the API reports an
unavailable state rather than inventing one.

Deterministic `positive_drivers` and `negative_drivers` explain the strongest
configured signals. They are reason codes, not LLM-authored prose.

## 8. Persistence

Phase C persists compact aggregate facts rather than intraday rows for every
security:

- metric snapshots, including universe/coverage facts and component metric JSON
- score snapshots, including raw/display scores, regime, confidence, quality,
  frozen state, drivers, and version metadata
- daily All-A Median Index values

Quote snapshots and provider health continue to use the Phase B contracts.
Phase C does not create a second quote, quality, lineage, or circuit-breaker
implementation.

## 9. API contract

All endpoints require the existing V2 bearer authentication:

- `GET /api/v3/market/state`: latest persisted state
- `GET /api/v3/market/state/history`: score history filtered by date and limit
- `GET /api/v3/market/metrics`: latest complete metric snapshot
- `GET /api/v3/market/median-index`: ordered median-index history
- `POST /api/v3/market/calculate`: authenticated manual calculation trigger

Read endpoints never call upstream providers implicitly. `calculate` is the
only endpoint allowed to request a new calculation, and the engine service owns
idempotency for a coherent snapshot key. No one-minute scheduler or realtime
monitor is introduced in Phase C.

API output rounds display scores to one decimal and confidence to a meaningful
precision while retaining ratios as floats. It exposes calculation timestamps,
quality/frozen state, versions, component scores, core metrics, universe facts,
and deterministic drivers.

## 10. Explicit exclusions

Phase C does not implement Realtime Monitor, Trigger Engine, Fast Analysis,
Quant Candidate V3, Stock/ETF Opportunity Score, Portfolio Manager V3, Dynamic
Risk Budget, Position Sizing, Trade Ledger, ETF Look-Through, Alpha Memory,
broker integration, automatic trading, or a full backtest framework.



## 11. Unavailable metrics

Phase C marks a metric unavailable instead of inventing a value. The current
data foundation does not yet provide:

- industry classification, so Diffusion is unavailable until industry facts exist
- same-time historical market aggregates, so same-time turnover and projected
  full-day amount stay unavailable rather than using a linear 240-minute
  extrapolation
- yesterday-strong continuation membership
- major-index confirmation series

Unavailable components are omitted from the weighted score and lower
confidence. They are never scored as zero.

Daily bars use one forward-adjusted (`QFQ`) close series. A bar's default
`available_at` is the A-share session close (15:00 CST). Intraday calculations
therefore use completed history through the previous session plus the current
quote snapshot, not a still-forming daily bar.

## 12. API input policy

`POST /api/v3/market/calculate` may receive `trade_date`, `captured_at`,
`market_snapshot_id`, and `persist`. It does not accept client-owned
securities, quotes, history, calendar rows, coverage, fallback level, provider
endpoint, or expected count. Those facts are loaded from SecurityMaster,
TradingCalendar, the Phase B snapshot service, and the history access layer.
The service layer still accepts injected rows in tests.
