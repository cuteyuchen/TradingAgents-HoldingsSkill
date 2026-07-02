# Trading Rules

Use this file when turning analysis into orders. This file integrates A-share trading constraints, the risk revision loop from `TradingAgents-AShare`, and the alpha benchmark reflection from `TradingAgents-astock`.

## A-Share Constraints

Always factor in:

| Rule | Practical Effect |
|---|---|
| T+1 settlement | Shares bought today cannot be sold today; new buys carry overnight risk |
| Price limits | Main board usually +/-10%; ChiNext/STAR usually +/-20%; ST usually +/-5% |
| Limit-down liquidity | A stop-loss may not execute at limit-down; size positions before risk appears |
| Minimum lot | Usually 100 shares per lot; ChiNext/STAR constraints may differ |
| Trading hours | 09:30-11:30, 13:00-15:00 Beijing time |
| ST/delisting risk | Reduce position size or avoid unless explicitly requested/speculative |
| Cash-only assumption | Unless user states margin eligibility, assume no leverage |

For ETFs, use ETF-specific liquidity and tracking index strength, but still respect T+1 and position sizing.

## Time Checkpoint Rules

| Time | Decision Style | Focus |
|---|---|---|
| 09:25 | Plan only | Use auction/gap/news. Avoid aggressive new orders unless a pre-set risk plan exists. Check overnight global markets, HK/US futures, key news. |
| 10:00 | First execution | Compare holdings to open/prev close/sector; trim weak heavy positions. First confirmed intraday trend. |
| 12:00 | Midday adjustment | Reassess sector rotation, capital flow, and whether morning breakout held. Plan afternoon strategy. |
| 14:30 | Risk control | Avoid impulsive new buys; decide what to carry overnight. Last chance to reduce before close. |

## Position Rules

| Situation | Action Bias |
|---|---|
| Account exposure > 85% | Raise cash before considering new buys |
| Market weak and holdings weak | Reduce first, no averaging down |
| Market strong but heavy holdings weak | Use strength/liquidity to reduce weak holdings |
| Heavy loser underperforms index/sector | Evidence-gated split: hold, conditional reduce, reduce, or conditional add; loss alone is not enough |
| Winner stronger than index | Hold or partial take-profit; do not sell first just to raise cash |
| Small position with no red flag | Usually hold unless it distracts capital |
| New red-flag announcement | Reduce even if intraday price is stable |
| Data quality C or worse | Smaller action size; avoid new buys |
| Past decision on same ticker was wrong | Reduce confidence; apply more conservative sizing |

## Loss Position Decision Gate

亏损不是减仓的充分条件。Treat loss as a risk input, not the final action. For
every losing holding, classify the action from current evidence:

| Evidence Pattern | Allowed Action |
|---|---|
| Price is above open/previous close, sector is firm, fund flow is stable or improving, and no fresh red-flag news | Hold; if account exposure allows, use conditional add only after the trigger confirms |
| Price is mixed, sector is not breaking down, fund flow is unclear, and no hard risk exists | Hold or watch; set a conditional reduce trigger below support/open/previous close |
| Price is below open and previous close, underperforms index/sector, and fund flow or VPA confirms weakness | Conditional reduce or staged reduce from `available_qty` |
| Fresh negative announcement, lockup/reduction pressure, ST/delist risk, thesis broken, or limit-down liquidity risk | Reduce or sell as risk control, even if the position is already losing |

Sell/reduce recommendations for losing holdings must name the confirming
evidence: relative weakness, broken support, capital outflow, sector rollover,
negative news, oversized exposure, or T+1/price-limit risk. If those evidence
items are absent, default to hold/watch with explicit invalidation triggers.

## Dual-Horizon Position Rules

For core holdings run under dual-horizon analysis (see `multi-agent-workflow.md` Dual-Horizon section), size the two sleeves separately:

| Sleeve | Default Cap | Purpose | Funding |
|---|---|---|---|
| Short-term sleeve (短线仓) | `short_track_max_ratio` = 15% of portfolio | Tactical trades from the short track (today's trim/add/exit) | Cash from short-track trims recycles into short-track adds only |
| Medium-term base (中线底仓) | Remainder up to `single_position_max_ratio` = 30% per name | Hold/reduce decision from the medium track | Independent of short-term sleeve |

Rules:

- Short-track trades must not pull size from the medium-term base. If the short track says "trim 20%", that cash stays in the short-term sleeve for rotation, not for raising permanent cash unless the medium track also says reduce.
- When both tracks say reduce, treat it as a full reduce (medium-track authority).
- When short says add but medium says hold/reduce, the add is capped at the short-term sleeve and only if `account_exposure_high` (85%) is not breached.
- New buy candidates (see `buy-candidate-selection.md`) draw from the short-term sleeve by default; only move to the medium-term base after the medium track confirms.

## Trigger Design

Use levels that traders can execute:

- **Previous close (昨收)**: Reclaiming or losing it often separates weak/strong intraday behavior.
- **Open price (今开)**: If a holding stays below open while index rises, it is weak.
- **Intraday high/low (日内高低)**: Use failed breakout or breakdown as action trigger.
- **Cost price (成本价)**: Do not use cost as the only reason to hold; use it for psychological/risk sizing.
- **Support/resistance (支撑/阻力)**: Combine with volume and fund flow.
- **VPA signals (量价信号)**: Volume expansion on breakout, OBV trend, divergence patterns.

Always include:
- Action (动作).
- Quantity or percentage (数量或比例).
- Trigger price (触发价格).
- Invalidating condition (失效条件).

## Stop And Reduce Rules

Prefer staged actions:

- **First trim (首次减仓)**: 15%-30% of that holding when weak trigger confirms.
- **Second trim (二次减仓)**: Another 20%-30% if it breaks support or sector turns down.
- **Full exit (清仓)**: Major red flag, limit-down risk, ST/delist risk, or thesis broken.

All sell/reduce quantities must be capped by `available_qty`. `qty` is total
position only. If `qty > available_qty`, the difference is unavailable because
of pending orders, freeze, or T+1 limits; it is not evidence that the position
has already been reduced. If `available_qty` is 0, do not output an executable
sell/reduce order.

Do not propose precise stop-loss on a position that may hit limit-down and become untradeable without warning about execution risk.

Before any new reduce/sell recommendation, compare the archive context timeline
for the last 5 matching snapshots when available. If the current `qty` or
`available_qty` is lower than the most recent archived quantity, assume the user
may already have executed part of a previous reduction. Do not repeat the old
reduction amount. Recompute the new reduce/sell size only from the current
`available_qty`, and state that the current position is already lower than the
recent archive if it affects sizing.

## Add Rules

Only consider adding when most are true:

- Market index and sector are aligned upward.
- Holding is above previous close and open.
- Volume expands without obvious distribution (VPA confirms).
- Main funds/northbound/hot-money data confirm.
- No near-term lockup/reduction/major negative news.
- Portfolio has enough cash after risk-control trims.

Never add simply because:
- The loss is large.
- The stock is "cheap" relative to user's cost.
- The user wants to recover quickly.
- Past decision on this ticker was profitable (avoid recency bias).

## New Buy Candidate Rules

Every daily execution must include 1-2 buy/rotation candidates, even if the action is "watch only".

The buy/rotation candidate module may include either non-held symbols or
existing holdings that deserve more exposure. Label candidate rows clearly:
`新开仓`, `加仓现有持仓`, `条件加仓`, or `轮动观察`.

For current holdings, the candidate row must match the holding action table:
if a holding deserves "加仓", it may also appear as `加仓现有持仓`; if it deserves
"持有不加仓", "减仓", or "卖出", do not repeat it as a buy candidate later in the
report.

Before recommending a buy:

- Scan hot sectors/themes and rank candidates by sector heat, news/policy catalyst, technical setup, capital flow, and portfolio fit.
- Prefer candidates that are above both previous close and open, with price above MA5/MA20 or reclaiming them with volume.
- Prefer candidates with positive VPA signals (volume expansion, OBV uptrend, no divergence).
- Require enough liquidity for staged exits; avoid thin, one-word-theme, or pure limit-up chase candidates.
- If account exposure is above 85%, candidate size must come from selling weak holdings first.
- If data quality is C or worse, only give ETF-style or watch-only candidates.

For each candidate, include:

- Recommendation reason (建议理由), split into 消息面/催化, 资金面, and 板块位置/轮动阶段.
- Entry trigger (入场触发).
- Initial position size (初始仓位).
- First and second take-profit targets, or a trailing stop rule (止盈目标).
- Stop-loss price or condition (止损条件).
- Invalidating condition that cancels the trade (取消条件).

Do not recommend a fresh buy or add-on buy when:

- The candidate code already exists in the current holdings and the holding
  action is not `加仓` or `条件加仓`.
- The candidate is below open and previous close.
- The sector is rising but the candidate is lagging.
- Main funds are materially outflowing.
- The idea depends only on recovering losses from existing holdings.
- It is late session and the trade lacks strong confirmation.
- VPA shows distribution (price up but volume down).
- Any of the three recommendation reason fields (消息面, 资金面, 板块位置) cannot be supported by current evidence.

## Rotation Rules

Rotation candidates can be stocks or ETFs.

Prefer ETFs when:
- The user is already concentrated in single-stock losers.
- Sector direction is strong but single-stock selection risk is high.
- Data quality for individual stocks is incomplete.
- The account needs lower single-name risk while rotating into a hot sector.

Prefer single stocks only when:
- There is clear catalyst + capital confirmation + tradable liquidity.
- Position size remains survivable under T+1 and price-limit risk.
- The stock is stronger than its sector peers, not merely part of a hot label.

## Overnight Carry Rules

Carry overnight only if:

- Position is not oversized.
- It closes above key levels or at least stronger than sector peers.
- No unresolved negative announcement/policy/lockup risk.
- Global/HK/US risk does not directly threaten the position.

If late-session evidence is mixed, reduce rather than add.

## Risk Revision Loop

Inspired by `TradingAgents-AShare`'s risk judge revision mechanism:

After the Trader produces a proposal, the Risk Manager / Portfolio Manager reviews it and can:

1. **Pass (通过)**: Proposal is acceptable. Execute as planned.
2. **Revise (退回修正)**: Proposal violates constraints. Send back with:
   - **Hard constraints (硬性约束)**: Non-negotiable rules that must be followed.
     - Examples: "单只持仓不超过总资产30%", "不追加跌停风险标的", "现金比例不低于15%"
   - **Soft constraints (建议约束)**: Advisory guidance.
     - Examples: "建议分2批建仓", "优先考虑ETF而非个股"
   - **Execution preconditions (执行前提)**: Conditions that must hold before executing.
     - Examples: "需等10:00趋势确认后再执行", "需北向资金转正"
   - **De-risk triggers (去风险触发器)**: Conditions that require immediate position reduction.
     - Examples: "若标的跌破XX元立即减仓50%", "若大盘跌幅超2%暂停所有买入"
   - **Revision reason (退回原因)**: Why the original plan was insufficient.
3. **Reject (否决)**: Proposal is fundamentally flawed. Do not execute. Default to holding/waiting.

Max 1 revision. If the revised proposal still doesn't satisfy constraints, default to reject.

## Trading Memory & Alpha Benchmark

Inspired by `TradingAgents-astock`'s memory log system:

### Recording Decisions

After each execution, record the decision so future runs can reflect on it:
- Ticker, date, action, price at time of advice, confidence level, rating.
- Key reasons for the decision.
- **Storage**: if the persistence system is configured (`ADVISOR_API_URL` set), the Phase 6 archive upload (see `persistence.md`) stores the visible Markdown, holdings JSON, and screenshot automatically. Otherwise, reference prior decisions from conversation history.

### Archive Context Retrieval

When `ADVISOR_API_URL` and `ADVISOR_TOKEN` are configured, Phase 0 may call
`GET /archives/context?codes=...&limit=5` after current codes are confirmed.
This is the only allowed backend history lookup. It is archive-only and
read-only; do not call legacy `/runs`, `/memory/context`, portfolio, watchlist,
or health endpoints.

Use the returned:
- `timeline_by_code` for recent position size, available quantity, cost, price,
  and P/L changes.
- `latest_by_code` for the last known archive snapshot.
- `same_day_advice` and `advice_excerpt` for same-day consistency.

Current screenshot/input still wins over archive history for today's holdings.

### Same-Day Consistency Guard

Do not produce an unjustified same-day reversal. If an earlier same-day archive
recommended `买入`, `加仓`, or `条件加仓`, a later recommendation of `减仓`,
`卖出`, or `清仓` for the same code must cite material-change evidence:

- Price broke the earlier trigger, stop, open/previous close support, or another
  stated invalidation level.
- Index or relevant sector reversed materially from the earlier archive.
- Individual or sector fund flow turned materially negative.
- Major negative announcement, policy, lockup, reduction, or fundamental red
  flag appeared after the earlier archive.
- The current quality gate found a new critical gap that invalidates execution.

Without one of those evidence classes, change the action to `维持`, `观察`, or
`条件减仓`, and specify the exact evidence that would permit a later reversal.

### Historical Reduction Check

Before suggesting a fresh reduce/sell:

1. Compare current `qty`, `available_qty`, cost, and price with the last 5
   archive context snapshots.
2. Read the latest relevant advice excerpt for prior reduce/sell/add/hold
   intent.
3. If current `qty` is already lower than the recent archive quantity, treat it
   as a possible executed reduction and do not size from the old quantity.
4. If current `available_qty` is lower than prior available quantity, cap all
   execution by current `available_qty`; if it is 0, output no executable sell.
5. Only recommend another reduction when remaining exposure is still above
   limits or new risk evidence appears after the last archive.

### Reflecting on Past Decisions

When the same ticker appears in a future run:
1. **Retrieve**: What was the previous decision? At what price? Use conversation history, user-provided archive content, or the configured archive context endpoint. Do not call legacy persistence history endpoints during Phase 0.
2. **Compute raw return**: Current price vs previous advice price.
3. **Compute alpha**: Raw return minus CSI 300 (沪深300) return over the same period. If the benchmark price for the window is missing, mark `[数据缺失]` and lower alpha confidence (`alpha_window_fallback`).
4. **Assess**: Was the decision correct? What went right/wrong?
5. **Learn**: Extract 1-2 lessons to apply to the current decision.

### Injecting Memory

Feed into the Portfolio Manager's context:
- Available same-ticker decisions with performance from conversation history, user-provided archive content, or `/archives/context`.
- Available cross-ticker lessons from conversation history, user-provided archive content, or archive advice excerpts.
- If alpha was negative, apply `negative_alpha_sizing` (reduce confidence and tighten sizing).

Do not fetch memory via legacy backend endpoints. When past advice exists for a holding, reference it and the alpha in the final advice.

## Quality-Gated Output

When conditions prevent a full evidence-backed decision, do not output a lower-quality trading plan.

| Situation | Required Output |
|---|---|
| Mandatory evidence complete, grade A-B | Full advice with evidence pack, debate, holding actions, buy/rotation candidates, and risk controls |
| Non-critical gaps only, grade C | Explicitly mark missing fields, reduce action size, and block immediate new buys unless all buy-reason fields are supported |
| Mandatory quote/market/sector/capital/risk evidence missing | State "暂不能给出交易建议", list missing data, and provide next collection/confirmation steps |
| User requests brevity | Keep sections concise, but do not remove quality gate, key evidence, triggers, or risk controls |

The output may be shorter, but it must not be less rigorous.

## User-Facing Disclaimer

End with a brief line that the advice is decision support based on current data and is not a guaranteed investment instruction.

Example: "以上建议基于当前数据的决策支持，不构成投资建议。市场有风险，投资需谨慎。"
