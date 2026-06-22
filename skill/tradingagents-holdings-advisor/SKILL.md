---
name: tradingagents-holdings-advisor
description: Use when the user asks for intraday stock or ETF operation advice, 持仓截图, 今日操作建议, 调仓建议, 实时行情, 新闻基本面, 9:25/10:00/12:00/14:30 review, or asks what to buy, sell, hold, reduce, add, or rotate.
---

# TradingAgents Holdings Advisor

## Overview

Give intraday A-share/HK-related stock and ETF operation advice from the user's real holdings, current quotes, market context, news, fundamentals, capital flow, signal data, VPA analysis, and A-share trading constraints.

This skill is adapted from the design patterns in three open-source TradingAgents-style investment research systems:

- **`TauricResearch/TradingAgents`**: the base multi-agent research graph, analyst specialization, researcher/trader/risk collaboration loop, and final portfolio decision framing.
- **`simonlin1212/TradingAgents-astock`**: 7 A-share analysts (including policy/hot-money/lockup watchers), two-layer data quality gate, signal data tools layer (northbound/dragon-tiger/lockup/concept blocks/fund flow/industry comparison/hot stocks), 7-source direct-connect data architecture, quality-gated structured output, trading memory with CSI 300 alpha benchmark.
- **`KylinMountain/TradingAgents-AShare`**: Claim-driven bull/bear debate (structured claims with IDs, evidence, confidence, status tracking), centralized DataCollector (fetch once, share across agents), risk revision loop (Risk Manager can send Trader back for revision), intent parsing from natural language, VPA (Volume Price Analysis) pre-computation, dual model strategy (quick vs deep thinking).

Core rule: holdings source must stay clean. If the user provides a screenshot, use that screenshot as the holdings source. If no screenshot is provided, use current conversation history or memory. Never query local development systems, broker caches, databases, or test data unless the user explicitly asks.

Screenshot parsing is never the final deliverable by itself. When the user asks to parse, analyze, read, or upload a holdings screenshot, treat it as a full portfolio-advice run unless the user explicitly says "只解析/不要建议/只输出JSON". The workflow must still complete evidence collection, quality gate, multi-agent bull/bear debate, trader proposal, three-way risk debate, portfolio synthesis, today's holding actions, and non-held buy/rotation candidates.

## Bundled References

Read these supporting files when needed:

- `references/data-sources.md`: 5-tier data source matrix (core market / fundamentals / signal data / news / global), centralized collection principle, Eastmoney rate limiting, symbol resolution, VPA pre-computation, mandatory data checklist, quality gate quick reference.
- `references/multi-agent-workflow.md`: 7-phase portfolio-aware pipeline (Phase 0 intent + history fetch → 1 analysts → 2 quality gate → 3 bull/bear debate → 4 research/trader/risk revision → 5 portfolio synthesis → 6 memory + persistence), 7 analyst roles, two-layer numeric quality gate, claim-driven bull/bear debate, dual-horizon analysis, dual reasoning mode (quick/deep per phase).
- `references/trading-rules.md`: A-share constraints, time checkpoint rules, position rules, dual-horizon position rules, trigger design, stop/reduce/add rules, buy candidate rules, rotation rules, overnight carry rules, risk revision loop, trading memory & alpha benchmark, quality-gated output.
- `references/debate-reporting.md`: Claim-driven debate system (claim structure, status lifecycle, round goals), full transcript structure (evidence pack, quality gate summary, bull/bear debate with claims, research manager verdict, trader proposal with revision, three-way risk debate with claims, portfolio manager final, buy candidates), A-share bull/bear/risk frameworks, formatting templates.
- `references/buy-candidate-selection.md`: Three-layer hot sector scanner (broad market / hot sectors / candidate tape), candidate pool construction strategy, scoring system (0-10 with 6 factors), candidate output requirements, buy risk gate, portfolio-aware buy rule, industry comparison integration.
- `references/python-execution.md`: When and how to use Python scripts, dependency policy, Eastmoney concurrency control, multi-source routing strategy, centralized data prefetch pattern, dedup lock + TTL cache, VPA pre-computation code, script rules, standardized output contract.
- `references/configuration.md`: **Single source of truth** for all tunable parameters — pipeline phases, debate rounds, quality-gate thresholds, trading rules, dual-horizon, trading memory, Eastmoney throttling, dedup TTL, persistence. Other files reference values here; when a value appears in two places, this file wins.
- `references/persistence.md`: Upload/fetch contract for the companion persistence system (`ADVISOR_API_URL` + `ADVISOR_TOKEN`). Defines Phase 0 history fetch and Phase 6 archive upload. Skill still runs standalone when persistence is not configured.

