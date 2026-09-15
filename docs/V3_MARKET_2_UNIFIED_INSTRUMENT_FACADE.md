# V3-MARKET-2: Unified Instrument Market Facade

## Scope

Baseline: `60e620b8b88eefc887c658b14e2782bdd42e5446`.

MARKET-2 supplies one deterministic market-data contract for `STOCK`, `ETF`
and `INDEX`. It adds no database tables or migrations, model calls, agents,
strategies, trading execution, Portfolio Gate rules, or instrument detail UI.
Frontend changes are limited to API methods and TypeScript contracts.

## Architecture

```text
Analysis Engine / authenticated API clients
                |
                v
InstrumentMarketService
  |-- InstrumentIdentityResolver -> existing SecurityMaster
  |-- MarketSessionService -> existing TradingCalendar
  |-- InstrumentDataAdapters
  |     |-- existing critical quote chain / Provider Registry / health tracking
  |     |-- existing historical provider chain
  |     |-- Tencent five-level book adapter
  |     `-- existing Eastmoney fund-flow fetcher
  `-- existing market_snapshot_service runtime cache
```

`MarketFoundationService` continues to own market-wide aggregates.
`InstrumentMarketService` owns single/multiple-instrument facts and does not
load `latest_analysis` or depend on Analysis Engine.

Implementation locations:

- `backend/app/market/instrument_schemas.py`: public Pydantic contracts.
- `backend/app/market/instruments.py`: identity resolution, validation,
  session-aware quality, caching, snapshots and evidence.
- `backend/app/market/providers/instruments.py`: provider adaptation and units.
- `backend/app/routers/instruments_v3.py`: authenticated, typed HTTP endpoints.
- `backend/app/services/instrument_market_evidence.py`: legacy analysis projection.
- `frontend/src/api/types.ts` and `index.ts`: typed clients, no provider branching.

## Identity

SecurityMaster is the only identity authority. A provider response cannot
create an anonymous instrument. Unknown instruments return `404`; batch
responses retain an unavailable item with `INSTRUMENT_NOT_FOUND`.

Input normalization reuses `market.codes`: `600519`, `600519.SH`,
`SH600519` and other existing supported spellings are accepted. Output is
always canonical, for example `600519.SH`, `159915.SZ`, `000300.SH`.
Explicit exchange hints are preserved end to end. In particular,
`000001.SH` (index) and `000001.SZ` (stock) must never share a quote or cache key.

Identity includes `instrument_id`, `code`, `symbol`, `exchange`, `name`,
`instrument_type`, `board`, `currency`, `lot_size`, `is_st`, `is_suspended`
and `status`. Missing authoritative values remain `null`.

Existing master ingestion must provision the requested securities, including
indices. `upsert_security` now accepts `INDEX`; there is no quote-driven
auto-registration or new master-data registry.

## Capabilities

| Capability | STOCK | ETF | INDEX |
| --- | --- | --- | --- |
| quote | true | true | true |
| bars | true | true | true |
| order_book | true | true | false |
| capital_flow | true | provider-dependent, default false | false |
| fundamentals | true | false | false |
| etf_profile | false | true | false |

Capabilities also contain `bar_intervals` and supported `adjustments`.
They describe supported data surfaces, not a promise of current provider
availability. Fundamentals/ETF-profile capability does not introduce additional
endpoints in this stage. ETF capital flow is enabled only for an adapter
configuration with confirmed support (`etf_capital_flow=True`).

Consumers must read capabilities instead of inferring them from provider
names or security types.

## API

All endpoints use the existing authenticated-user dependency.

```http
GET /api/v3/market/instruments/600519.SH
GET /api/v3/market/instruments/600519.SH/quote
GET /api/v3/market/instruments/600519.SH/bars?interval=1d&limit=250&adjustment=forward
GET /api/v3/market/instruments/600519.SH/order-book
GET /api/v3/market/instruments/600519.SH/capital-flow
GET /api/v3/market/instruments/600519.SH/snapshot
POST /api/v3/market/instruments/quotes
Content-Type: application/json

{"codes":["600519.SH","159915.SZ","000300.SH"]}
```

There is no Dashboard-specific index bars endpoint. Stocks, ETFs and indices
use exactly the same paths and response models.

## Quote

`InstrumentQuoteResponse` includes identity and nullable `last`, `change`,
`change_pct`, `open`, `high`, `low`, `prev_close`, `volume`, `turnover`,
`amplitude_pct` and `turnover_rate`, plus the provenance fields below.

