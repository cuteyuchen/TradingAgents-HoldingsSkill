# V3-UI-2 Dashboard Decision Workbench

## Scope

Baseline: `df5762c8da3e1ac67e88ae5b88100884bcff7919` (V3-UI-1 unified instrument detail).

UI-2 formally migrates `/dashboard` to Quasar V3 as a decision workbench. The homepage must answer two questions immediately:

1. 市场现在处于什么状态？
2. 我的组合今天需不需要行动？

No strategy/Agent/LLM changes, no order placement, no DB migration, no new recommendation logic.

## Route Migration

```
before: /dashboard → views/DashboardView.vue  (legacy + Naive UI)
after:  /dashboard → v3/dashboard/V3DashboardView.vue  (V3, Quasar + V3 components)
```

- `path` remains `/dashboard`
- `name` remains `dashboard`
- `meta.uiSystem = 'v3'`
- auth protected (existing router guard)
- no parallel `/v3/dashboard` product entry
- legacy `DashboardView.vue` retained temporarily to avoid unrelated deletion

## Implementation Tree

```
frontend/src/v3/dashboard/
  V3DashboardView.vue
  useV3Dashboard.ts
  dashboard-types.ts
  dashboard-formatters.ts
  V3DashboardSessionBar.vue
  V3MajorIndices.vue
  V3SystemicRiskPanel.vue
  V3PortfolioSnapshot.vue
  V3DecisionHero.vue
  V3ActionList.vue
  V3LatestAnalysis.vue
  V3ImportantEvents.vue
  V3MarketMiniTrend.vue
```

View owns layout; controller owns requests/polling/race; adapter maps raw contracts → typed ViewModels.

## Dashboard IA (frozen order)

1. 交易时段 / 数据时间
2. 六大指数
3. 系统性风险
4. 组合状态
5. 今天要不要行动
6. 最新分析
7. 重要事件 / 下一检查点

Desktop may use a 2/3 + 1/3 row for systemic risk vs portfolio. Mobile keeps the same order.

## Data Sources

| Module | API |
| --- | --- |
| Session | `GET /api/v3/market/session` |
| Major indices | `GET /api/v3/market/major-indices` |
| Systemic risk | `GET /api/v3/market/systemic-risk` |
| Market overview | `GET /api/v3/market/overview` |
| Portfolio dashboard | `GET /api/v3/portfolios/{id}/dashboard/today` |

- Market modules are portfolio-independent and load in parallel first.
- Portfolio dashboard loads after `loadPortfolios()` + selected id.
- Portfolio switch does **not** re-fetch market public data.
- No Fuyao brief, no Markdown parsing, no browser-side provider calls, no fake data.
- Diagnostics are not loaded on the homepage by default.

## Market vs Strategy Timestamps

- Session bar reuses `V3DataTimestamp`.
- Quote time comes from major-index `as_of` / systemic `as_of` / overview `quote_as_of`.
- Strategy time comes from `strategy_analysis_at` / analysis `finished_at` / decision `decision_at`.
- Dashboard `generated_at`/`as_of` is **not** used as quote or strategy time.

## Market Session

`MarketSessionService` is the only authority. Display labels:

| session | label |
| --- | --- |
| PRE_OPEN | 盘前 |
| OPEN_AUCTION | 开盘集合竞价 |
| MORNING | 上午交易 |
| LUNCH_BREAK | 午间休市 |
| AFTERNOON | 下午交易 |
| CLOSE_AUCTION | 收盘集合竞价 |
| CLOSED | 已收盘 |
| NON_TRADING_DAY | 非交易日 |
| DATA_ABNORMAL | 数据异常 |

Weekend / previous-session-close / `data_basis=previous_session_close` shows **上一交易日收盘**, never “今日收盘”.

## Major Indices

Fixed product order (always 6 slots):

1. 上证指数 `000001.SH`
2. 深证成指 `399001.SZ`
3. 创业板指 `399006.SZ`
4. 沪深300 `000300.SH`
5. 中证1000 `000852.SH`
6. 科创50 `000688.SH`

Each card: name, canonical code, price, change, change_pct, quote_ts, availability/stale. Missing index shows unavailable — never silently removed. A-share colors: up=red, down=green, flat=neutral.

Click opens a **single** shared:

```vue
<V3InstrumentDetailDrawer v-model="instrumentDrawerOpen" :code="selectedInstrumentCode" />
```

Loaded via `defineAsyncComponent` so the initial dashboard chunk does not pull Kline/ECharts.

## Systemic Risk / All-A Median / Breadth / Top5

- Risk uses independent risk tokens (blue/amber/teal), never market-up red for “high risk”.
- 典型个股表现 section shows:
  - 全A中位日表现 (`all_a_median.daily_median_return`)
  - 20日趋势 (`trend_20d`)
  - 250日分位 (`percentile_250d`)
- Breadth: 上涨/下跌/平盘/涨跌比/涨停/跌停. Unknown = `—`, never 0.
- Turnover: total + 20d average.
- Top5 concentration: current `ratio`, `avg_20d`, trend text; SVG mini trend when history points exist. Mini trend is native SVG (`V3MarketMiniTrend`), not ECharts.

## Portfolio Summary

Shows: total assets, market value, cash, gross exposure, position count, quality, snapshot freshness.

### Day return / floating P&L

Dashboard portfolio contract currently has **no authoritative day-return amount or floating-P&L amount** (only per-holding `pnl_ratio`). Frontend therefore shows `—` and does **not** reverse-engineer amounts from ratios. See Known Limitations.

## Decision Hero (MISSING ≠ NO_ACTION)

