# Data Sources

Use this file when collecting data for intraday portfolio advice. The data architecture is inspired by `TradingAgents-astock`'s 7-source direct-connect model and `TradingAgents-AShare`'s centralized DataCollector pattern.

## Holdings Source Priority

1. Current message screenshot (highest trust).
2. Holdings explicitly typed in the current conversation.
3. Historical conversation or memory if no current screenshot/list exists.
4. Ask for a screenshot/list if holdings remain unknown.

Never infer holdings from local dev apps, local databases, test fixtures, broker cache files, or unrelated prior tool output.

## Centralized Data Collection Principle

Fetch all data once, share across all analyst roles. Never fetch the same data point twice. When multiple holdings share the same data needs (e.g., same sector news, same index data), deduplicate requests.

**Recommended collection order:**
1. Resolve all ticker symbols first (name → code mapping).
2. Batch-fetch quotes for all holdings in one request where possible.
3. Batch-fetch index/sector data (shared across all holdings).
4. Fetch per-holding data (news, fundamentals, capital flow) sequentially with rate limiting.
5. Pre-compute technical indicators and VPA from K-line data before analysts need them.

## Source Matrix

### Tier 1: Core Market Data

| Data Need | Primary Source | Fallback Chain | Notes |
|---|---|---|---|
| Real-time quote (实时行情) | Tencent Finance `qt.gtimg.cn` | Sina `hq.sinajs.cn` → Eastmoney push2 API | Price, pct change, open/high/low/prev close, turnover, volume ratio, quote time, market session |
| K-line / OHLCV | AkShare `stock_zh_a_hist` (Eastmoney) | mootdx (TCP 7709) → Sina daily → Tencent `stock_zh_a_hist_tx` | For support/resistance and trend; avoid look-ahead bias |
| ETF K-line | AkShare `fund_etf_hist_em` | `fund_etf_hist_sina` | Separate endpoint for ETFs vs stocks |
| Technical indicators | Derived from OHLCV via stockstats | Manual calculation from K-line | RSI, MACD, MA(5/10/20/60), EMA, Bollinger, ATR, VWMA, MFI |

### Tier 2: Fundamental & Valuation Data

| Data Need | Primary Source | Fallback Chain | Notes |
|---|---|---|---|
| Valuation (估值) | Tencent Finance `qt.gtimg.cn` | Eastmoney F10 → Xueqiu basic info | PE TTM, PB, market cap, turnover rate, limit prices |
| Financial statements (财报三表) | Sina Finance HTTP | AkShare `stock_financial_abstract` → THS `stock_financial_abstract_new_ths` | Balance sheet, income statement, cash flow |
| EPS forecast (一致预期) | THS `10jqka.com.cn` | Eastmoney consensus | Forward PE, PEG calculation |
| Industry comparison (行业对比) | Eastmoney push2 sector API | Baidu concept blocks | Full sector 90-block comparison |

### Tier 3: Signal Data (A股特有信号层)

Inspired by `TradingAgents-astock`'s 8 signal data tools:

| Data Need | Primary Source | Fallback | Notes |
|---|---|---|---|
| Northbound flow (北向资金) | THS `hsgtApi` | Eastmoney northbound data | Real-time minute-level flow; if historical unavailable, use "real-time snapshot + local CSV accumulation" pattern |
| Dragon-Tiger Board (龙虎榜) | Eastmoney `datacenter-web` | News search fallback | Seat details, institutional participation, net buy/sell per seat |
| Individual fund flow (个股资金流) | Eastmoney push2 fund flow | -- | Super-large/large/medium/small order breakdown |
| Concept blocks (概念板块) | Baidu `finance.pae.baidu.com` | Eastmoney concept rank | Sector/concept/region classification per stock |
| Hot stocks (热门股) | THS hot stocks with editor annotations | Xueqiu hot follow | Topic attribution, theme tags |
| ZT pool (涨停板) | AkShare `stock_zt_pool_em` | Eastmoney limit-up list | Limit-up chain analysis, sector clustering |
| Insider transactions (股东研究) | mootdx F10 `stock_main_stock_holder` | Eastmoney shareholder changes | 6-month activity, top holder changes |
| Lockup calendar (解禁日历) | Eastmoney `datacenter-web` | Announcement search | Historical + next 90 days, reduction filings |

### Tier 4: News & Sentiment Data

| Data Need | Primary Source | Fallback Chain | Notes |
|---|---|---|---|
| Stock news (个股新闻) | Eastmoney search/news | Sina Finance news | Company-specific, last 7 days preferred |
| Global/macro news | CLS `cls.cn` telegraph (电报) | Eastmoney 7x24 → CCTV `news_cctv` | Separate company, sector, macro, overseas |
| Policy news | State/media/sector policy sources | Eastmoney/CLS filtered | Classify as supportive, restrictive, neutral |
| Announcements (公告) | Eastmoney announcements | Sina/official filings | Company filings, regulatory notices |

### Tier 5: Global Context