- Percentage values use percentage points: `1.23` means `1.23%`.
- Volume is shares; turnover is CNY. Zero is a real value, never "unknown".
- Missing/invalid optional numbers are `null`, never NaN or Infinity.
- Invalid or missing prices return `unavailable`, grade `F`.
- Known suspension returns `INSTRUMENT_SUSPENDED` without requesting a quote.
- Change/amplitude may be calculated from validated prices; no price is fabricated.

## Batch Quote

Requests accept 1 to 100 codes, with the limit applied before deduplication.
Aliases are deduplicated after master resolution. Items retain deterministic
first-occurrence order, including unknown instruments. Each item owns its
`status`, `quality`, `quality_flags` and `error_code`.

There is one master lookup for a batch and one quote-adapter call for all
uncached instruments, not one call per security.

- Tencent: one mixed stock/ETF/index HTTP batch for 20 instruments.
- Eastmoney: one targeted `ulist.np` batch, not a full-market scan.
- Fuyao: native batches grouped by its equity and index endpoints. Twenty
  equity instruments use one call; a mixed equity/index batch uses two native
  endpoint calls, never 20 individual requests.
- Fallback only requests the still-unresolved codes from subsequent providers.

Malformed/missing individual quotes do not discard usable sibling quotes.

## Bars And Adjustment

Supported intervals: `1d`, `1w`, `1M`. Minute intervals are not exposed.
Parameters: `interval`, `start`, `end`, `limit` and `adjustment`.
The default limit is 250; the maximum is 1000; start/end are inclusive dates.
Future data is not requested beyond the current session calendar date.

Adjustment vocabulary is exactly:

| Value | Meaning |
| --- | --- |
| none | Unadjusted |
| forward | Forward adjusted |
| backward | Backward adjusted |

Unsupported adjustment returns HTTP 200 with `status=unsupported` and
`ADJUSTMENT_UNSUPPORTED`; it never silently becomes `none`.
Indices support only `none`. Fuyao's ETF history route supports only `none`;
the existing Eastmoney history adapter can satisfy supported ETF adjustment
requests, with explicit fallback provenance.

Daily rows use `YYYY-MM-DD` and are sorted ascending. Validation checks:

- Positive, finite OHLC, with high at least max(open, close, low) and low at
  most min(open, close, high).
- Nonnegative volume and turnover when supplied.
- Unique, valid timestamps and the requested date bounds.
- Malformed rows are excluded and quality is downgraded.
- All rows sharing a duplicate date are excluded, not arbitrarily selected.

Weekly bars aggregate daily rows by ISO week; monthly bars by calendar month.
Open is the first open, close the last close, high/low the extrema, and
volume/turnover the sums. A missing component volume/turnover makes that sum
`null`. The bar date is the last included trading date. A requested partial
current week/month remains a partial period, not a completed-period claim.
For a start date inside a week/month, fetching begins at that period's
boundary; output is filtered by the resulting bar dates.

Each response uses exactly one provider (`mixed_sources=false`). Adjustment
mismatch and mixed-provider rows fail the provider attempt. An empty attempt
followed by a failure returns `unavailable`, not a successful empty history.

## Order Book

V1 exposes at most five bids and five asks:

- Bids: best first, price descending. Asks: best first, price ascending.
- Levels are numbered 1 through 5 after validation and sorting.
- All volumes, totals, inner/outer volumes and order difference use shares.
- Tencent lots are multiplied by the authoritative instrument lot size.
  Unknown lot size cannot silently become 100; affected volume stays `null`.
- Invalid/duplicate levels are excluded with quality flags.
- During continuous trading, best bid greater than best ask sets
  `CROSSED_ORDER_BOOK` and downgrades to at least `C`.
- A missing side or fewer than five valid levels is partial, not a fabricated
  five-level book.

Provider-supplied order ratio/difference are preserved. When calculated from
available book totals, `derived=true` and `derived_fields` identify the
calculated fields. No price-direction heuristic fabricates inner/outer volume.

Suspended/unreachable instruments return `unavailable`; a valid empty book
returns `empty`; index book capability returns `unsupported` without a request.
This is a retail five-level snapshot, not exchange Level-2 or tick-by-tick data.

## Capital Flow

