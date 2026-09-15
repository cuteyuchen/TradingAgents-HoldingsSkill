# V3 MARKET-1: Market Session and Systemic Risk Foundation

## Status

- IMPLEMENTED: shared A-share market-session contract, all-A fact aggregation,
  major-index contract, and deterministic systemic-risk snapshot.
- TESTED: full backend suite, frontend type/build checks, and browser acceptance.
- MERGED: no.
- VERIFIED: no production-provider, exchange, or visual-product verification.

## Scope and authority

MARKET-1 extends the existing market foundation. It does not create another
TradingCalendar, SecurityMaster, Provider Registry, quote normalizer, or raw
quote store.

The dependency direction is one-way:

```text
Analysis Engine -> Market Foundation
```

`TradingCalendar` is the authority for whether a date is an A-share trading
day. Calendar gaps are never interpreted as a weekend or holiday. They are
reported as `DATA_ABNORMAL`.

All session calculation uses `Asia/Shanghai`, never a browser or server-local
timezone.

## Session contract

`MarketSessionService` is the single session authority. It exposes:

- `resolve_session(now)`
- `resolve_latest_valid_trading_date(now)`
- `resolve_previous_trading_date(date)`
- `resolve_next_trading_date(date)`

The session enum is:

```text
PRE_OPEN
OPEN_AUCTION
MORNING
LUNCH_BREAK
AFTERNOON
CLOSE_AUCTION
CLOSED
NON_TRADING_DAY
DATA_ABNORMAL
```

Trading-day phases are:

| China time | Session | Data basis |
| --- | --- | --- |
| Before 09:15 | `PRE_OPEN` | `previous_session_close` |
| 09:15-09:25 | `OPEN_AUCTION` | `live` |
| 09:25-09:30 | `PRE_OPEN` | `previous_session_close` |
| 09:30-11:30 | `MORNING` | `live` |
| 11:30-13:00 | `LUNCH_BREAK` | `live` |
| 13:00-14:57 | `AFTERNOON` | `live` |
| 14:57-15:00 | `CLOSE_AUCTION` | `live` |
| 15:00 and later | `CLOSED` | `session_close` |

On a calendar-marked non-trading day, `session=NON_TRADING_DAY` and
`data_basis=previous_session_close`. `latest_valid_trading_date`, previous,
and next dates are always resolved through the persisted calendar, including
long holidays.

If the calendar says the market should be open but the required batch quote
data is unavailable, `data_status=abnormal` and the effective session is
`DATA_ABNORMAL`; it is never falsely labelled closed.

`market_open` remains a backward-compatible boolean projection only. It is
not a session authority.

## Time semantics

Market facts carry `quote_as_of` from the quote/session snapshot. Session
resolution separately carries `resolved_at`. Dashboard compatibility fields
also expose `strategy_analysis_at` independently from `quote_as_of`; a later
analysis never overwrites or relabels the quote timestamp.

## Major indices

The v1 reference list is ordered and always returns all six entries:

```text
000001.SH  上证指数
399001.SZ  深证成指
399006.SZ  创业板指
000300.SH  沪深300
000852.SH  中证1000
000688.SH  科创50
```

An index uses `SecurityMaster` identity when an `INDEX` identity exists. A
missing one index yields its own `status=unavailable`; the response remains
HTTP 200 and other index records remain usable. Numeric fields unavailable
from a provider are `null`, never synthetic zeroes.

The index provider call is one request for all six symbols. It does not cause
per-index calls. Pre-open and non-trading-day reads instead reuse the latest
persisted previous-session-close indices; they never perform a live index
refresh. An unavailable index has `as_of=null` rather than borrowing another
instrument's timestamp.

## All-A universe and metrics

The MARKET-1 all-A universe is versioned as `all-a-foundation-v1` and is not
the frozen Phase C Market Score universe. Phase C deliberately has stricter
score-specific exclusions (for example ST/BSE/new-listing restrictions), so
its historical score definition remains unchanged.

MARKET-1 includes active `SecurityMaster` rows classified as `STOCK` on SSE,
SZSE, or BSE. It excludes ETF, LOF/fund, bond, convertible bond, index,
non-stock identity, B-share, and delisted/inactive rows. A BSE stock is kept
when SecurityMaster classifies it as an A-share stock.

For each universe member:

- suspended rows increment `suspended_count` and do not enter return/breadth
  denominators;
- missing or invalid quote quality, non-positive `price`/`prev_close`, and
  implausible invalid returns are explicitly counted as exclusions;
- a valid eligible return is `price / prev_close - 1`.

The returned counts are `universe_total`, `eligible_count`, `excluded_count`,
and `suspended_count`.

### Median return and median index

```text
daily_median_return_t = median(eligible individual_return_t)
median_index_t = median_index_(t-1) * (1 + daily_median_return_t)
```

