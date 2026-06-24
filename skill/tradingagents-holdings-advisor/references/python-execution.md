# Python Execution

Use Python when manual API calls or calculations would make the intraday analysis slow, fragile, or incomplete.

This file integrates the centralized data collection pattern from `TradingAgents-AShare`'s DataCollector (fetch once, serve many), the concurrency control from `TradingAgents-astock`'s Eastmoney throttling, `TradingAgents`' verified data-access contract, and VPA pre-computation from both A-share repos.

## When To Use Python

Use Python scripts when any applies:

- The portfolio has multiple holdings and candidate targets (batch efficiency).
- Live quotes, index data, ETF candidates, news, announcements, or sector ranks need batch fetching.
- Technical indicators such as MA, RSI, MACD, Bollinger, ATR, VWMA, MFI, volume ratios, or support/resistance need calculation.
- VPA indicators (OBV, volume ratio, bar type classification, volume-price divergence, selling climax detection) need pre-computation.
- Data from several sources must be normalized into one evidence table.
- A repeatable market snapshot is needed for 09:25, 10:00, 12:00, or 14:30 runs.
- Candidate scoring requires programmatic comparison across multiple stocks/ETFs.

## Fast Market Snapshot Script

For screenshot or multi-holding runs, use the bundled script first after codes
are confirmed:

```bash
python skill/tradingagents-holdings-advisor/scripts/market_snapshot.py \
  --holdings holdings.json \
  --output evidence_snapshot.json
```

If only codes are known:

```bash
python skill/tradingagents-holdings-advisor/scripts/market_snapshot.py \
  --codes 600519,000001,512480 \
  --output evidence_snapshot.json
```

Use `--no-network` only for tests or source outages. A no-network snapshot is not
enough for quote-dependent trading advice; it should produce a blocker for
actions that need current prices.

### Mandatory Final Quote Refresh

Before writing the final visible action table, refresh quote fields against the
current snapshot:

```bash
python skill/tradingagents-holdings-advisor/scripts/market_snapshot.py \
  --refresh-final evidence_snapshot.json \
  --output evidence_snapshot.final.json
```

Rules:

- Use `evidence_snapshot.final.json` for the final holding action table and buy
  candidate triggers.
- Update only quote-sensitive fields after refresh: current price, pct change,
  quote time, market session, trigger distance, stop/invalidation distance.
- Do not rerun the full debate unless refreshed prices moved enough to invalidate
  a hard trigger, stop, or risk constraint (`final_refresh_rerun_debate_threshold_pct`).
- If final refresh fails, state `[最终行情刷新失败: source/reason]` and block only
  quote-dependent execution that cannot be verified.

## Dependency Policy

Installing dependencies is allowed when necessary for data collection or parsing.

Prefer this order:

1. **Standard library + already installed packages**.
2. **Small widely used packages**: `requests`, `beautifulsoup4`, `lxml`, `pandas`, `numpy`.
3. **Finance data packages** only when justified by the data need:
   - `akshare`: A-share/HK comprehensive data aggregator. Do not install it just for data already available through direct HTTP routes in `market_snapshot.py`.
   - `stockstats`: Technical indicator computation from OHLCV DataFrames.
   - `mootdx`: Direct connection to Tongdaxin servers (K-line, F10).
   - Check whether direct public APIs are insufficient before installing.

Keep installs minimal:

- Install only the package needed for the current task.
- Prefer `python -m pip install --user <package>` or a temporary virtual environment.
- Do not install unrelated large toolchains.
- If installation fails, continue with available sources and mark the missing data.

## Concurrency Control

Inspired by `TradingAgents-astock`'s `_em_get()` and `TradingAgents-AShare`'s `_AkshareLock`:

### Quality-First Parallel Collection

Routine screenshot portfolio analysis should target `target_advice_sec` = 600
seconds, but this is a progress target rather than a hard cutoff. Use multiple
ticker-worker subagents or Python worker threads for independent data fetches,
but keep Eastmoney throttling global. If the run exceeds 10 minutes, report
which mandatory evidence is still pending instead of issuing lower-quality
advice.

Recommended collection sequence:

| Stage | Work |
|---|---|
| Shared prefetch | Run `market_snapshot.py`, symbol confirmation, Tencent/Sina batch quotes, major indices, sector heat |
| Ticker workers | Split holdings into up to `max_ticker_workers` bundles; fetch K-line, VPA, news, fundamentals, fund-flow fallback per bundle |
| Candidate workers | Scan non-held new-position ETFs/stocks and existing-holding add-on eligibility from hot sectors in parallel with ticker workers |
| Merge + quality gate | Normalize evidence, mark missing fields, grade whether action advice is allowed |
| Final refresh + synthesis + archive | Refresh quote fields, show advice first, then archive Markdown/holdings/screenshot if persistence is configured |

Rules:

- Start with batch quote/index/sector requests before per-ticker work.
- Treat the snapshot JSON as the single source of current run evidence. Analysts
  should read from it instead of issuing their own duplicate quote/sector/news
  calls.
- Partition holdings by risk priority, not by screen order: heavy losers and
  largest market-value names get first worker slots.
- Use `ThreadPoolExecutor(max_workers=max_ticker_workers)` or separate
  ticker-worker subagents for non-Eastmoney routes. A worker owns one bundle and
  returns a normalized evidence dict; it must not write holdings or upload runs.
- Keep one process-wide Eastmoney semaphore and one process-wide timestamp
  throttle. Eastmoney calls across all workers still obey `em_max_concurrent`
  and `em_min_interval_sec`.
- Set every request timeout to `per_source_timeout_sec` unless a source
  explicitly needs longer. Retry at most once on the next configured fallback.
- If a mandatory route chain fails, mark the exact missing fields and block the
  affected trading advice instead of filling with a weak substitute.
- If elapsed time exceeds `progress_notice_sec`, state which mandatory data is
  still being fetched and why it is needed for advice quality.
- If elapsed time makes initial quotes older than `final_quote_refresh_max_age_sec`,
  refresh quotes before output rather than restarting the whole analysis.
- Upload/archive is never allowed to consume time before the user sees the
  final advice; persistence happens only after visible output.

Worker sketch:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def collect_ticker_bundle(bundle):
    out = []
    for holding in bundle:
        out.append(fetch_one_holding_with_fallbacks(holding))
    return out

with ThreadPoolExecutor(max_workers=4) as pool:
    futures = [pool.submit(collect_ticker_bundle, b) for b in bundles]
    for f in as_completed(futures):
        evidence["holdings"].extend(f.result())
