# Buy Candidate Selection

Use this file whenever the user asks for today's operation advice, rebalance advice, or any execution run. The output must include buy/rotation candidates unless the user explicitly asks for risk-control-only advice.

This file integrates the signal data layer from `TradingAgents-astock` (northbound flow, dragon-tiger board, concept blocks, industry comparison, hot stocks with theme attribution) and the centralized candidate scoring from `TradingAgents-AShare`.

## Required Buy Module

Every execution must answer:

1. Which hot sectors/themes are leading today?
2. Is the market mood suitable for new buys, only rotation, or no buys?
3. Which 1-2 stocks or ETFs are the best candidates?
4. What are the entry trigger, position size, take-profit, stop-loss, and invalidation rules?
5. What condition cancels all new buys today?

If data quality is weak or the portfolio exposure is too high, still provide a "watch only / conditional buy" candidate table and state why orders should wait.

## Hot Sector Scanner — Three-Layer Architecture

### Layer 1: Broad Market Context (大盘环境)

| Check | Data Source | Signal |
|---|---|---|
| Major index trend | Eastmoney push2 / AkShare | 上证/深证/创业板/科创 日内趋势 |
| Turnover & breadth | Eastmoney market stats | 涨跌家数比, 涨停/跌停家数, 成交额 |
| Limit-up/down mood | AkShare `stock_zt_pool_em` | 涨停板连板数, 板块聚集度 |
| Overseas/HK risk appetite | Eastmoney global | 美股期货, 恒指, USD/CNH |
| Northbound flow direction | THS hsgtApi / Eastmoney | 北向当日净流入/流出 |

### Layer 2: Hot Sectors & Themes (热门板块/题材)

| Check | Data Source | Signal |
|---|---|---|
| Sector/concept rank | Eastmoney sector API, Baidu concept blocks | 涨幅前10板块, 概念板块轮动 |
| ETF leaders | Eastmoney ETF rank | 涨幅前10 ETF, 对应板块 |
| Sector fund flow | Eastmoney push2 fund flow | 板块资金净流入排名 |
| THS hot stocks with theme tags | THS hot stocks API | 编辑标注的题材归因 |
| Dragon-Tiger Board clustering | Eastmoney datacenter | 龙虎榜上榜个股的板块聚集 |
| News/policy catalysts | CLS telegraph, Eastmoney news | 驱动板块的政策/事件催化 |

### Layer 3: Candidate Tape Check (个股/ETF盘面验证)

| Check | Data Source | Signal |
|---|---|---|
| Price vs open/prev close | Real-time quote | 是否在今开和昨收之上 |
| MA alignment | Computed from K-line | 价格 > MA5 > MA20 为佳 |
| Volume expansion | K-line + stockstats | 量比 > 1.5, 成交量较5日均量放大 |
| VPA signals | Pre-computed from OHLCV | OBV趋势, 量价配合, 无背离 |
| Main fund flow | Eastmoney push2 individual | 超大单/大单净流入 |
| Turnover rate | Real-time quote | 换手率适中(3%-15%), 不过低(流动性差)不过高(出货嫌疑) |
| Liquidity check | Real-time quote | 日均成交额 > 5000万(个股) / > 1亿(ETF) |

## Candidate Pool Construction Strategy

### Step 1: Sector Heat Map → Shortlist

From Layer 2 data, identify today's top 3-5 sectors/themes by:
- Sector rank position (涨幅前5).
- Sector fund flow (净流入前5).
- News/policy catalyst count.
- Limit-up clustering (涨停板聚集).

### Step 2: Sector → Candidate Mapping

For each hot sector, find the best expression:

| Expression Type | When to Prefer | Examples |
|---|---|---|
| Sector ETF | Sector clear but stock selection risky; user concentrated in losers; data quality incomplete | 半导体ETF(512480), 新能源ETF(516160) |
| Sector leader (龙头) | Clear catalyst + capital confirmation + tradable liquidity; stronger than peers | 板块内涨幅前3且资金净流入的个股 |
| Concept/theme play | Strong news catalyst + early rotation stage; higher risk | 概念板块中有独特题材标签的个股 |

### Step 3: Candidate Filtering