Current fields: `main_net_inflow`, `super_large_net_inflow`,
`large_net_inflow`, `medium_net_inflow`, `small_net_inflow`.
Positive means net inflow; negative means net outflow; currency is CNY.
The adapter requests up to 20 daily observations and returns ascending history.
Missing fields remain `null`.

Every response explicitly sets:

```json
{"provider_derived":true,"methodology":"provider_defined","currency":"CNY"}
```

Main/large/small order classifications are provider-derived estimates, not
exchange-confirmed investor identities. The provider's undisclosed
classification algorithm is not represented as an exchange standard.
Available flow has grade no better than `B`, including
`PROVIDER_DERIVED_CLASSIFICATION`. Indices are unsupported; ETF support follows
the capability contract. Missing provider observation time remains `null`.

## Metadata

The metadata endpoint returns `identity`, `capabilities`, `metadata` and
provenance. It projects an allowlist of existing master-data fields:

- Stock: board, industry, concepts, list date, ST state, lot size, price-limit rule.
- ETF: category, underlying index, manager, expense ratio, tracking target.
- Index: publisher, base date/value and constituent count.

Unknown fields remain `null`; no provider payload is forwarded. Metadata
includes `available_for_trading` but never a holding's `available_qty`.
T+1 sellable quantity remains Portfolio/Holding state.

## Provenance And Time

`MarketDataProvenance` contains `source`, `provider`, `provider_profile`,
`fallback`, `observed_at`, `fetched_at`, `trading_date`, `data_basis` and quality.

- `observed_at` is the provider's actual data timestamp.
- `fetched_at` identifies the system's acquisition, not the market observation.
- Timestamps are timezone-aware. Daily bar dates are not invented intraday
  observation timestamps.
- When a provider only gives `HH:MM:SS`, the canonical Tencent path does not
  attach the fetch date. Quote observation/date remain unknown and are
  flagged. The old Tencent parser retains its legacy compatibility behavior.
- SecurityMaster metadata uses its stored source/update timestamps.
- A module skipped because of unsupported capability or suspension does not
  invent a successful acquisition timestamp.
- Provider profile is a digest of non-public routing configuration, not raw
  credentials or a provider URL.

### Data Basis And Freshness

The existing `MarketSessionService` and persisted calendar remain authoritative:

- `live`: intraday data, including the lunch break.
- `session_close`: confirmed same-day closing data.
- `previous_session_close`: a previous trading session's close.

During trading, old quotes become `stale` based on observation time (90 seconds),
not a recently renewed cache/fetch timestamp. During lunch the reference is
11:30. A valid 15:00 close does not become stale at 20:00 or over the weekend.
It becomes stale when a new live trading session requires newer data.
An intraday observation does not become a confirmed close just because it is
requested at night. Missing calendar facts explicitly degrade quality.

Historical bars describe their latest included date; older requested history
is not rejected merely because it is old.

## Quality, Fallback And Partial Availability

Statuses are `available`, `degraded`, `stale`, `unavailable`, `unsupported`,
`empty`; grades are `A`, `B`, `C`, `D`, `F`.
Every module has a status, grade and explicit quality flags.

Fallback returns the actual fallback provider, `fallback=true`,
`PROVIDER_FALLBACK`, and grade no better than `B`. Provider errors are represented
by stable internal codes, not exception text. Historical rows are never silently
mixed or served with a different adjustment.

Snapshot quality uses the shared `market.quality._worst_grade`, also used by
MARKET-1. `A + B + F` is `F`. Quote always participates; order book and flow
participate only when supported by capabilities. Unsupported index modules
therefore do not turn an otherwise usable index snapshot into grade `F`.

A supported module failure degrades the composite without discarding sibling
modules. Invalid book/flow containers return module-level `unavailable`;
malformed individual rows can be excluded while preserving valid rows.
`snapshot` is market-only: identity, capabilities, quote, book, flow, quality
and `as_of`.

## Cache

The facade extends the existing `market_snapshot_service` cache rather than
introducing a separate cache system. It is bounded to 2048 total entries,
lock-protected and copy-on-read/write. No new durable cache tables are required.

| Data | TTL |
| --- | --- |
| Quote | 4 seconds |
| Order book | 2 seconds |
| Capital flow | 45 seconds |
| Current live bars | 30 seconds |
| Closed/historical bars | 6 hours |
| Unavailable/empty results | 1 second |