```

### Script Output Use

`market_snapshot.py` emits:

- `schema_version`: snapshot contract version.
- `timestamp`: initial snapshot time.
- `source_chain`: configured provider chain per data type.
- `holdings[]`: normalized holdings with quote, fund-flow, concept-block, VPA, and per-holding quality fields when available.
- `holdings[].quote.source_chain`: exact quote routes attempted for the code.
- `market.major_indices`: shared broad-market quote snapshot.
- `market.hot_sectors`: shared sector heat / fund-flow ranking.
- `market.northbound`: real-time northbound snapshot plus local CSV history only.
- `market.news`: shared market news headlines.
- `missing_fields[]`: exact missing quote/data keys.
- `errors[]`: fetch errors preserved for audit/debugging.
- `quality_gate`: A/B/C/D/F grade plus `action_allowed`, `new_buy_allowed`, and blockers.
- `final_quote_refresh_at`: present only after final refresh.

If `holdings[].quote.source` is `[数据缺失: quote]`, do not issue an executable
buy/sell/reduce instruction for that holding. State the missing source chain and
the next collection step.

If `quality_gate.new_buy_allowed` is false because sector, concept, or capital-flow evidence is missing, output only conditional watch triggers for new candidates. Do not turn missing hot-sector data into a weak executable buy.

### Eastmoney Throttling

All Eastmoney requests must follow these rules:

- **Minimum interval**: 1 second between repeated Eastmoney requests.
- **Random jitter**: Add 0.1-0.5s random delay to avoid pattern detection.
- **Session reuse**: Use `requests.Session()` with Keep-Alive headers.
- **Batch first**: Prefer batch quote APIs over individual queries.
- **Concurrent slot limit**: At most 5 concurrent Eastmoney requests total; 3 for scheduled/automated tasks.
- **Zombie detection**: If a request holds a slot for > 120 seconds, consider it timed out and release the slot.

### Multi-Source Strategy

Route requests to minimize Eastmoney load:

| Data Type | Route To | Reason |
|---|---|---|
| Real-time quotes | Tencent `qt.gtimg.cn` first | No rate limit, GBK encoding |
| K-line OHLCV | Sina/direct HTTP first, then mootdx/Eastmoney | Avoid stale latest-day gaps and look-ahead bias |
| Technical indicators | Local computation via stockstats | No network needed |
| VPA indicators | Local computation via numpy | No network needed |
| Fund flow | Eastmoney push2 | Unique data, no alternative |
| Dragon-tiger board | Eastmoney datacenter | Unique data, no alternative |
| Lockup calendar | Eastmoney datacenter | Unique data, no alternative |
| News | CLS telegraph or Sina first | Reduce Eastmoney dependency |
| Fundamentals | Tencent + Sina | Reduce Eastmoney dependency |
| Northbound flow | Real-time snapshot + local CSV history | Public historical routes can be stale or empty |

### Centralized Data Prefetch Pattern

Inspired by `TradingAgents-AShare`'s DataCollector:

1. **Resolve all tickers first**: Chinese name → 6-digit code mapping.
   - If a name is ambiguous, compare public quote prices with the screenshot price.
   - If no single candidate is within the 2% tolerance, ask the user to confirm/input the code and pause persistence.
2. **Batch-fetch shared data** (fetched once, used by all analysts):
   - Major indices (上证/深证/创业板).
   - Sector fund flow ranking.
   - Northbound flow snapshot.
   - Global context (US/HK/commodities).
   - `source_chain`, `missing_fields`, and `quality_gate` for verified access auditing.
3. **Batch-fetch per-holding data** (fetched once per holding):
   - Quotes for all confirmed holdings in one batch URL. Use live quotes during trading hours; after close, use latest completed trading session data.
   - K-line history for technical indicators + VPA.
4. **Sequential fetch per-holding unique data** (with rate limiting):
   - Individual news, fundamentals, capital flow, lockup, insider transactions.
5. **Pre-compute all derived data** before analysts need it:
   - Technical indicators from K-line via stockstats.
   - VPA indicators from OHLCV via numpy.
   - Support/resistance levels from recent high/low/MA.
   - Candidate scores from multi-factor comparison.

### Dedup Lock + TTL Cache

Inspired by `TradingAgents-AShare`'s `_AkshareLock`: the same data point must not be fetched twice within a short window. All parameters below are in `configuration.md`.

- **TTL window**: `dedup_ttl_sec` = 30 seconds. A cached result within TTL is returned without a network call.
- **Lock scope**: `dedup_scope` = per `(ticker, data_type)`. The lock key is the tuple, so 600519-quote and 600519-fundflow are independent.
- **Lock behavior**: if a fetch for a key is in flight, concurrent callers wait on the same lock and reuse the result — they do not issue a second request.
- **Cache eviction**: oldest entries evicted first when the cache grows; a single run rarely exceeds a few dozen keys.
- **Fallback on fetch error**: record `[数据缺失: source/field]` per `fallback_action`, do not cache failures (so the next caller retries once).

Reference implementation skeleton:

```python
####################### 去重锁 + TTL 缓存 #######################
import time, threading
from functools import wraps

_cache = {}            # key -> (value, fetched_at)
_locks = {}
_locks_guard = threading.Lock()

def _get_lock(key):
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]

def dedup_fetch(ttl_sec=30):
    """Decorator: cache a fetch result keyed by (ticker, data_type) for ttl_sec."""
    def decorator(fetch_fn):
        @wraps(fetch_fn)
        def wrapper(ticker, data_type, *args, **kwargs):
            key = (ticker, data_type)
            lock = _get_lock(key)
            with lock:
                now = time.time()
                if key in _cache and now - _cache[key][1] < ttl_sec:
                    return _cache[key][0]          # TTL hit, no network
                value = fetch_fn(ticker, data_type, *args, **kwargs)
                _cache[key] = (value, now)         # cache success only
                return value
        return wrapper
    return decorator
```

### Thread-Safe Implementation

If using concurrent fetching:
- Use the dedup lock above (per `(ticker, data_type)`) to prevent duplicate fetches.
- Use a shared `requests.Session()` object for connection pooling.
- Use a semaphore sized at `em_max_concurrent` for Eastmoney request concurrency.
- Record fetch timestamps (handled by the TTL cache) to avoid re-fetching within `dedup_ttl_sec`.

## VPA (Volume Price Analysis) Pre-Computation

Before analysts interpret data, pre-compute these indicators from OHLCV:

```python
####################### VPA 量价分析预计算 #######################
# 输入: OHLCV DataFrame (columns: open, high, low, close, volume)
# 输出: VPA signals dictionary

