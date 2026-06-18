# Configuration Parameters

This file is the **single source of truth** for all tunable parameters in this skill. Other files (`multi-agent-workflow.md`, `trading-rules.md`, `data-sources.md`, `python-execution.md`, `debate-reporting.md`) reference the values here. When a parameter appears in two places, this file wins.

Inspired by `TradingAgents-astock`'s centralized config dict and `TradingAgents-AShare`'s configurable debate parameters.

## Pipeline Phase Parameters

The workflow runs in 7 phases (see `multi-agent-workflow.md`). Each phase has a reasoning mode.

| Parameter | Default | Applies To | Notes |
|---|---|---|---|
| `pipeline_phases` | 7 (Phase 0–6) | Whole pipeline | Phase 0 意图解析 / 1 分析师 / 2 质量门 / 3 多空辩论 / 4 研究·交易·风控修正 / 5 组合综合 / 6 记忆反思+持久化 |
| `quick_mode_phases` | 0, 1, 3, 4(trader) | Reasoning mode | Fast, data-focused: analyst reports, debate responses, trader proposal |
| `deep_mode_phases` | 4(research mgr), 4(risk mgr), 5 | Reasoning mode | Thorough synthesis: research/risk/portfolio managers weigh all evidence + unresolved claims |

## Debate Parameters

Control how deep the claim-driven debate goes. Inspired by `TradingAgents-AShare`.

| Parameter | Default | Meaning |
|---|---|---|
| `max_debate_rounds` | 2 | Investment (bull/bear) debate depth. 2 rounds = 4 responses (Bull→Bear→Bull→Bear) |
| `max_risk_discuss_rounds` | 1 | Three-way risk debate depth. 1 round = 3 responses (Aggressive→Conservative→Neutral) |
| `max_revision_retries` | 1 | How many times Risk Manager can send Trader back. After this, default to reject |
| `claims_per_side_min` | 2 | Minimum tracked claims per side in output |
| `claims_per_side_target` | 3 | Target claims per side for full detail |
| `evidence_per_claim_max` | 3 | Max evidence items per claim |

## Quality Gate Parameters (Layer 1 Hard Checks)

Numeric thresholds for evidence grading. Inspired by `TradingAgents-astock`'s measured hard-check values. See `multi-agent-workflow.md` Phase 2.

| Check | Threshold | Grade Impact |
|---|---|---|
| `report_min_chars` | 200 | Report < 200 chars → grade D |
| `data_missing_ratio_max` | 0.40 | "数据缺失" markers > 40% of report → grade C |
| `data_missing_ratio_critical` | 0.70 | > 70% → grade D |
| `mandatory_fields_missing_caution` | 3 | Missing 3+ mandatory fields for a heavy holding → caution, cap at B |
| `llm_review_trigger` | < 4 reports fail Layer 1 | Layer 2 LLM review only runs when fewer than 4 reports fail hard checks (token saving) |
| `heavy_holding_weight` | 2.0x | Heavy holdings weighted higher in overall grade |

| Overall Grade | Meaning | Action Bias |
|---|---|---|
| A | All checks pass, current data | Normal action |
| B | Minor gaps | Normal action, lower confidence |
| C | Multiple missing / stale | Reduce action size; no new buys |
| D | Mostly failed / too short | Risk-control only |
| F | No usable data | Ask for data or generic risk framework only |

## Trading Rules Parameters

Position and action sizing. See `trading-rules.md`.

| Parameter | Default | Notes |
|---|---|---|
| `account_exposure_high` | 0.85 | Above 85% exposure → raise cash before new buys |
| `first_trim_pct` | 0.15–0.30 | First trim of a weak holding |
| `second_trim_pct` | 0.20–0.30 | Second trim if support breaks / sector turns |
| `cash_min_ratio` | 0.15 | Soft floor on cash after actions |
| `single_position_max_ratio` | 0.30 | Hard cap: one holding ≤ 30% of total assets |
| `candidate_score_buyable` | 7 | Score ≥ 7 = buyable |
| `candidate_score_watch` | 5 | 5–6 = watch only; < 5 = do not recommend |

## Dual-Horizon Parameters

Short vs medium-term parallel analysis for core holdings. See `multi-agent-workflow.md` Intent Parsing and `trading-rules.md`.

