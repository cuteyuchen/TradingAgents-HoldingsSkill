# Alpha Memory Contract

Phase G preserves historical decision context as an auditable record. A successful `AnalysisRun` creates one immutable Decision Memory snapshot. Later Outcomes and Daily Reviews are derived facts and must never rewrite that snapshot.

Recommendation and user action remain separate. Only `CONFIRMED` Trade Ledger entries are execution facts; `PENDING_REVIEW` and `VOIDED` entries do not count. Execution alignment is deterministic: `FOLLOWED`, `PARTIAL`, `IGNORED`, `OPPOSITE`, `UNRESOLVED`, or `NOT_APPLICABLE`. Missing Ledger evidence is not enough to call a recommendation ignored.

Outcomes use the persisted TradingCalendar, completed DailyBarCache data, and All-A Median Index when available. Horizons are 1, 5, 10, 20, 60, and 120 trading days. Raw return, benchmark return, excess return, MFE, MAE, directional return, and execution return are server-calculated ratios; the model does not calculate or supply them.

Historical analogues use deterministic structured similarity over the saved decision-time features. Missing features reduce similarity coverage instead of being filled with zero. Outcomes do not affect similarity. Memory is advisory evidence only: current Market, Portfolio, Candidate, Data Quality, and Decision Gates always take precedence. Memory cannot invent a candidate, promote a candidate stage, override a hard cap, or change any strategy weight.

Daily Review is deterministic first. It reports what happened today, confirmed execution alignment, and Outcomes that matured today. Aggregates with fewer than 10 samples are marked `INSUFFICIENT_SAMPLE`; no review changes factor weights or emits a recommended strategy.