def compute_vpa(df):
    signals = {}

    # OBV (On-Balance Volume) — 趋势确认/背离
    obv = [0]
    for i in range(1, len(df)):
        if df['close'][i] > df['close'][i-1]:
            obv.append(obv[-1] + df['volume'][i])
        elif df['close'][i] < df['close'][i-1]:
            obv.append(obv[-1] - df['volume'][i])
        else:
            obv.append(obv[-1])
    signals['obv_trend'] = 'up' if obv[-1] > obv[-5] else 'down'
    signals['obv_divergence'] = (
        (df['close'].iloc[-1] > df['close'].iloc[-5]) and (obv[-1] < obv[-5])
    ) or (
        (df['close'].iloc[-1] < df['close'].iloc[-5]) and (obv[-1] > obv[-5])
    )

    # Volume ratio (量比) — 突破确认
    avg_vol_5d = df['volume'].iloc[-6:-1].mean()
    signals['volume_ratio'] = df['volume'].iloc[-1] / avg_vol_5d if avg_vol_5d > 0 else 0

    # Bar type classification (K线形态)
    last = df.iloc[-1]
    body = abs(last['close'] - last['open'])
    range_ = last['high'] - last['low']
    if range_ > 0 and body / range_ < 0.1:
        signals['bar_type'] = 'doji'  # 十字星
    elif last['close'] > last['open'] and (last['open'] - last['low']) > 2 * body:
        signals['bar_type'] = 'hammer'  # 锤子线
    elif last['close'] < last['open'] and (last['high'] - last['open']) > 2 * body:
        signals['bar_type'] = 'shooting_star'  # 射击之星
    else:
        signals['bar_type'] = 'normal'

    # Volume-price divergence (量价背离)
    price_up = df['close'].iloc[-1] > df['close'].iloc[-5]
    vol_up = df['volume'].iloc[-1] > df['volume'].iloc[-5:-1].mean()
    signals['bullish_divergence'] = not price_up and vol_up  # 价跌量增(恐慌/换手)
    signals['bearish_divergence'] = price_up and not vol_up  # 价涨量缩(无量上涨)

    # Selling climax detection (放量下跌后企稳)
    recent_low = df['close'].iloc[-5:].min()
    recent_vol_max = df['volume'].iloc[-5:].max()
    avg_vol = df['volume'].iloc[-20:].mean()
    signals['selling_climax'] = (
        recent_vol_max > 2 * avg_vol and
        df['close'].iloc[-1] > recent_low  # 放量后企稳
    )

    # VWMA (Volume Weighted Moving Average)
    signals['vwma_20'] = (df['close'] * df['volume']).iloc[-20:].sum() / df['volume'].iloc[-20:].sum()

    return signals