Remove candidates that:
- Are below both open and previous close (弱势).
- Have main funds materially outflowing while price rises (假突破).
- Are already in the user's portfolio (重复).
- Have fresh negative announcement, reduction, pledge, ST/delist risk.
- Have turnover rate > 25% (possible distribution).
- Have VPA divergence signals (量价背离).

## Candidate Scoring System

Score each candidate from 0-10. Only name candidates with score 7+ as "buyable"; score 5-6 is "watch only"; below 5 should not be recommended.

| Factor | Points | Evidence Required |
|---|---:|---|
| Sector heat (板块热度) | 0-2 | Sector/concept rank in top 5, ETF strength, breadth, limit-up clustering |
| News/policy catalyst (催化剂) | 0-2 | Fresh supportive policy, earnings/news catalyst, no rumor-only thesis |
| Technical setup (技术形态) | 0-2 | Above open and previous close, price > MA5 > MA20, breakout with volume |
| VPA confirmation (量价确认) | 0-1 | OBV uptrend, volume expansion, no bearish divergence, no selling climax |
| Capital flow (资金流向) | 0-1 | Main funds/northbound/hot money confirm, no obvious distribution |
| Portfolio fit (组合匹配) | 0-2 | Reduces concentration risk, position size survivable under T+1, cash remains adequate |

**Score Interpretation:**

| Score | Action | Confidence |
|---|---|---|
| 8-10 | Buyable with normal sizing | High |
| 7 | Buyable with reduced sizing | Medium |
| 5-6 | Watch only — set trigger and wait | Conditional |
| < 5 | Do not recommend | Low |

## Candidate Output

For each candidate, output:

- **Name and code** (名称和代码); mark uncertainty if any.
- **Candidate type** (候选类型): ETF / stock / watch only.
- **Long thesis in one sentence** (一句话看多逻辑).
- **Entry trigger** (入场触发): Exact price or condition.
- **Initial size** (初始仓位): Amount, shares/lots, or portfolio percentage.
- **Take-profit** (止盈): First and second target, or trailing rule.
- **Stop-loss** (止损): Hard price/condition and same-day cancellation rule.
- **Invalidating condition** (取消条件): Sector turns weak, index loses level, fund flow reverses, or news risk appears.
- **Score** (评分): 0-10 with breakdown.

## Buy Risk Gate

Block or downgrade new buys when any applies:

- Account exposure remains above 85% and no weak holdings were reduced first.
- Major index and candidate sector diverge negatively (大盘跌而板块涨 — 不可持续).
- Candidate is below both open and previous close.
- Main funds are materially outflowing while price rises.
- Candidate has fresh negative announcement, reduction, pledge, ST/delist, or major uncertainty.
- Current checkpoint is 14:30 and the buy would be a fresh overnight gamble without strong confirmation.
- VPA shows distribution signals (price up + volume down, or selling climax).
- Northbound flow is materially negative today.
- Data quality is C or worse.

When blocked, phrase the output as "不立即买；满足触发后再买" and still give the trigger plan.

## Portfolio-Aware Buy Rule

Buy recommendations must not conflict with the portfolio plan:

- If the user is overexposed, candidate size should come only from cash raised by reducing weak holdings.
- Do not recommend averaging down current losers as the only buy idea.
- If the strongest existing holding is already the best expression of the hot sector, say "优先持有/小幅加仓现有强势 ETF" instead of adding a new single stock.
- If two candidates are given, make one safer ETF-style candidate and one higher-risk stock-style candidate when evidence supports it.
- If the risk revision loop rejected the original plan, the revised candidate must incorporate the hard constraints.

## Industry Comparison Integration

Before finalizing candidates, use Eastmoney's 90-block industry comparison to verify:

- Is the candidate's sector actually outperforming the broader market today?
- Where does the candidate's sector rank in 5-day and 20-day performance?
- Is the sector at an early, mid, or late stage of its rotation cycle?

Prefer candidates in sectors that are:
- Top 10 in daily performance (early stage, just starting).
- Top 20 in 5-day performance but not top 5 in 20-day (catching up, not yet overextended).

Avoid candidates in sectors that are:
- Top 5 in 20-day performance but fading in daily performance (late stage, distribution risk).
- Not in the top 30 in any timeframe (no momentum).
