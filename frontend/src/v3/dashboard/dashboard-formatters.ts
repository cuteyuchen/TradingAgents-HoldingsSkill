/**
 * Dashboard pure formatters and adapter helpers.
 * Missing/unknown never becomes 0.
 */
import type {
  DailyDashboard,
  DashboardSection,
  MajorIndexQuote,
  MarketBreadthMetric,
  MarketSessionKind,
  MarketSessionResponse,
  SystemicRiskSnapshot,
  TotalTurnoverMetric,
  TurnoverConcentrationMetric,
  AllAMedianMetric,
} from '../../api/types'
import {
  MAJOR_INDEX_ORDER,
  type V3ActionRowVM,
  type V3BreadthVM,
  type V3ConcentrationVM,
  type V3DecisionKind,
  type V3DecisionVM,
  type V3ImportantEventVM,
  type V3ImportantEventsVM,
  type V3IndexCardVM,
  type V3LatestAnalysisVM,
  type V3PortfolioSummaryVM,
  type V3RiskLevel,
  type V3SessionBarVM,
  type V3SystemicRiskVM,
  type V3Tone,
  type V3TurnoverVM,
  type V3TypicalStockVM,
  type V3DashboardViewModel,
} from './dashboard-types'

export function isNum(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function numOrNull(value: unknown): number | null {
  return isNum(value) ? value : null
}

export function strOrNull(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

export function formatNumber(value: number | null, digits = 2): string {
  if (value === null) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatPercentFromPoints(value: number | null, digits = 2): string {
  if (value === null) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}

export function formatRatioPercent(value: number | null, digits = 1): string {
  if (value === null) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function formatMoney(value: number | null): string {
  if (value === null) return '—'
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
}

export function formatCount(value: number | null): string {
  if (value === null) return '—'
  return String(Math.round(value))
}

export function directionFromChange(change: number | null, changePct: number | null): 'up' | 'down' | 'flat' | 'unknown' {
  const basis = changePct ?? change
  if (basis === null) return 'unknown'
  if (basis > 0) return 'up'
  if (basis < 0) return 'down'
  return 'flat'
}

export const SESSION_LABELS: Record<MarketSessionKind, string> = {
  PRE_OPEN: '盘前',
  OPEN_AUCTION: '开盘集合竞价',
  MORNING: '上午交易',
  LUNCH_BREAK: '午间休市',
  AFTERNOON: '下午交易',
  CLOSE_AUCTION: '收盘集合竞价',
  CLOSED: '已收盘',
  NON_TRADING_DAY: '非交易日',
  DATA_ABNORMAL: '数据异常',
}

export function sessionTone(kind: MarketSessionKind | null): V3Tone {
  switch (kind) {
    case 'MORNING':
    case 'AFTERNOON':
    case 'OPEN_AUCTION':
    case 'CLOSE_AUCTION':
      return 'info'
    case 'DATA_ABNORMAL':
      return 'danger'
    case 'NON_TRADING_DAY':
      return 'neutral'
    case 'CLOSED':
      return 'blocked'
    case 'LUNCH_BREAK':
      return 'warning'
    default:
      return 'neutral'
  }
}

export function riskTone(level: V3RiskLevel): V3Tone {
  switch (level) {
    case 'LOW':
      return 'info'
    case 'MEDIUM':
      return 'warning'
    case 'HIGH':
      return 'danger'
    case 'EXTREME':
      return 'danger'
    default:
      return 'blocked'
  }
}

export function mapMajorIndices(rows: MajorIndexQuote[] | null | undefined): V3IndexCardVM[] {
  const byCode = new Map<string, MajorIndexQuote>()
  for (const row of rows || []) {
    if (row?.code) byCode.set(String(row.code), row)
  }
  return MAJOR_INDEX_ORDER.map((def) => {
    const raw = byCode.get(def.code)
    const last = numOrNull(raw?.last)
    const change = numOrNull(raw?.change)
    const changePct = numOrNull(raw?.change_pct)
    const status = raw?.status === 'available' && last !== null ? ('available' as const) : ('unavailable' as const)
    const quality = strOrNull(raw?.quality) || (raw ? 'UNKNOWN' : 'MISSING')
    return {
      code: def.code,
      name: def.name,
      last,
      change,
      changePct,
      quoteTs: strOrNull(raw?.as_of),
      status,
      quality,
      stale: status === 'available' && !['VALID', 'DEGRADED', 'A', 'B'].includes(quality.toUpperCase()),
      direction: status === 'available' ? directionFromChange(change, changePct) : 'unknown',
    }
  })
}

export function mapSessionBar(
  session: MarketSessionResponse | null,
  quoteAt: string | null,
  strategyAt: string | null,
): V3SessionBarVM {
  const kind = (session?.session ?? null) as MarketSessionKind | null
  const dataBasis = strOrNull(session?.data_basis) || '—'
  const isPreviousClose = dataBasis === 'previous_session_close' || kind === 'NON_TRADING_DAY' || kind === 'CLOSED'
  const quoteStateText = isPreviousClose
    ? '上一交易日收盘'
    : kind
      ? SESSION_LABELS[kind]
      : '状态未知'
  return {
    session: kind,
    sessionLabel: kind ? SESSION_LABELS[kind] : '状态未知',
    displayLabel: strOrNull(session?.display_label) || (kind ? SESSION_LABELS[kind] : '状态未知'),
    dataBasis,
    tradingDate: strOrNull(session?.trading_date),
    isPreviousClose,
    quoteAt,
    strategyAt,
    quoteStateText,
    sessionTone: sessionTone(kind),
  }
}

export function mapSystemicRisk(snapshot: SystemicRiskSnapshot | null | undefined): V3SystemicRiskVM | null {
  if (!snapshot) return null
  const level = (strOrNull(snapshot.risk_level) || 'UNKNOWN') as V3RiskLevel
  return {
    level,
    score: numOrNull(snapshot.risk_score),
    stateText: level === 'UNKNOWN' ? '风险状态未知' : `系统性风险 · ${level}`,
    factors: Array.isArray(snapshot.risk_factors) ? snapshot.risk_factors.filter((item) => typeof item === 'string') : [],
    riskTone: riskTone(level),
    asOf: strOrNull(snapshot.as_of),
  }
}

export function mapTypicalStock(median: AllAMedianMetric | null | undefined): V3TypicalStockVM | null {
  if (!median) return null
  return {
    medianDaily: numOrNull(median.daily_median_return ?? median.current_value),
    trend20d: numOrNull(median.trend_20d),
    percentile250d: numOrNull(median.percentile_250d),
    status: strOrNull(median.status) || 'unavailable',
    asOf: strOrNull(median.as_of),
    tradingDate: strOrNull(median.trading_date),
  }
}

export function mapBreadth(breadth: MarketBreadthMetric | null | undefined): V3BreadthVM | null {
  if (!breadth) return null
  const unavailable = breadth.status === 'unavailable'
  return {
    advancers: unavailable ? null : numOrNull(breadth.advancers),
    decliners: unavailable ? null : numOrNull(breadth.decliners),
    unchanged: unavailable ? null : numOrNull(breadth.unchanged),
    limitUp: unavailable ? null : numOrNull(breadth.limit_up),
    limitDown: unavailable ? null : numOrNull(breadth.limit_down),
    breadthRatio: numOrNull(breadth.advance_decline_ratio),
    status: strOrNull(breadth.status) || 'unavailable',
  }
}

export function mapTurnover(turnover: TotalTurnoverMetric | null | undefined): V3TurnoverVM | null {
  if (!turnover) return null
  return {
    total: numOrNull(turnover.value),
    totalAvg20d: numOrNull(turnover.avg_20d),
    status: strOrNull(turnover.status) || 'unavailable',
  }
}

export function mapConcentration(metric: TurnoverConcentrationMetric | null | undefined): V3ConcentrationVM | null {
  if (!metric) return null
  const trend = (strOrNull(metric.trend) || 'unavailable') as V3ConcentrationVM['trend']
  const history: number[] = []
  if (isNum(metric.avg_20d)) history.push(metric.avg_20d)
  if (isNum(metric.ratio)) history.push(metric.ratio)
  return {
    ratio: numOrNull(metric.ratio),
    avg20d: numOrNull(metric.avg_20d),
    deltaVs20d: numOrNull(metric.delta_vs_20d),
    trend,
    percentile250d: numOrNull(metric.percentile_250d),
    status: strOrNull(metric.status) || 'unavailable',
    history,
  }
}

function sectionField(section: DashboardSection | undefined, key: string): unknown {
  if (!section || typeof section !== 'object') return undefined
  return (section as Record<string, unknown>)[key]
}

export function mapPortfolioSummary(
  dashboard: DailyDashboard | null,
  portfolioId: number | null,
  portfolioName: string | null,
): V3PortfolioSummaryVM | null {
  if (!dashboard || portfolioId === null) return null
  const section = dashboard.portfolio
  const status = strOrNull(section?.status) || 'MISSING'
  if (status === 'MISSING' && sectionField(section, 'snapshot_id') == null && sectionField(section, 'total_assets') == null) {
    return {
      portfolioId,
      portfolioName,
      status: 'MISSING',
      freshness: strOrNull(sectionField(section, 'freshness')) || 'MISSING',
      snapshotId: null,
      snapshotTime: null,
      totalAssets: null,
      marketValue: null,
      spendableCash: null,
      reserveAssets: null,
      cashRatio: null,
      grossExposure: null,
      positionCount: null,
      qualityStatus: 'MISSING',
      riskFlags: [],
      dayReturn: null,
      floatingPnl: null,
      dayReturnAvailable: false,
      floatingPnlAvailable: false,
    }
  }
  return {
    portfolioId,
    portfolioName,
    status,
    freshness: strOrNull(sectionField(section, 'freshness')) || 'UNKNOWN',
    snapshotId: numOrNull(sectionField(section, 'snapshot_id')),
    snapshotTime: strOrNull(sectionField(section, 'snapshot_time')),
    totalAssets: numOrNull(sectionField(section, 'total_assets')),
    marketValue: numOrNull(sectionField(section, 'market_value')),
    spendableCash: numOrNull(sectionField(section, 'spendable_cash')),
    reserveAssets: numOrNull(sectionField(section, 'reserve_assets')),
    cashRatio: numOrNull(sectionField(section, 'cash_ratio')),
    grossExposure: numOrNull(sectionField(section, 'gross_exposure')),
    positionCount: numOrNull(sectionField(section, 'position_count')),
    qualityStatus: strOrNull(sectionField(section, 'quality_status')) || status,
    riskFlags: Array.isArray(sectionField(section, 'risk_flags'))
      ? (sectionField(section, 'risk_flags') as unknown[]).filter((item): item is string => typeof item === 'string')
      : [],
    // Dashboard portfolio contract has no authoritative day-return / floating-P&L amounts.
    dayReturn: null,
    floatingPnl: null,
    dayReturnAvailable: false,
    floatingPnlAvailable: false,
  }
}

function asActionList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object') : []
}

function mapActionRows(rows: Array<Record<string, unknown>>, kind: 'holding' | 'candidate'): V3ActionRowVM[] {
  return rows.map((row) => ({
    kind,
    code: strOrNull(row.code),
    name: strOrNull(row.name),
    action: strOrNull(row.action ?? row.portfolio_action ?? row.holding_action ?? row.decision),
    reason: strOrNull(row.reason ?? row.rationale ?? row.summary),
    stage: strOrNull(row.stage ?? row.display_stage),
  }))
}

const NO_ACTION_SET = new Set(['NO_ACTION', 'HOLD', 'HOLD_ONLY', 'WATCH', 'WATCH_ONLY'])
const ACTION_SET = new Set(['ACTION', 'ADD', 'BUY', 'REDUCE', 'SELL', 'EXIT', 'REBALANCE', 'TRIM', 'INCREASE'])

function normalizeConclusion(raw: unknown): string {
  return String(raw || '').trim().toUpperCase()
}

export function mapDecision(
  dashboard: DailyDashboard | null,
  hasPortfolio: boolean,
): V3DecisionVM {
  if (!hasPortfolio) {
    return {
      kind: 'NO_PORTFOLIO',
      title: '需先建立组合后才能生成组合决策',
      subtitle: '导入或选择投资组合后，系统才能给出今日行动结论。',
      tone: 'warning',
      actionCount: 0,
      holdingActions: [],
      candidateActions: [],
      reasons: [],
      conclusion: null,
      quality: null,
      decisionAt: null,
      analysisRunId: null,
    }
  }
  if (!dashboard) {
    return {
      kind: 'MISSING',
      title: '暂无有效策略结论',
      subtitle: '组合决策数据尚未返回。',
      tone: 'blocked',
      actionCount: 0,
      holdingActions: [],
      candidateActions: [],
      reasons: [],
      conclusion: null,
      quality: null,
      decisionAt: null,
      analysisRunId: null,
    }
  }

  const decisions = dashboard.decisions
  const analysis = dashboard.analysis
  const decisionStatus = normalizeConclusion(sectionField(decisions, 'status')) || 'MISSING'
  const analysisStatus = normalizeConclusion(sectionField(analysis, 'status')) || 'MISSING'
  const latestDecision = (sectionField(decisions, 'latest') || null) as Record<string, unknown> | null
  const latestAnalysis = (sectionField(analysis, 'latest') || null) as Record<string, unknown> | null
  const analysisInProgress = Boolean(sectionField(analysis, 'analysis_in_progress'))
  const hasValidDecision = decisionStatus === 'AVAILABLE' && !!latestDecision && Object.keys(latestDecision).length > 0
  const hasValidAnalysis = analysisStatus === 'AVAILABLE' && !!latestAnalysis && Object.keys(latestAnalysis).length > 0

  const quality = normalizeConclusion(
    (latestDecision as Record<string, unknown> | null)?.quality
      ?? (latestAnalysis as Record<string, unknown> | null)?.quality,
  ) || null

  if (!hasValidDecision && !hasValidAnalysis) {
    return {
      kind: 'MISSING',
      title: analysisInProgress ? '暂无有效策略结论' : '暂无有效策略结论',
      subtitle: analysisInProgress
        ? '分析进行中，完成后才会给出今日结论。'
        : '今日尚无有效 latest decision / analysis，不能视为明确无需操作。',
      tone: 'blocked',
      actionCount: 0,
      holdingActions: [],
      candidateActions: [],
      reasons: [],
      conclusion: null,
      quality,
      decisionAt: null,
      analysisRunId: null,
    }
  }

  const holdingActions = mapActionList(
    (latestDecision as Record<string, unknown> | null)?.holding_actions,
    (latestAnalysis as Record<string, unknown> | null)?.holding_actions,
  )
  const candidateActions = mapActionList(
    (latestDecision as Record<string, unknown> | null)?.candidate_actions,
    null,
  )

  if (quality === 'BLOCKED' || quality === 'DATA_GAP') {
    return {
      kind: 'BLOCKED',
      title: '策略暂不可执行',
      subtitle: quality === 'DATA_GAP' ? '数据质量不足，无法形成可靠行动。' : '硬约束或数据质量门控阻断。',
      tone: 'blocked',
      actionCount: 0,
      holdingActions,
      candidateActions,
      reasons: collectReasons(latestDecision, latestAnalysis, ['数据质量不足', quality || 'BLOCKED']),
      conclusion: 'BLOCKED',
      quality,
      decisionAt: strOrNull((latestDecision as Record<string, unknown> | null)?.decision_at),
      analysisRunId: numOrNull((latestDecision as Record<string, unknown> | null)?.analysis_run_id),
    }
  }

  const rawConclusion =
    (latestDecision as Record<string, unknown> | null)?.conclusion
    ?? (latestAnalysis as Record<string, unknown> | null)?.portfolio_action
    ?? (latestAnalysis as Record<string, unknown> | null)?.final_rating
    ?? sectionField(decisions, 'final_action')
  const conclusion = normalizeConclusion(rawConclusion)

  if (conclusion === 'BLOCKED' || conclusion === 'BLOCK') {
    return {
      kind: 'BLOCKED',
      title: '策略暂不可执行',
      subtitle: '结构化决策标记为 BLOCKED。',
      tone: 'blocked',
      actionCount: 0,
      holdingActions,
      candidateActions,
      reasons: collectReasons(latestDecision, latestAnalysis, ['策略受阻']),
      conclusion: 'BLOCKED',
      quality,
      decisionAt: strOrNull((latestDecision as Record<string, unknown> | null)?.decision_at),
      analysisRunId: numOrNull((latestDecision as Record<string, unknown> | null)?.analysis_run_id),
    }
  }

  const actionCount = holdingActions.filter((row) => row.action && ACTION_SET.has(normalizeConclusion(row.action))).length
    + candidateActions.filter((row) => row.action && ACTION_SET.has(normalizeConclusion(row.action))).length

  if (NO_ACTION_SET.has(conclusion) || conclusion === '') {
    return {
      kind: 'NO_ACTION',
      title: '今日无需操作',
      subtitle: '有效策略结论为不操作 / 观察。',
      tone: 'info',
      actionCount: 0,
      holdingActions: [],
      candidateActions: [],
      reasons: collectReasons(latestDecision, latestAnalysis, ['当前没有足够的新信息改变组合决策。']),
      conclusion: conclusion || 'NO_ACTION',
      quality,
      decisionAt: strOrNull((latestDecision as Record<string, unknown> | null)?.decision_at),
      analysisRunId: numOrNull((latestDecision as Record<string, unknown> | null)?.analysis_run_id),
    }
  }

  if (ACTION_SET.has(conclusion) || actionCount > 0) {
    return {
      kind: 'ACTIONABLE',
      title: '今日需要行动',
      subtitle: `结构化行动 ${actionCount || 1} 项`,
      tone: 'warning',
      actionCount: actionCount || 1,
      holdingActions,
      candidateActions,
      reasons: collectReasons(latestDecision, latestAnalysis, ['组合层已给出调整建议。']),
      conclusion,
      quality,
      decisionAt: strOrNull((latestDecision as Record<string, unknown> | null)?.decision_at),
      analysisRunId: numOrNull((latestDecision as Record<string, unknown> | null)?.analysis_run_id),
    }
  }

  // Unknown structured conclusion — do not invent NO_ACTION.
  return {
    kind: 'MISSING',
    title: '暂无有效策略结论',
    subtitle: `无法识别的结构化结论：${conclusion || '空'}`,
    tone: 'blocked',
    actionCount: 0,
    holdingActions,
    candidateActions,
    reasons: [],
    conclusion: conclusion || null,
    quality,
    decisionAt: strOrNull((latestDecision as Record<string, unknown> | null)?.decision_at),
    analysisRunId: numOrNull((latestDecision as Record<string, unknown> | null)?.analysis_run_id),
  }
}

function mapActionList(primary: unknown, secondary: unknown): V3ActionRowVM[] {
  const rows = asActionList(primary)
  if (rows.length) return mapActionRows(rows, 'holding')
  const fallback = asActionList(secondary)
  return fallback.length ? mapActionRows(fallback, 'holding') : []
}

function collectReasons(
  latestDecision: Record<string, unknown> | null,
  latestAnalysis: Record<string, unknown> | null,
  fallback: string[],
): string[] {
  const raw =
    (latestDecision as Record<string, unknown> | null)?.reasons
    || (latestDecision as Record<string, unknown> | null)?.top_reasons
    || (latestDecision as Record<string, unknown> | null)?.blocking_reasons
    || (latestAnalysis as Record<string, unknown> | null)?.reasons
  if (Array.isArray(raw)) {
    const list = raw
      .map((item) => (typeof item === 'string' ? item : (item as Record<string, unknown>)?.reason || (item as Record<string, unknown>)?.summary))
      .filter((item): item is string => Boolean(item))
    if (list.length) return list
  }
  return fallback
}

export function mapLatestAnalysis(dashboard: DailyDashboard | null): V3LatestAnalysisVM {
  const section = dashboard?.analysis
  const latest = (sectionField(section, 'latest') || sectionField(section, 'last_analysis') || null) as Record<string, unknown> | null
  const inProgress = Boolean(sectionField(section, 'analysis_in_progress'))
  if (!latest || !Object.keys(latest).length) {
    return {
      available: false,
      analysisInProgress: inProgress,
      finishedAt: null,
      mode: null,
      status: inProgress ? 'RUNNING' : 'MISSING',
      conclusion: null,
      quality: null,
      confidence: null,
      isPrevious: false,
    }
  }
  return {
    available: true,
    analysisInProgress: inProgress,
    finishedAt: strOrNull(latest.finished_at),
    mode: strOrNull(latest.mode),
    status: strOrNull(latest.status) || 'SUCCESS',
    conclusion: strOrNull(latest.portfolio_action ?? latest.final_rating ?? latest.conclusion),
    quality: strOrNull(latest.quality),
    confidence: numOrNull(latest.confidence),
    isPrevious: inProgress,
  }
}

export function mapImportantEvents(dashboard: DailyDashboard | null): V3ImportantEventsVM {
  const timeline = dashboard?.timeline
  const items = Array.isArray(timeline?.timeline) ? timeline.timeline : []
  const events: V3ImportantEventVM[] = items.map((item, index) => ({
    key: strOrNull(item.key) || `timeline-${index}`,
    time: strOrNull(item.time || item.scheduled_at),
    label: strOrNull(item.label) || strOrNull(item.key) || '检查点',
    kind: strOrNull(item.kind) || 'timeline',
    detail: strOrNull(item.mode || item.status),
    isCurrent: Boolean(item.is_current),
  }))
  const nextCheckpoint = events.find((item) => item.isCurrent && item.kind !== 'notification') || events.find((item) => item.kind.includes('CHECK') || item.kind.includes('REVIEW') || item.kind.includes('FAST') || item.kind.includes('DEEP')) || events[0] || null

  const triggerSection = dashboard?.triggers
  const triggerItems = Array.isArray(sectionField(triggerSection, 'items'))
    ? (sectionField(triggerSection, 'items') as Array<Record<string, unknown>>)
    : []
  const triggers: V3ImportantEventVM[] = triggerItems.slice(0, 5).map((row, index) => ({
    key: `trigger-${numOrNull(row.id) ?? index}`,
    time: strOrNull(row.detected_at),
    label: strOrNull(row.reason || row.trigger_type) || '触发器',
    kind: 'trigger',
    detail: strOrNull(row.status),
    isCurrent: strOrNull(row.status) === 'ACTIVE',
  }))

  const notifications = dashboard?.notifications
  const notificationItems = Array.isArray(notifications?.items) ? notifications.items : []
  const warnings: V3ImportantEventVM[] = notificationItems
    .filter((item) => {
      const severity = normalizeConclusion(item.severity)
      return severity === 'WARNING' || severity === 'ERROR' || severity === 'CRITICAL' || severity === 'BLOCKED'
    })
    .slice(0, 5)
    .map((item) => ({
      key: strOrNull(item.notification_id) || item.dedupe_key,
      time: strOrNull(item.occurred_at),
      label: strOrNull(item.title) || strOrNull(item.summary) || '警告',
      kind: 'warning',
      detail: strOrNull(item.summary),
      isCurrent: true,
    }))

  const operational: V3ImportantEventVM[] = notificationItems
    .slice(0, 3)
    .map((item) => ({
      key: strOrNull(item.notification_id) || item.dedupe_key,
      time: strOrNull(item.occurred_at),
      label: strOrNull(item.title) || '通知',
      kind: 'notification',
      detail: strOrNull(item.summary),
      isCurrent: !item.read,
    }))

  return {
    nextCheckpoint,
    warnings,
    notifications: operational,
    triggers,
  }
}

export function buildViewModel(input: {
  session: MarketSessionResponse | null
  majorIndices: MajorIndexQuote[] | null
  systemicRisk: SystemicRiskSnapshot | null
  overview: {
    session?: MarketSessionResponse
    major_indices?: MajorIndexQuote[]
    systemic_risk?: SystemicRiskSnapshot
    breadth?: MarketBreadthMetric
    all_a_median?: AllAMedianMetric
    turnover_concentration?: TurnoverConcentrationMetric
    total_turnover?: TotalTurnoverMetric
    quote_as_of?: string | null
  } | null
  dashboard: DailyDashboard | null
  portfolioId: number | null
  portfolioName: string | null
  hasPortfolio: boolean
}): V3DashboardViewModel {
  const session = input.session || input.overview?.session || input.dashboard?.market_session || null
  const indices = input.majorIndices?.length ? input.majorIndices : input.overview?.major_indices || null
  const risk = input.systemicRisk || input.overview?.systemic_risk || null
  const overview = input.overview
  const quoteAt =
    (indices || []).map((row) => strOrNull(row.as_of)).filter(Boolean).sort().pop()
    || strOrNull(overview?.quote_as_of)
    || strOrNull(risk?.as_of)
    || strOrNull(input.dashboard?.quote_as_of)
  const strategyAt = strOrNull(input.dashboard?.strategy_analysis_at)
    || strOrNull((input.dashboard?.analysis as Record<string, unknown> | undefined)?.latest
      ? ((input.dashboard?.analysis as Record<string, unknown>).latest as Record<string, unknown>)?.finished_at
      : null)

  const marketDegraded =
    !session
    || session.session === 'DATA_ABNORMAL'
    || !indices?.length
    || indices.every((row) => row.status === 'unavailable')

  const portfolio = mapPortfolioSummary(input.dashboard, input.portfolioId, input.portfolioName)
  const portfolioDegraded =
    input.hasPortfolio
    && (!portfolio
      || portfolio.status === 'MISSING'
      || portfolio.freshness === 'STALE'
      || portfolio.freshness === 'MISSING')

  return {
    sessionBar: mapSessionBar(session, quoteAt || null, strategyAt),
    majorIndices: mapMajorIndices(indices),
    systemicRisk: mapSystemicRisk(risk),
    typicalStock: mapTypicalStock(overview?.all_a_median || risk?.median_return),
    breadth: mapBreadth(overview?.breadth || risk?.breadth),
    turnover: mapTurnover(overview?.total_turnover || risk?.total_turnover),
    concentration: mapConcentration(overview?.turnover_concentration || risk?.turnover_concentration),
    portfolio,
    decision: mapDecision(input.dashboard, input.hasPortfolio),
    latestAnalysis: mapLatestAnalysis(input.dashboard),
    importantEvents: mapImportantEvents(input.dashboard),
    marketDegraded,
    portfolioDegraded,
    hasPortfolio: input.hasPortfolio,
    tradeDate: strOrNull(session?.trading_date) || strOrNull(input.dashboard?.trade_date),
  }
}

export function dashboardPollIntervals(session: MarketSessionKind | null): {
  sessionMs: number
  indicesMs: number | null
  riskMs: number | null
  overviewMs: number | null
  portfolioMs: number | null
} {
  const trading = session === 'MORNING' || session === 'AFTERNOON' || session === 'OPEN_AUCTION' || session === 'CLOSE_AUCTION'
  const lunch = session === 'LUNCH_BREAK'
  const closed = session === 'CLOSED' || session === 'NON_TRADING_DAY'
  const abnormal = session === 'DATA_ABNORMAL'
  const pre = session === 'PRE_OPEN'

  let sessionMs = 60_000
  if (!session || abnormal) sessionMs = 10_000
  else if (pre) sessionMs = 20_000
  else if (session === 'OPEN_AUCTION' || session === 'CLOSE_AUCTION') sessionMs = 12_000
  else if (closed) sessionMs = session === 'NON_TRADING_DAY' ? 300_000 : 120_000

  if (trading) {
    return {
      sessionMs,
      indicesMs: 8_000,
      riskMs: 45_000,
      overviewMs: 90_000,
      portfolioMs: 60_000,
    }
  }
  if (lunch) {
    return {
      sessionMs,
      indicesMs: 30_000,
      riskMs: 90_000,
      overviewMs: 120_000,
      portfolioMs: 120_000,
    }
  }
  if (pre) {
    return {
      sessionMs,
      indicesMs: 15_000,
      riskMs: 60_000,
      overviewMs: 120_000,
      portfolioMs: 90_000,
    }
  }
  if (closed || abnormal) {
    return {
      sessionMs,
      indicesMs: null,
      riskMs: null,
      overviewMs: null,
      portfolioMs: null,
    }
  }
  return {
    sessionMs,
    indicesMs: null,
    riskMs: null,
    overviewMs: null,
    portfolioMs: null,
  }
}