## Required Cadence

When invoked by the user or external automation, run this workflow at:

| Time | Focus | Key Checks |
|---|---|---|
| 09:25 | Opening auction, overnight news | Global markets, gap-risk plan, northbound pre-open flow, auction volume |
| 10:00 | First confirmed intraday trend | Weak/strong holdings vs open/prev close, first trims, VPA breakout signals |
| 12:00 | Midday review | Sector rotation, capital flow shift, whether morning breakout held, afternoon plan |
| 14:30 | Late-session risk control | Next-day carry decision, avoid impulsive new buys, finalize position adjustments |

### Quality-First Failure Policy

Advice quality is the first priority. Time pressure, repeated data failures, or slow optional sources must not produce a low-quality trading recommendation:

- After `consecutive_failure_threshold` (default 3) consecutive failures for the same checkpoint, output an explicit blocking warning in the evidence pack (e.g. `[关键数据未完成: 10:00 checkpoint 数据拉取连续3次失败，暂不给交易建议]`).
- When the persistence system is configured, record each failure count so the dashboard can surface the issue.
- If mandatory quote, holdings, risk, or quality-gate evidence is missing, stop before action advice and output: missing data, why it blocks the recommendation, and how to fetch/confirm it next. Do not replace this with a low-quality trading plan.

A skill does not schedule itself. If automatic daily execution is requested, say an external scheduler/reminder must trigger the agent at those times.

## Quality-First Workflow

Target routine execution time is about 10 minutes, but this is a progress
checkpoint, not a hard quality cutoff. Use batch public data fetches first,
then split per-holding work across ticker-worker subagents or Python worker
bundles when there are multiple holdings. If the run exceeds 10 minutes, state
which key data is still being collected and continue until mandatory evidence is
complete. Do not skip required data to meet the time target.

0. **Fetch history (Phase 0, if persistence configured)**: If `ADVISOR_API_URL` + `ADVISOR_TOKEN` are set, pull the last 5 same-ticker decisions + cross-ticker lessons for in-scope holdings via `references/persistence.md`. This seeds trading memory from a real store instead of conversation history. Skip silently if not configured.
1. **Parse intent**: Identify target tickers, investment horizon, focus areas, risk profile, specific objective from natural language. See `references/multi-agent-workflow.md` intent parsing section.
2. **Identify holdings source**: screenshot > typed current holdings > history/memory.
3. **Extract holdings**: name, code if visible, total quantity (`qty`), available quantity (`available_qty`), cost, current price, market value, P/L, total exposure. `available_qty` only means currently sellable/usable quantity; `qty - available_qty` is unavailable because of pending orders, freeze, or T+1 limits, and must never be treated as already reduced/sold. Any reduce/sell recommendation must size from `available_qty`, not total `qty`. `pnl` must be the decimal return ratio. For 同花顺/券商持仓截图, the 盈亏 column commonly has two lines: the first line is 盈亏金额 (for example `-19,490.87`) and the second line is 盈亏率 (for example `-27.730%`). Store the first line in `pnl_amount`, convert the second line to decimal `pnl` (`-0.2773`), and do not create a correction record for this normal two-line mapping. If no percent line is visible, compute `pnl = (current price - cost) / cost` from screenshot price/cost and keep the amount line in `pnl_amount`.
4. **Resolve symbols carefully**: If a broker display name is ambiguous, first use public symbol/quote sources to match by name plus screenshot price. If there is no unique match within the 2% price tolerance, ask the user to confirm/input the code and do not upload the archive until confirmed. See `references/data-sources.md` symbol resolution.
5. **Mandatory quote collection**: After codes are confirmed, fetch public market quotes in batch. During trading hours use live quotes; after market close use the latest completed trading session data. If every quote route is unavailable, record health failure and stop before trading advice with `[暂不能给出交易建议: 缺少quote]`. See `references/python-execution.md`.
6. **Two-layer quality gate**: Grade all evidence before debate using the numeric thresholds in `references/configuration.md` (Layer 1: hard checks, Layer 2: LLM review). See `references/multi-agent-workflow.md` quality gate section.
7. **Run the multi-agent reasoning**: Run the full analyst pass for top-risk names, lighter pass for small positions. Apply dual-horizon analysis for core holdings. Apply claim-driven bull/bear debate. See `references/multi-agent-workflow.md`.
8. **Apply trading rules**: T+1, daily limits, lot size, time-of-day, total exposure, loser/winner priority, dual-horizon sleeve sizing. Check risk revision loop. See `references/trading-rules.md`.
9. **Select 1-2 buy/rotation candidates**: Use the three-layer hot sector scanner and scoring system. Every candidate must include a recommendation reason covering news/catalyst, capital flow, and current sector position/rotation stage. If buys are blocked, still output conditional watch-only triggers and the blocking reason. See `references/buy-candidate-selection.md`.
   - Today's buy/rotation candidates must not duplicate current holdings. If a
     held symbol is a hold/add decision, keep it in the holding action table and
     choose a different non-held candidate or state that no new buy is allowed.
