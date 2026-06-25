# Multi-Agent Workflow

Use this file to structure reasoning. It adapts and extends the base multi-agent research graph from `TauricResearch/TradingAgents`, plus A-share extensions from `simonlin1212/TradingAgents-astock` and `KylinMountain/TradingAgents-AShare`: layered data sources, 7+ analyst roles, two-layer data quality gate, claim-driven bull/bear debate, trader proposal, three-way risk debate with revision loop, trading memory reflection, and portfolio-level final decision.

## Portfolio-Aware Pipeline (7 Phases)

The original repos analyze one ticker through a graph. For a screenshot portfolio, extend to multi-ticker. The pipeline is organized into **7 numbered phases** so each can be tracked and quality-gated as a unit. Reasoning mode and default parameters for each phase are defined in `configuration.md` (single source of truth).

| Phase | Name | Reasoning Mode | Steps |
|---|---|---|---|
| **Phase 0** | Intent Parsing + Archive Context | quick | Parse intent; extract portfolio; after code confirmation fetch archive context when configured |
| **Phase 1** | Analyst Team | quick | Fast market snapshot; centralized data collection; rank by risk; full pass for top-risk, lighter pass for small |
| **Phase 2** | Quality Gate | quick | Two-layer grade on all evidence before any debate |
| **Phase 3** | Claim-Driven Bull/Bear Debate | quick | Structured claims with tracking |
| **Phase 4** | Research → Trader → Risk Revision | mixed (research/risk mgr = deep, trader = quick) | Verdict, executable proposal, revision loop |
| **Phase 5** | Portfolio Synthesis | deep | Three-way risk debate + portfolio-level final decision |
| **Phase 6** | Reflection + Archive | quick | Reflect on available past decisions; after visible advice, upload archive files |

### Phase 0 — Intent Parsing + Archive Context (quick)

1. **Intent Parsing**: Parse the user's natural language to identify: target tickers, investment horizon (短线/中线/长线), focus areas (技术/基本面/政策/资金), risk profile, and specific questions. If the user says "分析茅台短线", extract ticker=600519, horizon=short, focus=technical+momentum. See Intent Parsing section below.
   - If the user says "解析持仓/解析截图/上传持仓", default the objective to "完成持仓解析并给出今日操作建议". Do not downgrade the task to extraction only unless the user explicitly says not to provide advice.
2. **Extract the whole portfolio** from screenshot/typed input/history.
3. **Archive Context**: After current codes are confirmed, call `GET /archives/context?codes=...&limit=5` when `ADVISOR_API_URL` and `ADVISOR_TOKEN` are configured. Use the result for same-day consistency, historical reduction detection, and trading memory. Do not call legacy persistence endpoints for Phase 0 history. The current skill integration with the companion system is archive-only; see `persistence.md`.

### Phase 1 — Analyst Team (quick)

4. **Fast Snapshot + Centralized Data Collection**: Build `evidence_snapshot.json` with `scripts/market_snapshot.py`, then fetch any remaining non-quote evidence once for all holdings in a single batch pass. See `data-sources.md`. Never fetch the same data point twice.
   - Treat `schema_version`, `source_chain`, `missing_fields`, `errors`, and `quality_gate` as the verified data contract inherited from the upstream TradingAgents v0.3.0 data-access hardening.
   - The snapshot already includes quote fallback chains, major indices, sector heat, northbound snapshot, market news, per-holding fund flow, concept tags, and quote-derived VPA when available.
   - Routine runs target about 10 minutes end to end (`target_advice_sec` = 600), but this is a progress target, not a hard cutoff.
   - Start with shared batch quote/index/sector requests, then split holdings
     into up to `max_ticker_workers` ticker-worker subagents or Python worker
     bundles. Each worker fetches K-line/VPA/news/fundamentals/fund-flow
     fallbacks for its assigned tickers and returns normalized evidence.
   - Analyst, debate, trader, risk, and portfolio roles must consume the shared
     snapshot/evidence table. They must not independently refetch quote, sector,
     news, or candidate data unless the snapshot marks a mandatory gap.
   - Run candidate-sector scanning in separate candidate workers where useful.
   - Eastmoney remains globally throttled across all workers; do not exceed the
     configured semaphore and request interval.
   - If elapsed time exceeds `progress_notice_sec`, state which mandatory data
     is still being collected. Do not stop mandatory data collection just to
     meet the time target.