```

## Script Rules

Python scripts must be read-only with respect to holdings:

- Use the screenshot/current conversation/history as the holdings input.
- Do not query local broker apps, local databases, caches, or development systems for holdings.
- It is acceptable to query public market/news/fundamental APIs using codes extracted from the screenshot.
- Save reusable scripts under `scripts/` only when the logic will be reused; one-off scripts can run inline.

## Output Contract

When Python is used, normalize results before reasoning:

The current bundled script's minimum verified contract is `schema_version`,
`timestamp`, `source_chain`, `holdings[]`, `market`, `missing_fields[]`,
`errors[]`, and `quality_gate`. The richer example below shows optional fields
that may be appended by OCR, deeper fundamentals, K-line workers, or archive
packaging; do not require those optional fields before the script can be useful.

```python
####################### 标准化输出格式 #######################
evidence = {
    "timestamp": "2026-06-18 10:00",
    "screenshot": {
        "filename": "holdings-2026-06-18.png",
        "mime_type": "image/png",
        "data_url": "data:image/png;base64,...",
        "captured_at": "2026-06-18T10:00:00+08:00",
        "source": "user_upload"
    },
    "account": {
        "total_assets": 219165.49,
        "total_market_value": 203481.00,
        "broker_available_cash": 422.43,
        "corrected_unused_funds": 15684.49,  # total_assets - total_market_value
        "repo_or_standard_bond_value": 37000.00,
        "unused_or_repo_occupied_funds": 52684.49,
        "cash_rule_note": (
            "Use total_assets - total_market_value when both are visible; "
            "exclude 新标准券/国债逆回购 from holdings and treat it as unused/repo funds."
        )
    },
    "holdings": [
        {
            "name": "贵州茅台",
            "code": "600519",
            "code_confidence": "high",
            "qty": 100,
            "available_qty": 100,
            "unavailable_qty": 0,
            "cost": 1700.00,
            "price": 1680.00,
            "market_value": 168000.00,
            "pnl": -0.0117647059,
            "pnl_amount": -2000.00,
            "screenshot_price": 1680.00,
            "quote": {
                "price": 1680.00,
                "pct_change": -1.2,
                "open": 1695.00,
                "high": 1700.00,
                "low": 1675.00,
                "prev_close": 1700.50,
                "turnover": 45.2e8,
                "volume_ratio": 1.3,
                "source": "Tencent qt.gtimg.cn",
                "quote_time": "2026-06-18 10:00:03",
                "market_session": "trading"
            },
            "technicals": {
                "rsi_14": 45.2,
                "macd_signal": "below_zero",
                "ma_5": 1690, "ma_20": 1710,
                "bollinger_position": "lower_half"
            },
            "vpa": {
                "obv_trend": "down",
                "obv_divergence": False,
                "volume_ratio": 1.3,
                "bar_type": "normal",
                "bearish_divergence": False,
                "selling_climax": False,
                "vwma_20": 1695
            },
            "fund_flow": {
                "super_large_net": -2.1e8,  # [数据缺失: source/field] if unavailable
                "large_net": -0.8e8,
                "northbound_net": "[数据缺失]"
            },
            "red_flags": [],
            "data_quality": "B"
        }
    ],
    "excluded_items": [
        {
            "name": "新标准券",
            "display_value": 37000.00,
            "reason": "国债逆回购/standard bond cash-management item; exclude from holdings and count as unused/repo funds"
        }
    ],
    "market": {
        "shanghai_index": {"price": 3280, "pct_change": -0.3},
        "northbound_net": 12.5e8,
        "limit_up_count": 45,
        "limit_down_count": 8,
        "hot_sectors": ["半导体", "人工智能", "军工"]
    },
    "candidates": [
        {
            "name": "半导体ETF",
            "code": "512480",
            "score": 7.5,
            "entry_trigger": "突破1.050",
            "initial_size": "10%",
            "take_profit_1": "1.080",
            "take_profit_2": "1.120",
            "stop_loss": "1.020",
            "invalidation": "板块跌超2%或北向净流出超50亿",
            "recommendation_reason": {
                "news": "半导体政策/订单催化仍在发酵，无重大负面公告",
                "capital_flow": "板块和ETF资金净流入，个券/ETF无明显出货",
                "sector_position": "板块日内排名靠前，5日强于20日但未进入明显高位衰退"
            }
        }
    ]
}
```

If a source fails, output `[数据缺失: source/field]` instead of silently filling values. Reduce confidence grade for affected holdings.

Before reasoning or upload, validate every holding P/L ratio against the screenshot layout and `(price - cost) / cost` when both values exist. `holdings[].pnl` is a decimal return ratio only. For a normal 同花顺/券商 two-line 盈亏 cell, parse line 1 as `pnl_amount` and line 2 as percent-unit `pnl`; convert line 2 to decimal and do not add `pnl_corrections`. If there is no separate percent line and OCR only yields one amount-like value, store it in `holdings[].pnl_amount`, compute `holdings[].pnl` from price/cost, and add `pnl_corrections` only when this was a true ambiguous or conflicting single-value correction; Phase 6 stores the normalized holdings JSON in the archive.

When account totals are visible, compute `account.corrected_unused_funds` as
`total_assets - total_market_value`. Keep broker `available_cash` as a separate
field only, because pending-order funds may not have returned to availability.
If a row is named "新标准券"/standard bond/treasury reverse repo/国债逆回购, do not
append it to `holdings[]`; put it in `excluded_items[]` and account repo/unused
funds fields.

For quote failures, record the failed source chain in `missing_fields` and the visible evidence pack. If all quote routes fail for a confirmed holding, do not issue trading advice for that holding. Do not call legacy backend health-reporting endpoints; the active skill persistence contract is archive upload only.

## Error Handling

- If a non-critical data source is completely unavailable, record `[数据缺失: source名称]` and continue with remaining sources.
- If a mandatory source chain is unavailable, stop the affected trading advice and list the missing data plus next fetch step.
- If Python execution fails entirely, fall back to manual WebFetch/curl for critical data (real-time quotes, index status).
- If code resolution is uncertain after public matching, ask the user to confirm the code and do not upload the run until confirmation is available.
- Never let a Python error silently lower advice quality. Recover critical data manually; if it cannot be recovered, block trading advice for the affected scope.