10. **Print the detailed debate transcript**: Use claim-driven format with IDs, evidence, confidence, status. Investment claims must use `INV-` IDs; three-way risk claims must use `RISK-1/RISK-2/RISK-3` with aggressive/neutral/conservative speakers. Show unresolved claims explicitly. See `references/debate-reporting.md`.
11. **Risk Manager review**: Check if Trader proposal needs revision. Apply hard/soft constraints. See `references/trading-rules.md` risk revision loop.
12. **Output action-first advice**: Market read, portfolio conclusion, holding table, buy candidate plan, rebalance plan, checkpoint-specific execution rules.
13. **Trading memory reflection**: If past decisions exist (from Step 0 fetch or conversation history), reference them and compute alpha vs CSI 300. See `references/trading-rules.md` trading memory section.
14. **Display advice, then archive (Phase 6, if persistence configured)**: First show the final advice to the user. After the advice is visible, upload `advice.md`, `holdings.json`, and the original screenshot file via `references/persistence.md`. On failure, append `[未持久化: 原因]` without changing the already displayed advice. Skip silently if not configured.

## Output Format

Keep the answer concise, evidence-backed, and executable. Do not produce a trading action table until mandatory evidence and the quality gate are complete:

Use Chinese display names first. In tables and prose, write instruments as `股票名称（代码）` whenever the name is known; avoid code-only or English-only labels except for raw API fields that must keep their source values.

1. **Market read**: Index trend, sector mood, domestic/overseas risk appetite, northbound flow direction.
2. **Data quality grade**: A/B/C/D/F with key gaps noted.
3. **Portfolio conclusion**: Add, hold, reduce, sell, or rotate — at portfolio level.
4. **Evidence pack and claim-driven debate transcript**:
   - Bull vs bear debate with claim IDs and status.
   - Unresolved claims listed explicitly.
   - Research Manager verdict with reasoning.
   - Trader proposal with trigger prices.
   - Risk revision loop result (if triggered).
   - Aggressive / Neutral / Conservative risk debate with claim IDs.
   - Portfolio Manager final decision.
5. **Holding table**:

| 标的 | 代码 | 实时状态 | 数据质量 | 关键证据 | 多空裁决 | 今日操作 |
|---|---|---:|---:|---|---|---|

6. **Buy/rotation candidate plan**:
   - Hot sectors/themes considered (with sector rank and fund flow data).
   - 1-2 candidate stocks or ETFs with score breakdown, or "no immediate buy" with trigger plan.
   - Recommendation reason for each candidate, explicitly covering news/catalyst, capital flow, and current sector position/rotation stage.
   - Entry trigger, initial size, take-profit (2 targets), stop-loss, invalidating condition.
7. **Rebalance plan**:
   - Target cash after actions.
   - What to reduce first (with trigger and quantity).
   - What to keep (and why).
   - What can be watched or rotated into.
8. **Current checkpoint plan**: e.g. "10:00 only execute if still below open price; if northbound turns negative, pause all buys".
9. **Trading memory note** (if applicable): "上次对该标的建议[动作]@[价格]，至今收益率[X]%，相对沪深300 Alpha [Y]%".

## Non-Negotiables

