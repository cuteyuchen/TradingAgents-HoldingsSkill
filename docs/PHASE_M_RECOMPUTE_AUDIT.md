# Phase M Recompute Audit

Baseline: `1c004badf1ff0056b006a6885b7cd7057c9737e0`

This audit records what the deterministic recompute engine may reuse, what must
be extracted, and what cannot honestly claim `FULL_PIT_EQUIVALENT` in Phase M.

## 1. Production functions that can be reused directly

- Market universe construction: `build_market_score_universe` in
  `backend/app/market/engine/universe.py`.
- Market metrics: `calculate_cross_section_metrics`, `calculate_ma_breadth`,
  `calculate_ma_trend_metrics`, `calculate_new_high_low`, `next_median_index`
  in `backend/app/market/engine/metrics.py`.
- Market components: `calculate_all_components` and every component calculator
  in `backend/app/market/engine/components.py`.
- Market scoring, smoothing, regime, hysteresis and coverage:
  `build_market_score_snapshot`, `smooth_score`, `apply_regime_hysteresis`,
  `coverage_gate` in `backend/app/market/engine/score.py`.
- Candidate scoring: `score_stock_candidate`, `score_etf_candidate`,
  `calculate_entry_score`, `calculate_structure_risk_reward`,
  `calculate_portfolio_fit`, `calculate_decision_edge`,
  `held_opportunity_baseline`, `rank_candidates`, and the production stage
  assignment in `backend/app/candidates/service.py::_score_row`.
- Portfolio constraints and gate: `hard_cap_for_security`,
  `build_portfolio_constraints`, `apply_portfolio_decision_gate` in
  `backend/app/portfolio/`.
- Transaction cost: `TransactionCostModel` and the Phase E
  `transaction_cost_estimate` helper.
- PIT universe resolver: `resolve_equity_universe` and its pure core
  `resolve_equity_universe_from_facts` in
  `backend/app/history/universe.py`.
- PIT fact resolvers: `resolve_fundamental`, `resolve_valuation`,
  `resolve_etf_metadata`, `resolve_historical_holdings`, `visible`,
  `fundamental_visible_at`.

## 2. Functions that strongly depend on the live provider

- `MarketEngine.calculate` in `backend/app/services/market_engine.py` falls back
  to `get_all_a_share_quote_snapshot` when caller data is absent.
- `portfolio_context_for_analysis` and `calculate_portfolio_risk` default to
  live quote loading through `_default_quote_loader`.
- `Candidate Engine` scan path in `backend/app/candidates/service.py` fetches
  live quotes and uses persisted `MarketScoreSnapshot` for the current market
  state.
- `LegacyMarketDataHistoryProvider` performs provider K-line fetches.

The recompute engine never calls these provider paths. It supplies every input
from persisted PIT facts.

## 3. Pure deterministic cores that need extraction

No new score engine is created. The following production code paths already
separate calculation from persistence and are reused verbatim:

- Market: metrics + components + score/hysteresis/smoothing.
- Candidate: opportunity/entry/RR/fit/decision-edge/stage/ranking.
- Portfolio: constraints + decision gate.

The only extraction is the PIT universe resolver: its DB loading is factored
into `resolve_equity_universe_from_facts` so a batch recompute can build one
in-memory date index instead of issuing per-date queries.

## 4. Parameters that come from the governance snapshot

- Market regime lower bounds and hysteresis.
- Candidate stage thresholds, action thresholds, RR thresholds, decision-edge
  thresholds, weights and limits.
- No-action thresholds per market regime.
- Portfolio hard caps and gate constants.

Phase M freezes the complete normalized production snapshot, its version id,
its version label, and its canonical `config_hash`. Pre-governance runs use the
explicit `LEGACY_PRE_GOVERNANCE` version with the code-default snapshot.

## 5. Inputs already available from Phase L

- Security lifecycle events.
- Daily trading status.
- Daily ST / `*ST` classification.
- Daily valuation.
- Fundamental publication/revision/restatement visibility.
- ETF metadata history.
- Price basis metadata.
- Daily bar cache with `available_at`.
- Confirmed portfolio snapshots and holdings.
- Trading calendar.
- Point-in-time universe resolver.

## 6. Inputs still missing

- Historical money-flow factors.
- Historical industry classification and industry-return factors.
- Historical ETF constituent breadth/lookthrough.
- Intraday PIT bars/quotes.
- Historical broker cash for every portfolio date (fail-closed when absent).

## 7. Market equivalence

Market recompute can reach `PARTIAL_PIT_RECOMPUTE` for EOD. The Diffusion
component is unavailable whenever historical industry facts are absent, so
`FULL_PIT_EQUIVALENT` is not claimed unless industry history is complete and
warmup is complete.

## 8. Stock candidate equivalence

Stock candidates can reach `PARTIAL_PIT_RECOMPUTE` for EOD. Flow and Industry
factors are unavailable and must remain missing (never zero, never current,
never future). A full-universe scan is still performed from the PIT universe.

## 9. ETF candidate equivalence

ETF candidates are `PARTIAL_PIT_RECOMPUTE` or `DIAGNOSTIC_ONLY` depending on
historical ETF metadata and valuation availability. Constituent breadth is
`UNSUPPORTED`, so `FULL_PIT_EQUIVALENT` is never claimed.

## 10. Scope that cannot claim FULL in Phase M

- `CANDIDATE_STOCK` without full Flow + Industry history.
- `CANDIDATE_ETF` without constituent history.
- `MARKET` without industry history and warmup.
- Intraday checkpoints with EOD-only facts.
- Any scope with missing `source_available_at` or mixed RAW/QFQ price basis.

These limitations are honest capability labels, not engine failures.