Keys include database scope, instrument ID and identity version, data type,
session date/basis, provider profile, and all relevant query parameters.
Bars additionally include interval, adjustment, start, end and limit.
Different instruments or adjustments cannot share cached results.
Cached quote/book/flow freshness is reevaluated against the current session.
Cache is process-local; distributed cache coordination is not added in V1.

## Error Contract And Security

| Situation | HTTP | Result |
| --- | --- | --- |
| Unauthenticated | 401 | Existing auth contract |
| Unknown instrument | 404 | `INSTRUMENT_NOT_FOUND` |
| Invalid code/query/batch size | 422 | Validation error |
| Capability not supported | 200 | `unsupported` |
| Provider temporarily unavailable | 200 | `unavailable`, `F`, error code |
| Valid but empty module | 200 | `empty` |

Batch unknowns are per-item errors, not whole-request 404s.
Raw provider dictionaries, request headers, API keys, cookies, credentials,
password URLs and raw exception messages are not public contract fields.
Public Pydantic models forbid extra fields and non-finite numbers.
Sentinel tests cover nested raw metadata, credential URLs and provider errors.
The facade and adapters have no LLM/Agent dependencies.

## Analysis Evidence Integration

`InstrumentMarketService.evidence_snapshot` returns a freezeable
`v3-market-2.evidence.v1` document containing complete market-only snapshots and
typed bars with provenance. This is consumed by the existing CORE-3 evidence
artifact. No CORE-3 node ordering or retry policy changes are introduced.

`instrument_market_evidence.collect_market_snapshot` projects the facade into
the existing analysis keys and retains the canonical document under
`instrument_market`. Technical MA/trend/volume-ratio summaries are computed
from standard bars. News, announcements and sector context keep their existing
collection paths. Complete per-module quality remains in the canonical
document; the legacy optional-data gate is preserved.

The Shanghai index is separate from the stock with the same six-digit symbol.
If its authoritative master row is absent, no anonymous index is created.
The legacy analysis projection still uses its established symbol keys; new
consumers should use the canonical evidence rather than extend that projection.

Before first freeze, the canonical document is schema-validated. Retry/resume
loads the same hashed evidence artifact and never reacquires its market facts.
Only Final Quote Refresh requests `batch_quotes(..., force_refresh=True)`.
It deep-copies the snapshot, preserves frozen `instrument_market`, records the
new quote batch separately, and fails closed if any required quote is unusable.
The legacy `market_data.refresh_snapshot_quotes` entry point delegates here.

## Tests And Verification

Offline tests live in:

- `backend/tests/test_instrument_market.py`: identity/capabilities, authenticated
  APIs, batch ordering/limits, stock/ETF/index bars, aggregation, invalid rows,
  adjustment keys, five-level books, derived fields, flow semantics, worst-grade
  aggregation, session freshness, cache isolation and secret sentinels.
- `backend/tests/test_instrument_market_adapters.py`: native HTTP batch counts,
  qualified exchange identity, provider units/endpoints, explicit fallback,
  time-only observation handling, empty/failure distinction, immutable evidence,
  fresh final quotes, preserved analysis summaries and hermetic acceptance.

Regression commands:

```sh
cd backend
python -m pytest tests
cd ../frontend
npm run typecheck
npm run build
npm run e2e:acceptance
cd ..
docker compose build
git diff --check
```

Acceptance uses the existing isolated database/services and deterministic
providers; it must not contact production market or model endpoints.
The PR must be checked against the actual pushed head SHA in GitHub Actions,
including backend, frontend, frontend-acceptance and Docker jobs.

## Not Verified And Follow-Up

Implementation, automated tests, merging and production verification are
separate states. A passing offline fixture does not prove live-market quality.
This stage does not claim verification of:

- Long-running real intraday quotes or real five-level book stability.
- The accuracy of a provider's proprietary capital-flow classification.
- Exchange Level-2 consistency.
- A manual InstrumentDetail UI or its mobile experience.
- Investment returns, automatic trading, or strategy effectiveness.

Operational prerequisites include provisioned SecurityMaster rows, a current
TradingCalendar, configured provider access and reliable lot-size metadata.
Index volume/turnover semantics are conservatively unknown: quote/bars return
`null` with `INDEX_VOLUME_SEMANTICS_UNKNOWN` rather than invent comparable
shares/CNY totals. Unsupported ETF flow and minute bars remain explicit.
Future UI consumers should use capabilities and canonical identities; they
must not parse provider-specific names or payloads.