5. **Rank holdings by risk priority**:
   - Largest market value.
   - Largest loss.
   - Weakest relative to index/sector (underperformance during market rebound is a red flag).
   - New red-flag news/fundamental/policy events.
6. **Run the full analyst pass** for top-risk names and any large ETF.
7. **Run a lighter pass** for small positions unless they have major news.

### Phase 2 — Quality Gate (quick)

8. **Quality Gate** (two-layer): Grade all evidence before any debate begins. See Quality Gate section below. Start from `evidence_snapshot.quality_gate`; then tighten the grade if later analyst reports expose additional missing mandatory fields.

### Phase 3 — Claim-Driven Bull/Bear Debate (quick)

9. **Claim-Driven Bull/Bear Debate**: Structured argumentation with tracked claims, evidence, confidence, and resolution status. See Debate section below.

### Phase 4 — Research → Trader → Risk Revision (mixed)

10. **Research Manager Verdict** (deep): Synthesize debate into directional plan.
11. **Trader Proposal** (quick): Convert plan into executable order with A-share constraints.
12. **Risk Manager Decision + Revision Loop** (deep): Risk Manager can send Trader back for revision (max `max_revision_retries`, default 1) with structured constraints.

### Phase 5 — Portfolio Synthesis (deep)

13. **Three-Way Risk Debate** + **Portfolio-Level Synthesis**: Aggressive / Neutral / Conservative with claim tracking, then aggregate all decisions at portfolio level, not single-stock level.

### Phase 6 — Memory Reflection + Archive (quick)

14. **Final Quote Refresh**: Run `scripts/market_snapshot.py --refresh-final evidence_snapshot.json` immediately before the final visible advice. Update quote-sensitive fields only. Rerun affected trader/risk logic only when refreshed prices invalidate a hard trigger, stop, or risk constraint.
15. **Trading Memory Reflection**: Compare with past decisions on same tickers, compute alpha vs CSI 300 benchmark. See `trading-rules.md` Trading Memory section.
16. **Archive Upload** (if enabled): First display the final advice. Then upload `advice.md`, `holdings.json`, and the original screenshot to the companion system via `persistence.md`. On failure, mark `[未持久化: 原因]` and do not change the already displayed advice.

**Quality note**: Under time/data pressure, do not skip mandatory evidence or the quality gate. If mandatory evidence is unavailable, block trading advice and state the missing data plus next collection step.

**Action output note**: Regardless of compression level, final output must include
both tables: (1) current holding operation advice, and (2) today's
buy/rotation advice. The buy/rotation table may include non-held symbols or a
current holding labeled `加仓现有持仓`/`条件加仓`, but it must never contradict the
holding table. Existing holdings marked `持有不加仓`, `减仓`, or `卖出` must not
reappear as buy candidates.

## Intent Parsing

Before starting analysis, parse the user's intent from natural language:

| Field | Extraction Method | Example |
|---|---|---|
| Target ticker(s) | Chinese name → code mapping, regex for 6-digit codes | "茅台" → 600519 |
| Investment horizon | Keywords: 短线/日内 = short(1-14d), 中线 = medium(14-90d), 长线 = long(90d+) | "做个短线" → short |
| Focus areas | Keywords: 技术/形态/K线, 基本面/财报, 政策/监管, 资金/主力/北向 | "看看资金面" → capital flow focus |
| Risk profile | Keywords: 激进/稳健/保守, or infer from portfolio concentration | High concentration → conservative |
| Specific objective | Keywords: 建仓/加仓/减仓/止损/调仓/轮动 | "该不该加仓" → add position decision |
| User context | Position info, cost basis, cash available from screenshot/conversation | Extract from holdings data |

If intent is ambiguous, ask for clarification rather than assume. Always state the parsed intent in the evidence pack.

### Dual-Horizon Parallel Analysis (短线/中线双轨)

Inspired by `TradingAgents-AShare`'s dual-period analysis. For **core holdings only** (`dual_horizon_holdings`, default: core holdings; small positions use a single track), run two parallel conclusion tracks instead of one:

| Track | Horizon | Default Window | Question Answered |
|---|---|---|---|
| Short track (短线轨) | short | `horizon_short_days` = 1–14d | Today's tactical action: trim/add/exit |
| Medium track (中线轨) | base | `horizon_medium_days` = 14–90d | Hold the base position or not |

Run the analyst pass and bull/bear debate **once per track** for core holdings. When the two tracks conflict:

- The track matching the user's **stated horizon wins** (`horizon_conflict_rule`).
- If horizon is unspecified, the **medium track (base) wins** for the hold/reduce decision; the short track only sizes tactical trades within `short_track_max_ratio` (default 15%).
- State both conclusions and the conflict explicitly in the evidence pack so the user can see the tension.

Non-core holdings use a single track matching the parsed horizon (or medium by default).

## Agent Roles

> **Phase mapping**: The detailed role sections below expand on the 7-phase pipeline above. Old sub-numbering is remapped to the unified phases: Analyst Team = Phase 1, Quality Gate = Phase 2, Bull/Bear Debate = Phase 3, Research Manager + Trader = Phase 4, Three-Way Risk + Risk Manager/PM = Phase 5.

### Phase 1: Analyst Team (7 analysts, run conceptually in parallel)

| Agent | Job | Key Data Sources | Key Questions | Mandatory Checklist |
|---|---|---|---|---|
| Market Analyst (技术分析师) | Price, trend, support/resistance, volume, indicators, VPA | K-line/OHLCV, stockstats indicators, VPA pre-computation | Is this stronger or weaker than its index/sector today? | Price/pct change, 30d return, 5d vs 20d volume, 3+ indicators (RSI/MACD/MA/Bollinger/ATR), support/resistance, VPA signals |
| Sentiment Analyst (情绪分析师) | Market/social/news tone, attention heat | News count, Xueqiu hot stocks, ZT pool, social media | Is attention heating or cooling? Is sentiment one-sided? | News count/time window, pos/neg/neutral split, top 3 topics, sentiment grade, warming/cooling trend |
| News Analyst (新闻分析师) | Company/sector/global events, catalysts | Eastmoney/CLS/Sina news, CCTV global news, announcements | Any new catalyst, risk event, or announcement? | Company news count, macro/sector news, 3-event timeline, bull/bear/neutral counts, explicit risk events |
| Fundamentals Analyst (基本面分析师) | Valuation, earnings quality, financial health | PE/PB/market cap (Tencent), financial statements (Sina), EPS forecast (THS) | Is the loss temporary price action or fundamental deterioration? | PE TTM, PB, market cap, revenue growth, net profit growth, ROE, debt ratio, operating cash flow vs net profit, forward EPS |
| Policy Analyst (政策分析师) | China policy and regulatory context | State/media/sector policy news, Eastmoney/CLS/global news | Is the sector supported, restricted, or rumor-driven? | Recent policy list with date/source, supportive/restrictive/neutral, impact strength, time window, policy rating |
| Hot Money Tracker (游资追踪师) | Northbound, main funds, LHB, hot-stock themes, concept blocks | Eastmoney fund flow, THS northbound, Dragon-Tiger Board, Baidu concept blocks, hot stocks | Is money entering or leaving? Is it hot-money chase or institutional confirmation? | 5d volume trend, northbound flow, individual main fund flow, concept blocks and sector move, hot-stock presence, capital-flow rating |
| Lockup Watcher (解禁监控师) | Lockup, reduction, pledge, ST/delist pressure | Eastmoney lockup calendar, insider transactions (mootdx F10), announcements | Is there supply shock or shareholder sell pressure? | 6-month insider/major-holder activity, top holder change, lockup/reduction news, reduction-pressure grade, next 90 days risk |

### Phase 2: Quality Gate (数据质量门控)

Runs after all analysts complete, before any debate begins. All thresholds below are defined in `configuration.md` (single source of truth).

Start with the snapshot-level gate:

| Snapshot Signal | Required Action |
|---|---|
| `quote:*` in `missing_fields` | Block executable action for that holding |
| `market.hot_sectors` or `concept_blocks:*` missing | Block executable new-buy candidates; watch-only triggers are allowed |
| `fund_flow:*` missing | Block aggressive add/average-down decisions |
| `market.northbound.history` absent | Do not infer northbound trend; use only real-time snapshot |
| `quality_gate.grade` D/F | Stop or heavily constrain action advice before debate |

**Layer 1 — Hard Checks (numeric thresholds):**

For each analyst report, apply these objective rules:

| Check | Threshold (default) | Grade Impact |
|---|---|---|
| Report length | `report_min_chars` = 200 | < 200 chars → cap grade at **D** |
| `数据缺失`/`unable to fetch` ratio | `data_missing_ratio_max` = 0.40 | > 40% → cap at **C** |
| `数据缺失`/`unable to fetch` ratio | `data_missing_ratio_critical` = 0.70 | > 70% → cap at **D** |
| Mandatory checklist fields missing | `mandatory_fields_missing_caution` = 3 | Missing 3+ for a heavy holding → cap at **B** and flag caution |
| Summary data table present | required | Absent → cap at **B** |

Grade each report individually: **A** (all checks pass) / **B** (minor gaps) / **C** (multiple missing) / **D** (mostly failed) / **F** (no usable data). Pick the **lowest** grade triggered by any failed check (most binding rule wins).

Overall data quality = weighted average, with heavy holdings weighted at `heavy_holding_weight` (default 2.0×).

**Layer 2 — LLM Review (语义级复审):**

Only runs if **fewer than `llm_review_trigger` (default 4)** reports fail Layer 1 hard checks (to save tokens). Checks:
- Cross-report consistency: do news and sentiment agree? Does technical align with capital flow?
- Evidence specificity: are claims backed by numbers, or vague language?
- Temporal relevance: is data current, or stale/cached?

Output: `data_quality_summary` with grade, key gaps, and confidence modifier.

**Quality Gate Action Bias:**

| Grade | Meaning | Action Bias |
|---|---|---|
| A | Mandatory checks mostly complete and current | Can give normal action |
| B | Minor missing data | Use normal action with lower confidence |
| C | Multiple missing fields or stale data | Reduce action size; avoid new buys |
| D | Mostly failed/too short evidence | Block buy/sell/reduce actions unless mandatory risk-control data is still sufficient |
| F | No usable data | Do not give trading advice; ask for the missing data and next collection step |

### Phase 3: Claim-Driven Bull/Bear Debate

Unlike simple alternating debate, use **structured claim tracking** inspired by TradingAgents-AShare:

Each bull/bear/risk response must produce structured claims:

```
Claim {
  claim_id: "INV-1" | "RISK-1" | ...  // INV- prefix for investment debate, RISK- prefix for risk debate
  speaker: "bull" | "bear" | "aggressive" | "conservative" | "neutral"
  stance: "bullish" | "bearish" | "risk_accept" | "risk_avoid" | "risk_balance"
  claim: "具体论点（一句话）"
  evidence: ["证据1", "证据2", "证据3"]  // max 3
  confidence: 0.0 - 1.0
  status: "open" | "addressed" | "resolved" | "unresolved"
  target_claim_ids: ["INV-1"]  // which claims this responds to
}
```

Note: Investment debate (Phase 3) uses `INV-` prefix with speakers `bull/bear` and stances `bullish/bearish`. Risk debate (Phase 5) uses `RISK-` prefix with speakers `aggressive/conservative/neutral` and stances `risk_accept/risk_avoid/risk_balance`.

**Debate Flow:**

1. Bull Researcher: Present initial bullish claims (all open).
2. Bear Researcher: Attack bull claims (mark as addressed/unresolved), present counter claims.
3. Bull Researcher: Defend/counterattack (resolve addressed claims, open new ones).
4. Bear Researcher: Same.
5. ... (controlled by max_debate_rounds, default 2 rounds = 4 total responses).

**Round Goals** (escalating):
- Round 1: "Establish core claims from both sides"
- Round 2: "Challenge weakest claims, reinforce strongest"
- Round 3+: "Prepare to close — identify unresolved claims"

**Unresolved Claims**: After debate ends, list all claims still marked "open" or "unresolved" — these are the key uncertainty points that the Research Manager must weigh.