The base is 1000. The first valid observation uses the existing
`next_median_index()` base behavior. The daily series is persisted through the
existing `AllAMedianIndexDaily` table under a separate
`calculation_version=systemic-market-v1`, so it does not silently overwrite
the Phase C Market Score median-index history.

`trend_20d` is:

```text
current_value / value_20_trading_days_ago - 1
```

`percentile_250d` is the inclusive empirical percentile of the current index
within the most recent 250 available index values. It is `null` until 250
observations are available.

### Turnover concentration

Only eligible all-A stocks with a calculable non-negative `amount` enter this
metric. `amount` is transaction amount in CNY, not volume.

```text
eligible_count = number of stocks with calculable amount
top_n = ceil(eligible_count * 0.05)
ratio = sum(top_n amounts sorted by amount descending, then code ascending)
        / sum(all eligible amounts)
```

If total turnover is not positive, the ratio is unavailable. The response
records `top_n`, `eligible_count`, and `total_turnover`. `avg_20d` uses the
previous 20 available daily systemic-market observations; `delta_vs_20d` and
the rising/falling/flat direction follow directly from that comparison.
`percentile_250d` follows the same explicit 250-observation rule.

### Breadth and limits

`breadth` returns `advancers`, `decliners`, `unchanged`, `suspended`,
`limit_up`, `limit_down`, `total`, `eligible_count`, and
`advance_decline_ratio`. `total` is eligible breadth members plus suspended
members; invalid/missing data remains separately visible through exclusions.

Limit aggregation reuses the canonical `is_price_limit(...)` function and
SecurityMaster board/ST attributes. It covers main-board, ST, ChiNext, STAR,
and BSE limits; MARKET-1 does not introduce a fixed 10-percent rule.

All monetary turnover values use CNY. Presentation formatting into yi/yuan or
trillion is a frontend responsibility.

## Quality and Systemic Risk

Every aggregate emits a quality grade, quality flags, source status, as-of
time, and trading date. Provider fallback downgrades quality explicitly.
Missing all-A metrics do not become zeroes.

`SystemicRiskSnapshot` is deterministic and does not call a model. It uses the
existing versioned Phase C Market Score only when a current-day, non-frozen,
usable score exists:

| Existing market regime | Systemic risk level |
| --- | --- |
| `STRONG_RISK_OFF` | `EXTREME` |
| `RISK_OFF` | `HIGH` |
| `NEUTRAL` | `MEDIUM` |
| `RISK_ON` / `STRONG_RISK_ON` | `LOW` |

This is a documented reuse of the existing deterministic regime algorithm, not
a new investment threshold. If any critical all-market metric is unavailable,
or the current score is absent/frozen, `risk_level=UNKNOWN` and
`risk_score=null`.

Machine-readable facts may include:

```text
BREADTH_WEAK
MEDIAN_STOCK_WEAK
TURNOVER_CONCENTRATED
INDEX_DIVERGENCE
LIQUIDITY_LOW
LIMIT_DOWN_EXPANSION
DATA_DEGRADED
```

They explain observed conditions and do not independently invent a score.

## Snapshot and cache behavior

All full-market calculations consume one existing all-A batch snapshot. There
is no `for stock: provider.fetch(stock)` path. The short-lived process cache is
implemented inside `market_snapshot_service`, the existing quote-snapshot
authority, rather than as a parallel cache:

- live session: 5 seconds;
- session-close request: 300 seconds;
- failed/abnormal snapshot: 5 seconds;
- pre-open/non-trading reads: no new quote request; use the latest persisted
  `previous_session_close` aggregate.

At a usable session close, compact aggregate facts are stored in the existing
`MarketMetricSnapshot` and `AllAMedianIndexDaily` tables with
`systemic-market-v1`. Raw all-A quote rows are not permanently persisted.

## API

All routes retain the existing authenticated V3 market namespace:

```text
GET /api/v3/market/session
GET /api/v3/market/major-indices
GET /api/v3/market/systemic-risk
GET /api/v3/market/overview
```

`overview` returns `session`, `major_indices`, `systemic_risk`, `breadth`,
`all_a_median`, `turnover_concentration`, `total_turnover`, and `quote_as_of`.
The optional `as_of` query parameter exists for deterministic testing and
operator diagnosis; it does not accept user-owned market facts.

## Persistence and migration

No database migration is needed. MARKET-1 reuses the existing compact market
metric and median-index tables with a distinct calculation/universe version.

## Tests

`backend/tests/test_market_foundation.py` covers:

- Shanghai time session phases and calendar authority;
- weekend/holiday previous and next trading-day resolution;
- missing calendar and live quote failure as `DATA_ABNORMAL`;
- ETF exclusion, BSE inclusion, suspension handling, median return, breadth,
  board-aware limit up/down, and CNY amount usage;
- Top 5 percent `ceil(N * 0.05)` for 100 and 101 eligible stocks;
- one batch request for a coherent overview;
- weekend previous-session reuse without a live quote call;
- partial six-index contract and `UNKNOWN` risk under critical data absence.