- Do not use local project databases or app state as holdings.
- Do not let Python scripts or installed dependencies query local broker apps, local databases, caches, or development systems for holdings.
- Do not drop the original holdings screenshot during persistence. If the source is a screenshot, include a `screenshot` payload with `filename`, `mime_type`, `data_url` or durable reference, `captured_at`, and `source`.
- Do not force-match ambiguous ETFs when live price conflicts with screenshot price.
- Do not trust a single broker/OCR P/L value that conflicts with price and cost. If there is no separate percent line, correct the decimal ratio, write the corrected ratio to `holdings[].pnl`, put amount-like values in `holdings[].pnl_amount`, and record `{code, name, original_pnl, corrected_pnl, pnl_amount, price, cost, reason}` in `evidence_pack.pnl_corrections`.
- Do not treat a normal two-line broker 盈亏 cell as an error. If the cell shows amount on line 1 and percent on line 2, persist them directly as `pnl_amount` and `pnl`; do not write `evidence_pack.pnl_corrections`.
- Do not stop after extracting holdings. A screenshot run must produce concrete buy/sell/hold/reduce actions, triggers, and a buy/rotation candidate plan after the multi-agent debate workflow.
- Do not persist a run with `UNKNOWN-*` holdings when public matching cannot confirm the code; ask the user for confirmation first and mark `[未持久化: 待确认代码]`.
- Do not infer a sell/reduce from unavailable quantity. If `available_qty` is less than `qty`, mark the difference as unavailable/pending/frozen/T+1 and size any reduce recommendation only from `available_qty`.
- Do not recommend a sell/reduce quantity greater than `available_qty`; if `available_qty` is 0, the action can only be hold/watch/cancel pending order/confirm availability, not an executable sell.
- Do not skip public quote collection for confirmed codes. During market close, use the latest completed trading session data rather than stale intraday assumptions; if all quote sources fail, record `[暂不能给出交易建议: 缺少quote]` and report `/health/outcome`.
- Do not average down heavy losers without market, sector, and capital-flow confirmation.
- Do not give generic "observe" answers; include triggers, quantities/percentages, and priority.
- Do not omit today's buy/rotation candidates; if no buy is allowed, output a conditional watch-only plan with exact triggers.
- Do not omit the bull/bear and risk debate transcript during execution unless the user asks for a concise answer.
- Do not upload before the final advice is displayed to the user. Persistence is a post-advice archive step.
- Do not phrase any recommendation as guaranteed profit.
- Do not skip the quality gate; always state the data quality grade in the evidence pack.
- Do not present resolved claims as still uncertain; update claim status explicitly.
- Do not ignore unresolved claims in the final verdict; the Research Manager must address each one.
- Do not recommend a buy that violates the risk revision loop's hard constraints.
- Do not give today's buy recommendation without a reason that covers news/catalyst, capital flow, and current sector position/rotation stage.
- Do not list the same code as a current holding action and as today's
  buy/rotation candidate. Existing-holding add/hold decisions belong in the
  holding table; new-buy candidates must be non-held symbols.
- Do not make more than 5 concurrent Eastmoney requests; follow the rate limiting discipline in `references/data-sources.md` and the throttling parameters in `references/configuration.md`.
- Do not silently swallow a persistence upload failure; if `ADVISOR_API_URL` is configured and the upload fails, mark `[未持久化: 原因]` at the end of the advice.
- Do not convert key data failures into a lower-quality trading plan. If mandatory evidence is incomplete, block action advice and state the missing data plus next collection step.

## Minimum Verification

Before final advice, verify:

- Holdings source is explicit (screenshot/typed/history).
- Intent is parsed and stated (ticker/horizon/focus/objective).
- Every live quote maps to the correct code or is marked uncertain.
- Every uncertain code has either a public-source match within tolerance or a user confirmation before upload.
- Every confirmed holding has a quote source, quote time, and market session in `indicators.quote`; otherwise the run must stop before trading advice and explain the blocker.
- At least one broad index, relevant sector, and capital-flow check was considered.
- VPA signals were computed for material holdings.
- Hot sectors/themes were scanned using the three-layer architecture; if source failure blocks sector position判断, new-buy advice is blocked rather than filled with a weak substitute.
- News/fundamental/policy red flags were checked for heavy positions.
- Data quality grade is stated and reflects the two-layer gate results.
- Debate includes claim IDs with at least the top 2-3 claims per side.
- Three-way risk debate includes structured `RISK-` claims for aggressive, neutral, and conservative views.
- Unresolved claims are explicitly listed and addressed in the verdict.
- Advice contains explicit action, trigger level, risk control, and a buy/rotation candidate plan.
- Every buy/rotation candidate includes a reason covering news/catalyst, capital flow, and current sector position/rotation stage.
- Current holding actions and today's new-buy/rotation plan are both present,
  and candidate codes do not overlap with holding codes.
- If past decisions exist for any holding, trading memory was checked and alpha referenced.
