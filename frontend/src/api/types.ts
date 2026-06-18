// Type definitions mirroring the backend Pydantic schemas (1:1).

export type DataGrade = 'A' | 'B' | 'C' | 'D' | 'F'

export interface Quote {
  price?: number | null
  pct_change?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  prev_close?: number | null
  turnover?: number | null
  volume_ratio?: number | null
  source?: string | null
  quote_time?: string | null
  market_session?: string | null
}

export interface Technicals {
  rsi_14?: number | null
  macd_signal?: string | null
  ma_5?: number | null
  ma_20?: number | null
  bollinger_position?: string | null
}

export interface VPA {
  obv_trend?: string | null
  obv_divergence?: boolean | null
  volume_ratio?: number | null
  bar_type?: string | null
  bearish_divergence?: boolean | null
  selling_climax?: boolean | null
  vwma_20?: number | null
}

export interface FundFlow {
  super_large_net?: number | null
  large_net?: number | null
  medium_net?: number | null
  small_net?: number | null
  northbound_net?: number | null
}

export interface HoldingIndicators {
  quote?: Quote | null
  technicals?: Technicals | null
  vpa?: VPA | null
  fund_flow?: FundFlow | null
  red_flags?: string[] | null
}

export interface Claim {
  claim_id: string
  speaker: 'bull' | 'bear' | 'aggressive' | 'conservative' | 'neutral'
  stance?: string | null
  claim: string
  evidence?: string[] | null
  confidence?: number | null
  status: 'open' | 'addressed' | 'resolved' | 'unresolved' | 'accepted' | string
  target_claim_ids?: string[] | null
  round?: number | null
}

export interface QualityGate {
  analyst: string
  hard_check?: string | null
  llm_review?: string | null
  grade?: DataGrade | null
  gaps?: string | null
}

export interface ResearchVerdict {
  rating?: string | null
  winner?: 'bull' | 'bear' | null
  rationale?: string | null
  unresolved_handling?: Record<string, unknown> | null
  strategy?: string | null
  confidence?: string | null
}

export interface RiskRevision {
  verdict: 'pass' | 'revise' | 'reject'
  hard_constraints?: string[] | null
  soft_constraints?: string[] | null
  execution_preconditions?: string[] | null
  de_risk_triggers?: string[] | null
  revision_reason?: string | null
  revised_proposal?: Record<string, unknown> | null
}

export interface TraderProposal {
  code: string
  action?: string | null
  trigger_price?: number | null
  qty?: string | null
  take_profit?: string | null
  stop_loss?: string | null
  invalidation?: string | null
  checkpoint_rule?: string | null
  revision?: RiskRevision | null
}

export interface PMFinal {
  rating?: string | null
  cash_target?: string | null
  actions?: Array<Record<string, unknown>> | null
  priority_notes?: string | null
}

export interface Candidate {
  code: string
  name?: string | null
  type?: 'ETF' | 'stock' | 'watch' | null
  score?: number | null
  score_breakdown?: Record<string, number> | null
  entry_trigger?: string | null
  initial_size?: string | null
  take_profit_1?: string | null
  take_profit_2?: string | null
  stop_loss?: string | null
  invalidation?: string | null
  status: '待触发' | '已命中' | '已取消' | string
}

export interface Holding {
  code: string
  name?: string | null
  qty?: number | null
  available_qty?: number | null
  cost?: number | null
  price?: number | null
  market_value?: number | null
  pnl?: number | null
  data_quality?: DataGrade | null
  raw_return?: number | null
  benchmark_return?: number | null
  alpha?: number | null
  indicators?: HoldingIndicators | null
}

export interface Intent {
  tickers?: string[] | null
  horizon?: string | null
  focus?: string[] | null
  risk_profile?: string | null
  objective?: string | null
}

export interface RunDetail {
  id: number
  timestamp: string
  checkpoint?: string | null
  holdings_source?: string | null
  data_quality_grade?: DataGrade | null
  intent?: Intent | null
  evidence_pack?: Record<string, unknown> | null
  transcript?: string | null
  sections?: Record<string, unknown> | null
  quality_gates: QualityGate[]
  holdings: Holding[]
  claims: Claim[]
  research_verdict?: ResearchVerdict | null
  trader_proposals: TraderProposal[]
  pm_final?: PMFinal | null
  candidates: Candidate[]
}

export interface RunSummary {
  id: number
  timestamp: string
  checkpoint?: string | null
  data_quality_grade?: DataGrade | null
  pm_rating?: string | null
  holdings_count: number
  candidates_count: number
}

export interface TimelinePoint {
  run_id: number
  timestamp?: string | null
  checkpoint?: string | null
  price?: number | null
  cost?: number | null
  pnl?: number | null
  raw_return?: number | null
  benchmark_return?: number | null
  alpha?: number | null
  data_quality?: DataGrade | null
}

export interface HoldingTimeline {
  code: string
  points: TimelinePoint[]
  verdict?: ResearchVerdict | null
  proposal?: { action?: string | null; trigger_price?: number | null; qty?: string | null; stop_loss?: string | null; invalidation?: string | null } | null
  claims: Claim[]
}

export interface WatchlistItem {
  code: string
  name?: string | null
  cadence?: string | null
  enabled: boolean
}

export interface HealthStatus {
  code?: string | null
  checkpoint: string
  consecutive_failures: number
  degraded: boolean
  last_failure_at?: string | null
  last_success_at?: string | null
  note?: string | null
}

export interface BenchmarkPrice {
  date: string
  close: number
  pct_change?: number | null
}
