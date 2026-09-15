/**
 * V3 Dashboard typed view-models.
 * Raw backend contracts are adapted here; unknown/missing becomes null, never 0.
 */
import type {
  MarketSessionKind,
  MarketSessionResponse,
  MajorIndexQuote,
  SystemicRiskSnapshot,
} from '../../api/types'

export const MAJOR_INDEX_ORDER: readonly { code: string; name: string }[] = [
  { code: '000001.SH', name: '上证指数' },
  { code: '399001.SZ', name: '深证成指' },
  { code: '399006.SZ', name: '创业板指' },
  { code: '000300.SH', name: '沪深300' },
  { code: '000852.SH', name: '中证1000' },
  { code: '000688.SH', name: '科创50' },
]

export type V3DecisionKind =
  | 'NO_ACTION'
  | 'ACTIONABLE'
  | 'BLOCKED'
  | 'MISSING'
  | 'NO_PORTFOLIO'

export type V3RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'EXTREME' | 'UNKNOWN'

export type V3Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'blocked'

export interface V3IndexCardVM {
  code: string
  name: string
  last: number | null
  change: number | null
  changePct: number | null
  quoteTs: string | null
  status: 'available' | 'unavailable'
  quality: string
  stale: boolean
  direction: 'up' | 'down' | 'flat' | 'unknown'
}

export interface V3SessionBarVM {
  session: MarketSessionKind | null
  sessionLabel: string
  displayLabel: string
  dataBasis: string
  tradingDate: string | null
  isPreviousClose: boolean
  quoteAt: string | null
  strategyAt: string | null
  quoteStateText: string
  sessionTone: V3Tone
}

export interface V3SystemicRiskVM {
  level: V3RiskLevel
  score: number | null
  stateText: string
  factors: string[]
  riskTone: V3Tone
  asOf: string | null
}

export interface V3TypicalStockVM {
  medianDaily: number | null
  trend20d: number | null
  percentile250d: number | null
  status: string
  asOf: string | null
  tradingDate: string | null
}

export interface V3BreadthVM {
  advancers: number | null
  decliners: number | null
  unchanged: number | null
  limitUp: number | null
  limitDown: number | null
  breadthRatio: number | null
  status: string
}

export interface V3TurnoverVM {
  total: number | null
  totalAvg20d: number | null
  status: string
}

export interface V3ConcentrationVM {
  ratio: number | null
  avg20d: number | null
  deltaVs20d: number | null
  trend: 'rising' | 'falling' | 'flat' | 'unavailable'
  percentile250d: number | null
  status: string
  history: number[]
}

export interface V3PortfolioSummaryVM {
  portfolioId: number | null
  portfolioName: string | null
  status: string
  freshness: string
  snapshotId: number | null
  snapshotTime: string | null
  totalAssets: number | null
  marketValue: number | null
  spendableCash: number | null
  reserveAssets: number | null
  cashRatio: number | null
  grossExposure: number | null
  positionCount: number | null
  qualityStatus: string
  riskFlags: string[]
  dayReturn: number | null
  floatingPnl: number | null
  dayReturnAvailable: boolean
  floatingPnlAvailable: boolean
}

export interface V3ActionRowVM {
  kind: 'holding' | 'candidate'
  code: string | null
  name: string | null
  action: string | null
  reason: string | null
  stage: string | null
}

export interface V3DecisionVM {
  kind: V3DecisionKind
  title: string
  subtitle: string
  tone: V3Tone
  actionCount: number
  holdingActions: V3ActionRowVM[]
  candidateActions: V3ActionRowVM[]
  reasons: string[]
  conclusion: string | null
  quality: string | null
  decisionAt: string | null
  analysisRunId: number | null
}

export interface V3LatestAnalysisVM {
  available: boolean
  analysisInProgress: boolean
  finishedAt: string | null
  mode: string | null
  status: string | null
  conclusion: string | null
  quality: string | null
  confidence: number | null
  isPrevious: boolean
}

export interface V3ImportantEventVM {
  key: string
  time: string | null
  label: string
  kind: string
  detail: string | null
  isCurrent: boolean
}

export interface V3ImportantEventsVM {
  nextCheckpoint: V3ImportantEventVM | null
  warnings: V3ImportantEventVM[]
  notifications: V3ImportantEventVM[]
  triggers: V3ImportantEventVM[]
}

export interface V3DashboardViewModel {
  sessionBar: V3SessionBarVM
  majorIndices: V3IndexCardVM[]
  systemicRisk: V3SystemicRiskVM | null
  typicalStock: V3TypicalStockVM | null
  breadth: V3BreadthVM | null
  turnover: V3TurnoverVM | null
  concentration: V3ConcentrationVM | null
  portfolio: V3PortfolioSummaryVM | null
  decision: V3DecisionVM
  latestAnalysis: V3LatestAnalysisVM
  importantEvents: V3ImportantEventsVM
  marketDegraded: boolean
  portfolioDegraded: boolean
  hasPortfolio: boolean
  tradeDate: string | null
}

export type { MarketSessionKind, MarketSessionResponse, MajorIndexQuote, SystemicRiskSnapshot }