**Debate State to Preserve in Output:**

| State | Fields |
|---|---|
| `investment_debate_state` | `bull_claims[]`, `bear_claims[]`, `unresolved_claim_ids[]`, `round_summaries[]`, `judge_decision` |
| `risk_debate_state` | `aggressive_claims[]`, `conservative_claims[]`, `neutral_claims[]`, `unresolved_claim_ids[]`, `round_summaries[]`, `judge_decision` |

### Phase 4: Research Manager (研究总监)

Use **deep reasoning** (not quick analysis) to synthesize the debate:

- Input: All 7 analyst reports + quality summary + full claim debate + unresolved claims.
- Process: Weigh unresolved claims by evidence strength and confidence.
- Output: Structured investment plan:
  - Rating: Buy / Overweight / Hold / Underweight / Sell
  - Rationale (which claims won and why)
  - Strategic actions (prioritized list)

### Phase 4: Trader (交易员)

Convert investment plan into executable order:

- Input: Investment plan + A-share constraints + policy/hot-money/lockup reports.
- Output: Structured trader proposal:
  - Action: Buy / Hold / Sell
  - Entry/exit/trigger price
  - Quantity or percentage
  - Stop/invalidating condition
  - Timing at current checkpoint
  - For buy candidates: take-profit and stop-loss

### Phase 5: Three-Way Risk Debate (with Claim Tracking)

Same claim-driven structure as bull/bear debate, but three-way:

| Agent | Role | Key Arguments |
|---|---|---|
| Aggressive (激进派) | Accept risk for upside | Limit-up/momentum, policy support, early rotation, missing early rotation is a risk |
| Conservative (保守派) | Protect capital first | T+1 lock, limit-down trap, policy reversal, lockup/reduction, weak fundamentals |
| Neutral (中立派) | Balance and sizing | Position sizing > direction, T+1 and price limits, conditional plans, rotation cycle |

**Rotation Order**: Aggressive → Conservative → Neutral → Aggressive → ...
**Rounds**: Controlled by max_risk_discuss_rounds (default 1 round = 3 total responses).

### Phase 5: Risk Manager / Portfolio Manager (风控经理/组合经理)

Use **deep reasoning** to make final decision:

**Revision Loop** (unique feature from TradingAgents-AShare):
- Verdict can be: `pass` / `revise` / `reject`
- If `revise`: Send back to Trader with structured feedback:
  - `hard_constraints`: Non-negotiable (e.g., "仓位不超过30%")
  - `soft_constraints`: Advisory (e.g., "建议分批建仓")
  - `execution_preconditions`: Must-hold conditions
  - `de_risk_triggers`: Immediate action triggers
  - `revision_reason`: Why plan needs revision
- Max 1 retry. If still unsatisfactory, default to reject.

**Final Output:**
- Rating: Buy / Overweight / Hold / Underweight / Sell
- Portfolio-level action plan
- Cash target
- What to reduce first, keep, watch, or rotate into
- Which buy candidate can be executed, watched, or rejected today

### Optional: Hot Sector Scanner & Buy Candidate Analyst

These run as part of the portfolio synthesis, not as separate graph nodes. See `buy-candidate-selection.md`.

## Trading Memory System

Inspired by TradingAgents-astock's memory log, maintain awareness of past decisions without relying on removed/legacy backend interfaces. The current companion-system contract for the skill is archive-only: Phase 0 may read `/archives/context`, and Phase 6 stores advice Markdown, normalized holdings JSON, and the original screenshot after advice is visible.

1. **Record**: After each execution, the Phase 6 archive stores the visible advice, parsed holdings JSON, and screenshot. If persistence is not configured, reference prior decisions from conversation history.
2. **Reflect**: If the same ticker appears in available conversation history, user-provided archive content, or `/archives/context`, compare:
   - What was the previous decision and price?
   - What is the raw return since then?
   - What is the alpha return vs CSI 300 (`alpha_benchmark`) when benchmark data is available? Missing benchmark → `[数据缺失]` + lower confidence.
   - Was the decision correct? What can be learned?
