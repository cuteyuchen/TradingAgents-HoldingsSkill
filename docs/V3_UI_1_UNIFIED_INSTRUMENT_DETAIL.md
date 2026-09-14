# V3-UI-1 Unified Instrument Detail

## Scope

Baseline: `82580522616e65c66dec884ed61ff40be55308d4` (MARKET-2 / MARKET-2.1 / V3-UI-0).

UI-1 turns the frozen MARKET-2 instrument market facade into a reusable, trustworthy instrument detail surface. It adds no backend features, no DB migrations, no strategy/Agent changes, and no frontend direct provider requests.

## Architecture

```
/market/instruments/:code  (V3InstrumentDetailPage)
V3InstrumentDetailDrawer   (V3DetailDrawer shell)
        \                     /
         V3InstrumentDetail   <-- single shared core
                 |
         useInstrumentDetail  <-- shared data controller
                 |
         typed api client (AbortSignal)
                 |
         GET /api/v3/market/instruments/*
```

Implementation tree:

```
frontend/src/v3/instrument-detail/
  V3InstrumentDetail.vue
  V3InstrumentDetailPage.vue
  V3InstrumentDetailDrawer.vue
  V3InstrumentHeader.vue
  V3InstrumentQuoteSummary.vue
  V3InstrumentKlineChart.vue
  V3InstrumentOrderBook.vue
  V3InstrumentCapitalFlow.vue
  V3InstrumentMetadata.vue
  V3InstrumentDataQuality.vue
  useInstrumentDetail.ts
  indicators.ts
  formatters.ts
```

Page and Drawer must never fork request logic. Dashboard / Holdings / Candidates / History later mount `V3InstrumentDetailDrawer` or navigate to the page route.

## Route

- Path: `/market/instruments/:code`
- Name: `instrument-detail`
- meta: `{ uiSystem: 'v3', title: '标的详情' }`
- Auth protected (existing `hasSession` guard). Not `public`. Not gated by Foundation flag.
- Lazy component import so ECharts stays out of the initial main chunk.

## Tabs

| Tab | UI-1 |
| --- | --- |
| 行情 | Full: quote + Kline/MA/MACD/RSI + provenance + metadata |
| 盘口 | Full when `capabilities.order_book` |
| 资金 | Full when `capabilities.capital_flow` |
| 分析 | Placeholder honest empty state |
| 新闻 | Placeholder honest empty state |
| 历史 | Placeholder honest empty state |

Analysis/News/History do **not** parse Markdown reports, Agent hidden reasoning, or invent providers. They only light up when a stable structured contract by instrument code exists.

## Capability Handling

Capabilities are the only source of truth. INDEX typically:

- quote=true, bars=true, order_book=false, capital_flow=false

When unsupported, the tab remains as product position, shows a dedicated message, and **does not** send the corresponding HTTP request. ETF capital flow follows the same rule (`capital_flow=false` default).

## Data Loading

First paint (parallel after identity):

1. metadata
2. market session
3. quote
4. bars (default `interval=1d`, `limit=250`, preferred adjustment)

Adjustment priority: `forward` → `none` → `backward`. INDEX usually resolves to `none`. Controls only show `capabilities.adjustments`.

Lazy modules:

- 盘口 first open → order-book
- 资金 first open → capital-flow

## Race Safety

`useInstrumentDetail` combines:

- `AbortController` (code/interval/adjustment change, drawer close, unmount)
- monotonically increasing request sequence
- active code validation before applying payloads

Abort is normal control flow — no Toast/Banner.

## Session-aware Polling

Uses `/api/v3/market/session` (not browser clock):

| Module | Continuous auction | Lunch | Closed / non-trading |
| --- | --- | --- | --- |
| Quote | 5s | 30s | no poll |
| OrderBook (active tab) | 4s | — | no poll |
| CapitalFlow (active tab) | 50s | — | no poll |
| Bars | 30s | — | no poll |

`document.visibilitychange`: hidden → pause timers; visible → re-evaluate from session. Unmount clears timers/listeners.

## Time / Provenance / Quality

- Header quote time uses `observed_at` only; `fetched_at` never pretends to be market time.
- `observed_at=null` shows 行情时间未知.
- `data_basis`: 盘中 / 当日收盘 / 上一交易日收盘.
- Header neighborhood: quality A–F, stale, fallback (备用数据源), suspended.
- Detailed provider/source/observed/fetched/trading_date/flags/error_code live in 数据说明/数据质量, not the hero visual.

## K-line and Indicators

Library: **Apache ECharts only**, modular imports via `echarts/core` + chart/component/renderer modules.

- Intervals: backend-supported `1d` / `1w` / `1M` only.
- Limits: 60 / 120 / 250 / 500 (backend max 1000).
- Interaction: crosshair, OHLC tooltip, wheel/drag zoom, DataZoom, legend.
- Layout: Kline+MA / Volume / MACD|RSI (switchable).