| Data Need | Primary Source | Notes |
|---|---|---|
| US indices (S&P, Nasdaq, Dow) | Eastmoney global / AkShare | Overnight performance, futures |
| HK indices (HSI, HSCEI) | Eastmoney / Tencent | Relevant for HK ETFs, AH premium |
| USD/CNH exchange rate | Eastmoney forex | Risk appetite indicator |
| Commodities (gold, oil, copper) | Eastmoney / AkShare | Sector-relevant commodities |

## Eastmoney Rate Limiting Discipline

Eastmoney endpoints can rate-limit or temporarily block aggressive polling. All thresholds below are defined in `configuration.md` (single source of truth). The pattern is inspired by `TradingAgents-astock`'s measured `_em_get()` throttling.

**Measured ban triggers** (empirical, from astock's production usage):

| Trigger | Threshold | Effect |
|---|---|---|
| Request rate | > `em_max_per_second` = 5 req/s | IP temporarily blocked |
| Concurrency | ≥ 10 concurrent (vs `em_max_concurrent` budget of 5) | IP temporarily blocked |
| Volume | > `em_max_per_minute` = 200 req/min | IP temporarily blocked |

**Tunable throttling parameters** (all in `configuration.md`):

| Parameter | Default | When To Change |
|---|---|---|
| `em_min_interval_sec` | 1.0 | **Raise to 2.0 before large multi-holding batch runs** to proactively slow down |
| `em_jitter_sec` | 0.1–0.5 | Keep as-is; randomizes request timing |
| `em_max_concurrent` | 5 (interactive) / 3 (`em_scheduled_concurrent`, scheduled tasks) | Stay well under the 10 ban trigger |
| `em_zombie_timeout_sec` | 120 | Release a slot if a request hangs beyond this |

**Discipline rules:**

- **Minimum interval**: `em_min_interval_sec` between repeated Eastmoney requests.
- **Random jitter**: Add `em_jitter_sec` random delay to avoid pattern detection.
- **Session reuse**: Use Keep-Alive sessions to reduce connection overhead.
- **Batch first**: Prefer batch quote APIs over individual queries.
- **Non-Eastmoney for redundancy**: Use Tencent/Sina/mootdx for data they can reliably provide, reserving Eastmoney for unique data (dragon-tiger, lockup, fund flow).
- **Record gaps**: If a request fails, record `[数据缺失: source/field]` and continue only when the field is non-critical. Do not retry endlessly.
- **Concurrent slot limit**: Limit total concurrent Eastmoney requests to `em_max_concurrent` (5 interactive, 3 scheduled).

## Source Routing Decision Table

Inspired by `TradingAgents-astock`'s `data_vendors` configuration: each data type has a **fixed route priority** and a **deterministic fallback action**, so source selection is not ad hoc per run.

| Data Type | Route Priority | Fallback Action on Failure | Why This Route |
|---|---|---|---|
| Real-time quote | Tencent `qt.gtimg.cn` → Sina → Eastmoney push2 | Record `[数据缺失: quote]`; block affected trading advice if all quote routes fail | Tencent/Sina have no rate limit; reserve Eastmoney |
| K-line OHLCV (stock) | mootdx TCP 7709 → AkShare `stock_zh_a_hist` → Sina daily | Use cached last close, mark stale | mootdx is direct connection, no HTTP overhead |
| K-line OHLCV (ETF) | AkShare `fund_etf_hist_em` → `fund_etf_hist_sina` | Record `[数据缺失: etf kline]` | Separate endpoint per asset class |
| Technical indicators | Local compute via stockstats | Manual calc from K-line | No network needed |
| VPA indicators | Local compute via numpy | Skip VPA, mark `[数据缺失: vpa]` | No network needed |
| Fund flow (个股) | Eastmoney push2 | Record `[数据缺失: fund flow]` | Unique to Eastmoney |
| Northbound flow | THS `hsgtApi` → Eastmoney | Use real-time snapshot + local CSV accumulation | Reduce Eastmoney dependency |
| Dragon-Tiger Board | Eastmoney datacenter | News search fallback, lower confidence | Unique to Eastmoney |
| Lockup calendar | Eastmoney datacenter | Announcement search | Unique to Eastmoney |
| News / announcements | CLS telegraph → Sina → Eastmoney | Record `[数据缺失: news]` | Reduce Eastmoney dependency |
| Fundamentals | Tencent valuation + Sina statements → Eastmoney F10 → THS EPS | Record `[数据缺失: fundamentals]` | Reduce Eastmoney dependency |
| Sector/concept blocks | Baidu `finance.pae.baidu.com` → Eastmoney sector | Record `[数据缺失: sector]` | Baidu has richer concept classification |

**Routing rules:**

- The primary source is tried first; on failure, fall through the chain **once** — do not loop.
- Eastmoney is used as **primary only** for data unique to it (fund flow, dragon-tiger, lockup); for everything else it is last-resort to protect the rate budget.
- Any failed fetch records `[数据缺失: source/field]`. If the missing field is mandatory for the action decision, block the affected trading advice instead of lowering the evidence standard. Never retry endlessly (see `fallback_action` in `configuration.md`).
- Quote collection is mandatory after codes are confirmed. During trading hours, use live quote fields; outside trading hours, use the latest completed trading session's open/high/low/close/turnover data and set `market_session` to `closed_latest_session`.

## Symbol Resolution

### Stocks

- Resolve by 6-digit code when visible (e.g., 600519, 000858).
- If only Chinese name is visible:
  1. Search exact name first in stock name → code mapping.
  2. Try partial matches if exact fails.
  3. Fetch public quotes for candidates and compare the screenshot price.
  4. If multiple matches remain or no candidate is within 2% of the screenshot price, ask the user to input/confirm the code before archive upload.
- Suffix normalization: Shanghai stocks end in 6xx → `.SH`; Shenzhen stocks start with 0xx/3xx → `.SZ`; STAR market 688xxx → `.SH`.

### ETFs

- Broker display names are often ambiguous. Match by name plus screenshot price.
- If live quote price conflicts with screenshot price by more than 2%, do not use that code.
- If uncertain after public matching, ask the user to input/confirm the ETF code and do not upload the archive until confirmed.
- Common ETF name collisions: always verify by cross-checking the tracking index.

### Confirmation Rule

- `high`: one public symbol candidate and quote price within 2% of the screenshot price.
- `medium`: one likely candidate but name is abbreviated or price is from a closed/latest session; state the assumption and still record source/time.
- `needs_user_confirmation`: no match, multiple plausible matches, or price conflict >2%. Ask the user a concise question with the candidate list and pause persistence.
- Do not upload `UNKNOWN-*` holdings. If the user has not confirmed the code, finish the visible advice with `[未持久化: 待确认代码]`.

## VPA (Volume Price Analysis) Pre-Computation

Before analysts interpret volume-price relationships, pre-compute the following from OHLCV data:

| Indicator | Method | Interpretation Use |
|---|---|---|
| OBV (On-Balance Volume) | Cumulative volume based on price direction | Trend confirmation / divergence |
| Volume ratio (量比) | Current volume / 5-day average volume | Breakout confirmation |
| Bar type classification | Compare open/close/high/low relationships | Hammer, shooting star, engulfing, doji |
| Volume-price divergence | Price up + volume down = bearish divergence | Distribution / accumulation signals |
| Selling climax detection | Extreme volume + price drop + recovery | Potential reversal signal |
| VWMA (Volume Weighted MA) | MA weighted by volume | More reliable trend than simple MA |

Pre-computation saves tokens and ensures consistency across analyst interpretations. Use stockstats library or numpy for calculations.

## Optional Python Execution

When the task involves several holdings, hot-sector candidates, or repeated timed runs, use Python when helpful. See `python-execution.md` for dependency rules.

- Batch quote requests into one URL where possible.
- Sleep between Eastmoney requests to reduce rate-limit risk.
- Parse JSON into a compact evidence table before writing the debate.
- Calculate technical indicators and candidate scores programmatically.
- Keep the script read-only; do not query local development databases as holdings.
- If a request fails, record `[数据缺失: source/field]`; continue only for non-critical fields, otherwise block the affected trading advice.

## Mandatory Data Checklist

For concentrated or risky positions, collect as much of this as possible:

| Analyst Lens | Must Check |
|---|---|
| Market/technical | Latest price/date/pct change; 30-day return; 5-day vs 20-day volume; at least 3 indicators (RSI/MACD/MA/Bollinger/ATR); support/resistance; VPA signals (OBV divergence, volume ratio, bar type) |
| Sentiment | News count/time window; positive/negative/neutral split; top 3 topics; sentiment grade; warming/cooling trend |
| News | Company news count; macro/sector news; 3-event timeline; bullish/bearish/neutral counts; explicit risk events |
| Fundamentals | PE TTM, PB, market cap; revenue growth; net profit growth; ROE; debt ratio; operating cash flow vs net profit; forward EPS / PEG |
| Policy | Recent policy list with date/source; supportive/restrictive/neutral; impact strength; time window; policy rating |
| Hot money/capital | 5-day volume trend; northbound flow; individual main fund flow; concept blocks and sector move; hot-stock presence; capital-flow rating |
| Lockup/reduction | 6-month insider/major-holder activity; top holder change; lockup/reduction news; reduction-pressure grade; next 90 days risk |
| Buy candidates | Today's leading sectors/themes; candidate score; entry trigger; initial size; take-profit; stop-loss; invalidating condition |

If a field is unavailable, mark `[数据缺失: field]`. If it is mandatory for an action decision, block that advice and state the next collection step.

## Data Quality Gate

Before decision synthesis, apply the two-layer quality gate described in `multi-agent-workflow.md`.

Quick reference:
- Layer 2 (LLM review) only runs if fewer than 4 reports fail Layer 1 hard checks (to save tokens).
- Empty or very short evidence is low quality.
- A report made mostly of "unable to fetch" is low quality.
- Missing 3+ mandatory fields for a heavy holding requires caution.
- If hot-sector or candidate-pool data fails, block new-buy advice and state the missing sector/candidate source instead of filling with proxy-only candidates.
- If most data checks fail, output a data warning and avoid aggressive new buys.
- Quality grade must be explicitly stated in the evidence pack.