| kind | UI |
| --- | --- |
| `NO_ACTION` | 今日无需操作 (neutral/info, **not** green success) |
| `ACTIONABLE` | 今日需要行动 + action count |
| `BLOCKED` | 策略暂不可执行 + structured reasons |
| `MISSING` | 暂无有效策略结论 |
| `NO_PORTFOLIO` | 需先建立组合后才能生成组合决策 |

Backend may fallback `final_action=NO_ACTION` when no latest decision exists. Frontend checks:

- `decisions.status`
- `analysis.status`
- `latest` presence
- quality

If `decisions.status=MISSING` and no valid latest analysis → **暂无有效策略结论**, never explicit NO_ACTION.

Actions come only from structured `holding_actions` / `candidate_actions`. Frontend never invents BUY/SELL from risk score or index direction.

## Latest Analysis / Important Events

Latest analysis: finished_at, mode/run, conclusion, quality/confidence, `analysis_in_progress`. Running state shows 分析进行中; previous result is labeled 上一版结论.

Important events: next checkpoint from backend timeline, warnings, trigger state, operational notifications. Not a full diagnostics dump.

## Race Safety / Failure Isolation

- Per-module AbortController + sequence.
- Portfolio request captures portfolio id; late A response cannot override selected B.
- Abort does not surface error UI.
- Market failure does not hide portfolio/decision.
- Portfolio failure does not hide indices/risk.
- Refresh failure keeps last successful data and shows stale/refresh-failed badges.

## Polling / Visibility

- Session: self-healing heartbeat (never dead-zones null / DATA_ABNORMAL / all session kinds).
- **Session heartbeat only reschedules data pollers when the authoritative session kind actually changes.** Same-kind ticks must never reset indices/risk/overview/portfolio timers (would starve the 90s overview).
- Indices: 8s during trading, 30s lunch, stop on closed/non-trading.
- Systemic risk: ~45s trading (aggregate market, not 5s).
- Overview: ~90s low frequency + manual refresh.
- Portfolio: ~60s during trading.
- `document.hidden` stops timers.
- Resume: **session first**, then refresh live modules, then reschedule from the fresh session.

## Portfolio Ownership

Dashboard payload is bound to the portfolio that produced it:

- `portfolioDashboardOwnerId` records which portfolio owns the applied payload.
- ViewModel only exposes the payload when `ownerId === selectedPortfolioId`.
- Switch A → B while B is pending: selector shows B; A metrics/decision/analysis are **not** attributed to B (loading skeleton instead).
- B first failure: B localized error; A last-success is not shown as B.
- Same-portfolio refresh failure: keep last-success + refresh-failed badge.

## Module Failure Isolation

Independent loading/error/success state per module. No single `Promise.all` that blanks the whole page.

## Mobile / Accessibility

- 375px: no document horizontal overflow; same IA order.
- Portfolio select has label; refresh has aria-label.
- Index cards are buttons.
- Up/down not color-only (▲/▼ + text).
- Risk not color-only (level text).
- NO_ACTION/BLOCKED/MISSING always have text.
- Sparkline has aria summary.

## Bundle Impact

Route is lazy. InstrumentDetail drawer is async. Dashboard uses native SVG mini trend. Initial dashboard does not eager-load Candlestick ECharts.

Observed build (this branch):

| chunk | size | gzip |
| --- | --- | --- |
| main entry `index-*.js` | ~1707 kB | ~482 kB |
| `V3DashboardView-*.js` | ~47 kB | ~15 kB |
| `V3InstrumentDetailDrawer-*.js` | ~0.3 kB | ~0.2 kB |
| `V3InstrumentDetail-*.js` (drawer open) | ~47 kB | ~15 kB |
| ECharts charts/axisBand | separate async chunks | not in dashboard initial load |

## Acceptance

New deterministic suite: `frontend/e2e/v3-dashboard.spec.ts` (all `page.route()`, no live providers).

Covered fixtures/scenarios:

- MARKET_LIVE_NORMAL
- MARKET_PREVIOUS_CLOSE / NON_TRADING_DAY
- MARKET_DATA_ABNORMAL (session cadence path)
- PORTFOLIO_NORMAL / MISSING / NO_PORTFOLIO
- DECISION_NO_ACTION
- DECISION_ACTIONABLE
- DECISION_BLOCKED
- DECISION_MISSING (≠ NO_ACTION)
- ANALYSIS_RUNNING
- MULTI_PORTFOLIO race A-slow / B-fast
- Partial failure market 500 / portfolio 500
- Initial portfolio failure + localized retry
- CLOSED + session_close (same-day close)
- Blank conclusion = MISSING
- No fabricated Top5 sparkline
- Visibility resume session-first
- Mobile 375 overflow
- Index drawer reuse

Legacy race test updated to V3 selectors and continues to assert A-stale cannot override B.

Local full acceptance: **86 passed / 0 failed**.

## Backend Changes

None. Frontend-only. No DB migration.

## Known Limitations

- Holdings / Analysis / History / Settings remain Legacy.
- Real long-running live-market human stability not verified.
- Full visual human acceptance not completed.
- Full mobile business flows not human-verified.
- Day return / floating P&L unavailable on homepage (no authoritative dashboard amount contract).
- Homepage places no orders and adds no new strategy recommendations.
- Top5 history series is limited by current overview contract — no fabricated 2-point sparkline; current / 20d avg / trend only until a real series exists.

## Verification

- `npm run typecheck` — pass
- `npm run build` — pass
- `scripts/run_acceptance.py` — **86 passed, 0 failed**
- Backend unchanged (frontend-only)
- Exact-head CI required for merge gate

## Follow-up

Next: **V3-UI-3 — Holdings Workstation**