Pure frontend deterministic indicators (`indicators.ts`):

- `calculateSMA` — incomplete windows are `null`
- `calculateEMA` — SMA seed then recursive
- `calculateMACD` — EMA12/26, DIF, DEA=EMA9(DIF), HIST=2*(DIF-DEA)
- `calculateRSI` — Wilder smoothing, RSI14

No input mutation. Empty/short arrays safe. No NaN/Infinity leak. Indicators are display-only; no auto trade signals, no LLM.

## Order Book

Display order 卖5→卖1 / 买1→卖5. Asks are copy+reverse for UI only; source arrays are not mutated. Volumes in shares. Price colors compare to `prev_close` (not bid-red/ask-green). Derived 委比/委差 show 推导值 marker when `derived_fields` contains them.

States:

- unsupported: 指数当前不提供五档盘口
- empty: 当前没有盘口挂单
- unavailable: 数据源当前无法提供
- stale / degraded / fallback badges where applicable

## Capital Flow

Shows 主力/超大单/大单/中单/小单 in CNY (万/亿). Positive = net inflow (market-up red), negative = net outflow (market-down green). Permanent disclaimer: provider-derived classification, not exchange Level-2 truth. `provider_derived` / `methodology` visible. History chart defaults to 主力净流入 and switches metrics (not five lines by default). ETF/INDEX capability=false → no request.

## Formatters

Centralized in `formatters.ts`:

- null / NaN / Infinity → `—`
- 0 → real `0`
- price default 2 decimals (extensible)
- percent default 2 decimals with `+/-`
- volume shares → 股 / 万股 / 亿股 (never silently “手”)
- money CNY → 元 / 万 / 亿

A-share color helper: >0 market-up, <0 market-down, else flat. Risk/error colors never reuse market-up/down.

## Accessibility

- Tabs keyboard reachable (Quasar q-tab)
- Refresh button `aria-label`
- Drawer close keyboard operable
- Status not color-only (text labels)
- Change values always show signed numbers
- Error retry is a real `button`
- Canvas charts expose text/aria summary (symbol, interval, date range, last close)
- Quote numbers remain in DOM text, not only canvas

## Empty / Unsupported / Unavailable / Stale

Distinct copy and test ids. Never collapse everything into “暂无数据”.

Error mapping:

| Case | UI |
| --- | --- |
| 404 | 标的不存在或主数据未收录 |
| 429 | 行情请求过于频繁 |
| 5xx | 行情服务暂不可用 |
| network | 网络异常 |
| abort | silent |

No raw backend exception text.

## Acceptance

New file: `frontend/e2e/v3-instrument-detail.spec.ts`

Deterministic `page.route` fixtures only. Coverage:

- STOCK_AVAILABLE (600519.SH)
- ETF_AVAILABLE (159915.SZ) + no flow request
- INDEX_AVAILABLE (000300.SH) + no book/flow request + adjustment none only
- Cross-exchange 000001.SH vs 000001.SZ isolation
- QUOTE_STALE / QUOTE_DEGRADED
- BOOK empty / unsupported
- MODULE_UNAVAILABLE / INSTRUMENT_NOT_FOUND
- Race (slow A after switch to B)
- Interval / adjustment / MACD↔RSI network+UI
- Mobile 375 no horizontal overflow
- Honest analysis/news/history placeholders

## Bundle Impact

Dependency added: `echarts@^6` (modular).

Expected:

- InstrumentDetail route lazy chunk includes ECharts modules used by chart components.
- ECharts must not appear in initial main entry. Verify with `npm run build` asset list; fix lazy/modular imports if it does.

No second chart library.

## Backend Changes

None in this stage (contract-only confirmation). If a MARKET-2 contract bug is found, only a minimal fix + test is allowed.

## Migration

None. No DB migration.

## Known Limitations

- No minute bars (MARKET-2 exposes 1d/1w/1M only)
- Five-level book is a retail snapshot, not exchange Level-2
- Capital flow is provider-derived estimation
- ETF flow is provider/capability dependent (often unsupported)
- INDEX has no order book / capital flow
- Analysis / News / History decisions unavailable until structured contracts exist
- Manual visual QA of the full workbench not completed in this stage
- Complete mobile business flows (beyond layout smoke) not completed
- Live provider verification is out of scope for UI acceptance fixtures

## Verification Commands

```bash
cd backend
python -m pytest tests -q
cd ../frontend
npm run typecheck
npm run build
npm run e2e:acceptance
cd ..
docker compose build
git diff --check
```

## Follow-up

Next stage: **V3-UI-2 — Dashboard**, reusing `V3InstrumentDetailDrawer` for holdings/candidates click-through without rewriting the core.
