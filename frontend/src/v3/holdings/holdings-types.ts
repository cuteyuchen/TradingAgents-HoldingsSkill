/**
 * V3 Holdings typed view-models.
 * Position / Quote / Strategy are three time layers — never merge into one "current" fact.
 */
import type {
  DailyDashboard,
  Holding,
  InstrumentQuote,
  MarketSessionKind,
  PortfolioSnapshot,
} from '../../api/types'

export type V3HoldingStatus =
  | 'HOLD'
  | 'WATCH'
  | 'CONDITIONAL_ADD'
  | 'ADD'
  | 'CONDITIONAL_REDUCE'
  | 'REDUCE'
  | 'EXIT'
  | 'DATA_INSUFFICIENT'
  | 'RISK_BLOCKED'

export type V3QuoteStatus = 'VALID' | 'DEGRADED' | 'STALE' | 'MISSING' | 'IDENTITY_INCOMPLETE'
export type V3StrategyStatus = 'MATCHED' | 'STALE_SNAPSHOT' | 'MISSING' | 'BLOCKED' | 'NO_ACTION' | 'AVAILABLE'

export type V3HoldingFilter = 'all' | 'actionable' | 'conditional' | 'risk_data'
export type V3HoldingSort = 'default' | 'weight' | 'pnl' | 'change' | 'judgment'

export type V3HoldingDirection = 'up' | 'down' | 'flat' | 'unknown'

export interface V3QuoteVM {
  code: string
  last: number | null
  change: number | null
  changePct: number | null
  prevClose: number | null
  observedAt: string | null
  dataBasis: string | null
  quality: string
  qualityFlags: string[]
  status: V3QuoteStatus
  direction: V3HoldingDirection
}

export interface V3HoldingExecutionConstraintVM {
  availableQty: number | null
  unavailableQty: number | null
  /** Deterministic display only — never rewrites strategy. */
  text: string | null
  blocking: boolean
}

export interface V3HoldingDecisionVM {
  status: V3HoldingStatus
  label: string
  secondary: string | null
  rawAction: string | null
  decisionAt: string | null
  strategyQuality: string | null
  decisionSnapshotId: number | null
  keyTrigger: string | null
  riskFlags: string[]
  hardCap: number | null
  headroom: number | null
  strategyStatus: V3StrategyStatus
}

export interface V3HoldingRowVM {
  portfolioId: number | null
  snapshotId: number | null
  code: string
  canonicalCode: string | null
  name: string | null
  instrumentType: string | null
  resolutionStatus: string

  qty: number | null
  availableQty: number | null
  cost: number | null
  snapshotMarketValue: number | null
  snapshotWeight: number | null
  snapshotPnlRatio: number | null
  snapshotPnlAmount: number | null

  quote: V3QuoteVM | null
  liveMarketValueEstimate: number | null
  dayPnlEstimate: number | null
  markedPnlEstimate: number | null
  marketValueSource: 'quote_estimate' | 'snapshot' | 'none'
  pnlSource: 'snapshot' | 'marked_estimate' | 'none'

  judgment: V3HoldingDecisionVM
  execution: V3HoldingExecutionConstraintVM

  quoteStatus: V3QuoteStatus
  strategyStatus: V3StrategyStatus
  unresolved: boolean
  canOpenDetail: boolean
  weight: number | null
  sparklineCode: string | null
}

export interface V3HoldingsSummaryVM {
  totalAssets: number | null
  marketValue: number | null
  marketValueSource: 'snapshot' | 'quote_estimate' | 'none'
  quoteCoverage: number | null
  spendableCash: number | null
  grossExposure: number | null
  dayPnlEstimate: number | null
  dayPnlAvailable: boolean
  floatingPnlSnapshot: number | null
  floatingPnlAvailable: boolean
  positionCount: number | null
  riskFlags: string[]
  hardCapBreaches: string[]
  qualityStatus: string
  strategyState: string
}

export interface V3HoldingsDecisionBarVM {
  kind: 'NO_ACTION' | 'ACTIONABLE' | 'BLOCKED' | 'MISSING' | 'STALE_SNAPSHOT' | 'NO_PORTFOLIO'
  title: string
  subtitle: string
  conclusion: string | null
  quality: string | null
  confidence: number | null
  decisionAt: string | null
  analysisRunId: number | null
  decisionSnapshotId: number | null
  targetRangeText: string | null
  actionableCount: number
  conditionalCount: number
  riskBlockedCount: number
  dataInsufficientCount: number
  riskFlags: string[]
}

export interface V3HoldingsTimestampsVM {
  snapshotAt: string | null
  quoteAt: string | null
  strategyAt: string | null
  session: MarketSessionKind | null
  sessionLabel: string
}

export interface V3HoldingsViewModel {
  hasPortfolio: boolean
  portfolioId: number | null
  portfolioName: string | null
  snapshotId: number | null
  snapshotTime: string | null
  identityIncomplete: boolean
  emptySnapshot: boolean
  noSnapshot: boolean
  timestamps: V3HoldingsTimestampsVM
  summary: V3HoldingsSummaryVM | null
  decision: V3HoldingsDecisionBarVM
  rows: V3HoldingRowVM[]
  filter: V3HoldingFilter
  sort: V3HoldingSort
}

export interface BuildHoldingsInput {
  hasPortfolio: boolean
  portfolioId: number | null
  portfolioName: string | null
  snapshot: PortfolioSnapshot | null
  dashboard: DailyDashboard | null
  quotes: Map<string, V3QuoteVM>
  quotesAsOf: string | null
  quoteCoverage: number | null
  session: MarketSessionKind | null
  sessionLabel: string
  filter: V3HoldingFilter
  sort: V3HoldingSort
}

export interface RawQuoteItem extends Omit<InstrumentQuote, 'instrument'> {
  code: string
}

export type { Holding, PortfolioSnapshot, DailyDashboard }