| Parameter | Default | Notes |
|---|---|---|
| `horizon_short_days` | 1–14 | 短线/日内 |
| `horizon_medium_days` | 14–90 | 中线 |
| `horizon_long_days` | 90+ | 长线 |
| `dual_horizon_holdings` | core holdings only | Run both short + medium tracks; small positions use single track |
| `short_track_max_ratio` | 0.15 | Short-term sleeve ≤ 15% of portfolio |
| `horizon_conflict_rule` | horizon wins | When short and medium conclusions conflict, the one matching the user's stated horizon wins; if horizon unspecified, medium-track (base) wins, short-track (trade) only sizes within `short_track_max_ratio` |

## Trading Memory Parameters

How much history to recall and how alpha is measured. See `trading-rules.md` and `persistence.md`. Inspired by `TradingAgents-astock`'s `memory_log_max_entries`.

| Parameter | Default | Notes |
|---|---|---|
| `memory_same_ticker_entries` | 5 | Recall last 5 same-ticker decisions (sliding window) |
| `memory_cross_ticker_lessons` | 3 | Recall 3 cross-ticker lessons |
| `alpha_benchmark` | CSI 300 (沪深300, 000300) | Benchmark for alpha calculation |
| `alpha_window_fallback` | mark `[数据缺失]` | If benchmark price missing for the window, mark missing and lower alpha confidence |
| `negative_alpha_sizing` | more conservative | Negative alpha → reduce confidence and tighten sizing on next decision |

## Eastmoney Throttling Parameters

Rate-limit discipline to avoid Eastmoney IP bans. Inspired by `TradingAgents-astock`'s `_em_get()` measured thresholds. See `data-sources.md` and `python-execution.md`.

| Parameter | Default | Ban Trigger | Notes |
|---|---|---|---|
| `em_min_interval_sec` | 1.0 | — | Min seconds between repeated Eastmoney requests; **tunable** — set to 2.0 before large batch runs to proactively slow down |
| `em_jitter_sec` | 0.1–0.5 | — | Random delay added to avoid pattern detection |
| `em_max_concurrent` | 5 | ≥ 10 concurrent | Max concurrent Eastmoney requests |
| `em_scheduled_concurrent` | 3 | — | Stricter limit for scheduled/automated tasks |
| `em_max_per_minute` | 200 | > 200 req/min | Soft ceiling; throttle when approaching |
| `em_max_per_second` | 5 | > 5 req/s | Soft ceiling |
| `em_zombie_timeout_sec` | 120 | — | A request holding a slot > 120s is treated as timed out and released |

## Data Source Routing Parameters

TTL cache and fallback discipline. See `python-execution.md` and `data-sources.md`. Inspired by `TradingAgents-AShare`'s `_AkshareLock`.

| Parameter | Default | Notes |
|---|---|---|
| `dedup_ttl_sec` | 30 | Same data point not re-fetched within 30s (per-ticker lock + TTL cache) |
| `dedup_scope` | per (ticker, data_type) | Lock granularity |
| `fallback_action` | record `[数据缺失: source/field]`, continue | Never retry endlessly; record gap and reduce confidence |

## Persistence Parameters

When the companion persistence system is configured. See `persistence.md`.

| Parameter | Default | Notes |
|---|---|---|
| `persistence_enabled` | false | Set true when `ADVISOR_API_URL` + `ADVISOR_TOKEN` are configured |
| `upload_on_completion` | true | Upload full run at end of pipeline (Phase 6) |
| `fetch_history_on_start` | true | Pull same-ticker timeline at Phase 0 for trading memory |
| `upload_failure_policy` | warn, do not block | On upload failure, finish advice to user and mark `[未持久化: 原因]`; never silently swallow |
| `consecutive_failure_threshold` | 3 | After 3 consecutive data-fetch failures for a checkpoint, output degradation warning and let the system flag that checkpoint grey (health linkage) |

## Tuning Guidance

- These defaults are tuned for **single-user, single-portfolio, intraday A-share** use.
- To speed up routine runs: lower `max_debate_rounds` to 1, raise `claims_per_side_min` tolerance.
- To deepen analysis on a heavy position: raise `max_debate_rounds` to 3 for that name only.
- Before a large multi-holding batch: set `em_min_interval_sec` to 2.0 to avoid bans.
- When the persistence system is down: the skill still runs; only trading-memory recall and upload degrade.