3. **Consistency Check**: If `same_day_advice` shows an earlier add/buy and the current plan wants reduce/sell, require material-change evidence before the reversal; otherwise output hold/watch/conditional reduce.
4. **Reduction Check**: Compare current `qty` and `available_qty` with `timeline_by_code`; if the position already decreased, do not repeat the old reduce size.
5. **Inject**: Feed only actually available recalled decisions + lessons into the Portfolio Manager's context. If alpha was negative, apply `negative_alpha_sizing` (reduce confidence, tighten sizing).

When past advice exists for a holding, reference it and the alpha in the final advice regardless of which store it came from.

## Dual Reasoning Mode

Inspired by `TradingAgents-AShare`'s dual model strategy (quick_think_llm vs deep_think_llm). Each phase has a fixed reasoning mode; defaults are in `configuration.md`.

| Mode | Phases | Characteristics |
|---|---|---|
| **Quick mode (快速分析)** | Phase 0, Phase 1, Phase 3, Phase 4 (trader), Phase 6 | Faster, focused on specific data points, concise output |
| **Deep mode (深度推理)** | Phase 4 (research manager, risk manager), Phase 5 | Slower, synthesizes all reports + debate + unresolved claims, weighs trade-offs carefully |

In practice: analyst-level and debate-level reasoning (Phases 0/1/3, trader) is fast and data-focused. Judge-level reasoning (research manager, risk manager, portfolio manager — Phases 4/5) must be thorough, weighing all evidence and unresolved claims before deciding.

## Long-Running Execution

If the run is taking longer than expected, send a progress note after
`progress_notice_sec` and continue collecting mandatory evidence. To keep the
report readable after evidence is complete, summarize low-risk small positions
more compactly, but do not remove quality gate, material debate claims, action
triggers, risk controls, or buy-candidate reasons.

Use compact groupings only after the required data is present:

- Market + hot money = "tape check" (include VPA signals).
- News + policy + lockup = "event risk check".
- Hot sector + candidate score = "what can be bought today, or why not".
- Fundamentals = "can this be carried overnight?"
- Bull/bear/risk panel = "why hold vs why reduce vs survivable size" (still use claim IDs for key arguments).

## Detailed Debate Requirement

Every execution should include detailed debate for material decisions:

- If recommending a sell/reduce, show the strongest bull counterargument first, then why bear/risk evidence wins.
- If recommending hold/add, show the strongest bear risk first, then why bull evidence wins.
- If data quality is weak, the Research Manager verdict should say evidence is insufficient for aggressive action and reference the quality grade.
- If portfolio exposure is high, the Risk Panel must discuss survivable sizing under T+1 and price-limit risk.
- Track at least the top 3 claims per side with IDs in the output.

## Portfolio Synthesis Rules

- Never let single-stock reasoning ignore total account exposure.
- Prioritize reducing high-weight losers that are weaker than the market.
- Do not sell the strongest holding first just because it is profitable.
- Separate "today's execution" from "watchlist/rotation candidates".
- Always include a buy/rotation candidate module. If buys are blocked by exposure, timing, or data quality, output watch-only candidates with exact triggers.
- If the market is strong but the user's heavy holdings are weak, use strength to reduce weak holdings rather than add.
- New buy candidates must fit the portfolio cash plan; do not add exposure before planned weak-position trims when account exposure is above 85%.
- Buy/rotation candidates may duplicate current holdings only as
  `加仓现有持仓`/`条件加仓` and only when the current-holding action table gives
  the same add verdict. If a held symbol is the best expression of a hot sector
  but the holding verdict is `持有不加仓`, `减仓`, or `卖出`, select a different
  non-held candidate or state that no buy is allowed today.

## Final Decision Vocabulary

Use both rating and action:

| Rating | Meaning | Chinese Action |
|---|---|---|
| Buy | New/add exposure with strong evidence | 买入/加仓 |
| Overweight | Gradually increase exposure | 逐步加仓 |
| Hold | Maintain current exposure | 持有 |
| Underweight | Reduce partial exposure | 减仓 |
| Sell | Exit or avoid | 卖出/清仓 |

For user-facing intraday advice, translate these into concrete Chinese actions: 买入/加仓/持有/减仓/卖出/等待.
