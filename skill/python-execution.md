# Python Execution

Use Python when manual API calls or calculations would make the intraday analysis slow, fragile, or incomplete.

This file integrates the centralized data collection pattern from `TradingAgents-AShare`'s DataCollector (fetch once, serve many), the concurrency control from `TradingAgents-astock`'s Eastmoney throttling, and VPA pre-computation from both repos.

## When To Use Python

Use Python scripts when any applies:

- The portfolio has multiple holdings and candidate targets (batch efficiency).
- Live quotes, index data, ETF candidates, news, announcements, or sector ranks need batch fetching.
- Technical indicators such as MA, RSI, MACD, Bollinger, ATR, VWMA, MFI, volume ratios, or support/resistance need calculation.
- VPA indicators (OBV, volume ratio, bar type classification, volume-price divergence, selling climax detection) need pre-computation.
- Data from several sources must be normalized into one evidence table.
- A repeatable market snapshot is needed for 09:25, 10:00, 12:00, or 14:30 runs.
- Candidate scoring requires programmatic comparison across multiple stocks/ETFs.

## Dependency Policy

Installing dependencies is allowed when necessary for data collection or parsing.

Prefer this order:

1. **Standard library + already installed packages**.
2. **Minimal widely used packages**: `requests`, `beautifulsoup4`, `lxml`, `pandas`, `numpy`.
3. **Finance data packages** only when justified by the data need:
   - `akshare`: A-share/HK comprehensive data (Eastmoney/Sina/THS aggregator).
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
| K-line OHLCV | mootdx TCP 7709 first | Direct connection, no HTTP overhead |
| Technical indicators | Local computation via stockstats | No network needed |
| VPA indicators | Local computation via numpy | No network needed |
| Fund flow | Eastmoney push2 | Unique data, no alternative |
| Dragon-tiger board | Eastmoney datacenter | Unique data, no alternative |
| Lockup calendar | Eastmoney datacenter | Unique data, no alternative |
| News | CLS telegraph or Sina first | Reduce Eastmoney dependency |
| Fundamentals | Tencent + Sina | Reduce Eastmoney dependency |
| Northbound flow | THS hsgtApi first | Reduce Eastmoney dependency |

### Centralized Data Prefetch Pattern

Inspired by `TradingAgents-AShare`'s DataCollector:

1. **Resolve all tickers first**: Chinese name → 6-digit code mapping.
2. **Batch-fetch shared data** (fetched once, used by all analysts):
   - Major indices (上证/深证/创业板).
   - Sector fund flow ranking.
   - Northbound flow snapshot.
   - Global context (US/HK/commodities).
3. **Batch-fetch per-holding data** (fetched once per holding):
   - Real-time quotes for all holdings in one batch URL.
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

```python
####################### 标准化输出格式 #######################
evidence = {
    "timestamp": "2026-06-18 10:00",
    "holdings": [
        {
            "name": "贵州茅台",
            "code": "600519",
            "code_confidence": "high",
            "quote": {
                "price": 1680.00,
                "pct_change": -1.2,
                "open": 1695.00,
                "high": 1700.00,
                "low": 1675.00,
                "prev_close": 1700.50,
                "turnover": 45.2e8,
                "volume_ratio": 1.3
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
            "invalidation": "板块跌超2%或北向净流出超50亿"
        }
    ]
}
```

If a source fails, output `[数据缺失: source/field]` instead of silently filling values. Reduce confidence grade for affected holdings.

## Error Handling

- If a data source is completely unavailable, record `[数据缺失: source名称]` and continue with remaining sources.
- If Python execution fails entirely, fall back to manual WebFetch/curl for critical data (real-time quotes, index status).
- Never let a Python error prevent the skill from producing advice — degrade gracefully to available data.
