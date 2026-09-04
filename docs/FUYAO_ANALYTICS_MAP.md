# Fuyao Analytics Map

This map defines the O.2 evidence and context boundary. Fuyao can enrich an
explanation, but the data below does not change the frozen Market Score,
Candidate Score, Portfolio Gate, or Shadow contracts.

| Analysis | Source | Deterministic | Production Score | UI | PIT Safe |
|---|---|---:|---:|---|---|
| Major Index Context | `/api/a-share-index/prices/snapshot` | Yes | NO | Home, Analysis market context | CURRENT_SAFE |
| Industry Strength | THS index catalog + index snapshot | Yes | NO | Home, Analysis | CURRENT_SAFE for current data |
| Industry Breadth | THS index catalog/constituent context when supplied | Yes | NO | Analysis, drawer | CURRENT_SAFE; historical membership not proven |
| Limit Up/Down | `/api/a-share/special-data/limit-up-pool`, `limit-down-pool` | Yes | NO | Home sentiment, Analysis | HISTORICAL_PIT_UNKNOWN |
| Hot List | `/api/a-share/special-data/hot-stock-list` | Yes | NO | Analysis, candidate evidence | CURRENT_SAFE only |
| Hot Rank Trend | `/api/a-share/special-data/hot-stock-rank-trend` | Yes | NO | Candidate evidence | HISTORICAL_PIT_UNKNOWN |
| Abnormal | `/api/a-share/special-data/anomaly-analysis-list` | Yes | NO | Home sentiment, candidate risk | HISTORICAL_PIT_UNKNOWN |
| Dragon Tiger | `/api/a-share/special-data/dragon-tiger-list` | Yes | NO | Analysis, candidate risk | HISTORICAL_PIT_UNKNOWN |
| Valuation | `/api/a-share/valuations/snapshot` | Yes | NO | Holdings drawer, candidate evidence | CURRENT_SAFE; historical samples unavailable |
| Financial Growth | `/api/a-share/financials/income-statements` + indicators | Yes | NO | Holdings drawer, candidate evidence | CURRENT_ANALYSIS_ALLOWED; historical PIT not proven |
| Profitability | income statements + financial indicators | Yes | NO | Holdings drawer, candidate evidence | CURRENT_ANALYSIS_ALLOWED; historical PIT not proven |
| Cash Flow | `/api/a-share/financials/cash-flow-statements` + indicators | Yes | NO | Holdings drawer, candidate evidence | CURRENT_ANALYSIS_ALLOWED; historical PIT not proven |
| Portfolio Contribution | confirmed snapshot + critical quote chain | Yes | NO | Holdings table/drawer | Current quote freshness required; no fake zero |
| Candidate Enrichment | quote, valuation, financial, index and special-data context | Yes | NO | Analysis candidate drawer | Current context only unless each source proves PIT |

## Rules

- Percentages from Fuyao are percent points; they are not passed through the
  application's decimal-percent formatter a second time.
- Missing, stale, invalid, or conflict quotes remain unavailable. Missing
  sentiment data is not rendered as zero.
- Context analytics may explain a frozen score but may not add points such as
  `hot list +10`, `dragon tiger +5`, or `limit up +8`.
- Current index constituents cannot be used to replay historical membership.
- Financial and valuation summaries expose `HISTORICAL_PIT_NOT_PROVEN` until
  Fuyao provides an auditable announcement/published/available timestamp.
- LLM prompts receive compact summaries and evidence status, never raw
  multi-page financial JSON as an authority for deterministic calculations.
