/**
 * Holdings pure formatters and adapters.
 * Frozen status enum + deterministic action mapping.
 * Missing never becomes 0. Frontend never invents BUY/SELL/REDUCE/ADD.
 */
import type { DailyDashboard, Holding, PortfolioSnapshot } from '../../api/types'
import { dashboardDecisionSource, dashboardDecisionState, decisionLabel, decisionSummary } from '../../utils/decision'
import type {
  BuildHoldingsInput,
  V3HoldingDecisionVM,
  V3HoldingDirection,
  V3HoldingRowVM,
  V3HoldingSort,
  V3HoldingStatus,
  V3HoldingsDecisionBarVM,
  V3HoldingsSummaryVM,
  V3HoldingsTimestampsVM,
  V3HoldingsViewModel,
  V3QuoteVM,
  V3QuoteStatus,
  V3StrategyStatus,
} from './holdings-types'

export function numOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function strOrNull(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

export function formatMoney(value: number | null): string {
  if (value === null) return '—'
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
}

export function formatPrice(value: number | null): string {
  if (value === null) return '—'
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 3 })
}

export function formatQty(value: number | null): string {
  if (value === null) return '—'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

export function formatPercentFromPoints(value: number | null, digits = 2): string {
  if (value === null) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}

export function formatRatioPercent(value: number | null, digits = 1): string {
  if (value === null) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function directionFromChange(change: number | null, changePct: number | null): V3HoldingDirection {
  const basis = changePct ?? change
  if (basis === null) return 'unknown'
  if (basis > 0) return 'up'
  if (basis < 0) return 'down'
  return 'flat'
}

export function directionClass(direction: V3HoldingDirection): string {
  if (direction === 'up') return 'market-up'
  if (direction === 'down') return 'market-down'
  if (direction === 'flat') return 'market-flat'
  return ''
}

/** Frozen mapping — do not invent business states from price/pnl/score. */
export function normalizeHoldingStatus(raw: unknown): V3HoldingStatus {
  const key = String(raw ?? '').trim().toLowerCase().replace(/\s+/g, '_')
  switch (key) {
    case 'hold':
    case 'no_action':
    case 'no-action':
      return 'HOLD'
    case 'watch':
    case 'watch_only':
    case 'waiting':
      return 'WATCH'
    case 'conditional_add':
      return 'CONDITIONAL_ADD'
    case 'add':
    case 'increase':
      return 'ADD'
    case 'conditional_reduce':
      return 'CONDITIONAL_REDUCE'
    case 'reduce':
    case 'trim':
      return 'REDUCE'
    case 'sell':
    case 'exit':
      return 'EXIT'
    case 'blocked':
      return 'RISK_BLOCKED'
    case '':
    case 'missing':
    case 'unknown':
    case 'null':
    case 'undefined':
      return 'DATA_INSUFFICIENT'
    default:
      return 'DATA_INSUFFICIENT'
  }
}

export const HOLDING_STATUS_LABEL: Record<V3HoldingStatus, string> = {
  HOLD: '持有',
  WATCH: '观察',
  CONDITIONAL_ADD: '条件加仓',
  ADD: '加仓',
  CONDITIONAL_REDUCE: '条件减仓',
  REDUCE: '减仓',
  EXIT: '卖出',
  DATA_INSUFFICIENT: '数据不足',
  RISK_BLOCKED: '风险受阻',
}

const ACTIONABLE: ReadonlySet<V3HoldingStatus> = new Set(['ADD', 'REDUCE', 'EXIT'])
const CONDITIONAL: ReadonlySet<V3HoldingStatus> = new Set(['CONDITIONAL_ADD', 'CONDITIONAL_REDUCE', 'WATCH'])

export function isActionableStatus(status: V3HoldingStatus): boolean {
  return ACTIONABLE.has(status)
}

export function isConditionalStatus(status: V3HoldingStatus): boolean {
  return CONDITIONAL.has(status)
}

export function judgmentRank(status: V3HoldingStatus): number {
  if (isActionableStatus(status)) return 0
  if (isConditionalStatus(status)) return 1
  if (status === 'RISK_BLOCKED') return 2
  if (status === 'DATA_INSUFFICIENT') return 3
  return 4
}

function decisionConclusion(dashboard: DailyDashboard | null): string | null {
  const latest = dashboardDecisionSource(dashboard)
  return strOrNull(latest.conclusion)
}

function decisionQuality(dashboard: DailyDashboard | null): string | null {
  return strOrNull(dashboardDecisionSource(dashboard).quality)
}

function decisionSnapshotId(dashboard: DailyDashboard | null): number | null {
  return numOrNull(dashboardDecisionSource(dashboard).portfolio_snapshot_id)
}

function decisionAt(dashboard: DailyDashboard | null): string | null {
  return strOrNull(dashboardDecisionSource(dashboard).decision_at)
}

function portfolioHoldingAction(dashboard: DailyDashboard | null, code: string, canonicalCode: string | null): string | null {
  if (dashboardDecisionSource(dashboard) !== dashboard?.decisions?.latest) return null
  const holdings = (dashboard?.portfolio as { holdings?: Array<Record<string, any>> } | undefined)?.holdings || []
  const short = code.split('.')[0]
  const candidates = holdings.filter((row) => {
    const rowCode = strOrNull(row.code) || ''
    const rowCanonical = strOrNull(row.canonical_code)
    if (!rowCode && !rowCanonical) return false
    if (canonicalCode && (rowCanonical === canonicalCode || rowCode === canonicalCode)) return true
    if (rowCode === code || rowCode === short) return true
    if (rowCanonical === code || rowCanonical === short) return true
    return false
  })
  if (candidates.length !== 1) return null
  return strOrNull(candidates[0].holding_action)
}

function decisionHoldingAction(dashboard: DailyDashboard | null, code: string, canonicalCode: string | null): { action: string | null; trigger: string | null; reason: string | null } {
  const latest = dashboardDecisionSource(dashboard)
  const actions = Array.isArray(latest.holding_actions) ? latest.holding_actions as Array<Record<string, any>> : []
  const short = code.split('.')[0]
  const matched = actions.filter((row) => {
    const rowCode = strOrNull(row.code) || strOrNull(row.security_code) || ''
    const rowCanonical = strOrNull(row.canonical_code)
    if (canonicalCode && (rowCanonical === canonicalCode || rowCode === canonicalCode)) return true
    if (rowCode === code || rowCode === short) return true
    return false
  })
  if (matched.length !== 1) return { action: null, trigger: null, reason: null }
  const row = matched[0]
  return {
    action: strOrNull(row.action) || strOrNull(row.recommended_action) || strOrNull(row.holding_action),
    trigger: strOrNull(row.trigger) || strOrNull(row.condition) || strOrNull(row.invalidation),
    reason: strOrNull(row.reason) || strOrNull(row.summary),
  }
}

function portfolioRiskHolding(dashboard: DailyDashboard | null, code: string, canonicalCode: string | null): Record<string, any> | null {
  const holdings = (dashboard?.portfolio as { holdings?: Array<Record<string, any>> } | undefined)?.holdings || []
  const short = code.split('.')[0]
  const matched = holdings.filter((row) => {
    const rowCode = strOrNull(row.code) || ''
    const rowCanonical = strOrNull(row.canonical_code)
    if (canonicalCode && (rowCanonical === canonicalCode || rowCode === canonicalCode)) return true
    if (rowCode === code || rowCode === short) return true
    if (rowCanonical === code || rowCanonical === short) return true
    return false
  })
  if (matched.length !== 1) return null
  return matched[0]
}

export function resolveRowJudgment(input: {
  holding: Holding
  dashboard: DailyDashboard | null
  snapshotId: number | null
}): V3HoldingDecisionVM {
  const { holding, dashboard, snapshotId } = input
  const canonicalCode = strOrNull(holding.canonical_code)
  const code = strOrNull(holding.code) || ''
  const source = dashboardDecisionSource(dashboard)
  const latest = Object.keys(source).length ? source : null
  const quality = decisionQuality(dashboard)
  const conclusion = decisionConclusion(dashboard)
  const decisionSnap = decisionSnapshotId(dashboard)
  const decisionTime = decisionAt(dashboard)
  // Fail closed: null or mismatched portfolio_snapshot_id is never a current binding.
  const unbound = Boolean(latest && snapshotId !== null && decisionSnap !== snapshotId)
  const blocked = String(quality || '').toUpperCase() === 'BLOCKED' || String(conclusion || '').toUpperCase() === 'BLOCKED'
  const riskRow = portfolioRiskHolding(dashboard, code, canonicalCode)
  const riskFlags = Array.isArray(riskRow?.risk_flags)
    ? (riskRow!.risk_flags as unknown[]).map((item) => String(item)).filter(Boolean)
    : []
  const keyFromDecision = decisionHoldingAction(dashboard, code, canonicalCode)
  const dashboardAction = portfolioHoldingAction(dashboard, code, canonicalCode)
  const hardCap = numOrNull(riskRow?.hard_cap)
  const headroom = numOrNull(riskRow?.headroom)
  const decisionState = dashboardDecisionState(dashboard)
  if (latest && !unbound && ['EXPIRED', 'DATA_GAP', 'INCOMPLETE', 'UNKNOWN'].includes(decisionState)) {
    return { status: 'DATA_INSUFFICIENT', label: decisionLabel(decisionState), secondary: decisionSummary(decisionState),
      rawAction: null, decisionAt: decisionTime, strategyQuality: quality, decisionSnapshotId: decisionSnap,
      keyTrigger: null, riskFlags, hardCap, headroom, strategyStatus: decisionState === 'EXPIRED' ? 'STALE_SNAPSHOT' : 'MISSING' }
  }

  let strategyStatus: V3StrategyStatus = 'MISSING'
  let status: V3HoldingStatus = 'DATA_INSUFFICIENT'
  let secondary: string | null = null
  let rawAction: string | null = null

  if (!dashboard) {
    strategyStatus = 'MISSING'
    status = 'DATA_INSUFFICIENT'
    secondary = '策略数据暂不可用'
  } else if (unbound) {
    strategyStatus = 'STALE_SNAPSHOT'
    status = 'DATA_INSUFFICIENT'
    secondary = '策略基于旧或未绑定持仓快照 · 待重新分析'
  } else if (blocked) {
    strategyStatus = 'BLOCKED'
    if (keyFromDecision.action || dashboardAction) {
      rawAction = keyFromDecision.action || dashboardAction
      status = normalizeHoldingStatus(rawAction)
    } else {
      status = 'RISK_BLOCKED'
    }
    secondary = '策略暂不可执行'
  } else if (!latest) {
    strategyStatus = 'MISSING'
    status = 'DATA_INSUFFICIENT'
    secondary = '暂无有效策略决策'
  } else {
    strategyStatus = 'MATCHED'
    const sourceAction = keyFromDecision.action || dashboardAction
    rawAction = sourceAction
    if (sourceAction) {
      status = normalizeHoldingStatus(sourceAction)
    } else if (String(conclusion).toUpperCase() === 'NO_ACTION') {
      strategyStatus = 'NO_ACTION'
      status = 'HOLD'
      secondary = '组合级无需操作 (NO_ACTION)'
    } else {
      status = 'DATA_INSUFFICIENT'
      secondary = '该持仓无对应系统判断'
    }
  }

  const keyTrigger = keyFromDecision.trigger
    || strOrNull(riskRow?.opportunity_reference)
    || null

  return {
    status,
    label: HOLDING_STATUS_LABEL[status],
    secondary,
    rawAction,
    decisionAt: unbound ? null : decisionTime,
    strategyQuality: quality,
    decisionSnapshotId: decisionSnap,
    keyTrigger,
    riskFlags,
    hardCap,
    headroom,
    strategyStatus,
  }
}

function quoteStatusFrom(quote: V3QuoteVM | null, unresolved: boolean): V3QuoteStatus {
  if (unresolved) return 'IDENTITY_INCOMPLETE'
  if (!quote) return 'MISSING'
  return quote.status
}

function joinQuote(
  holding: Holding,
  quotes: Map<string, V3QuoteVM>,
  portfolioCanonicals: Map<string, number>,
): V3QuoteVM | null {
  if (holding.resolution_status !== 'RESOLVED') return null
  const canonical = strOrNull(holding.canonical_code)
  if (canonical && quotes.has(canonical)) return quotes.get(canonical)!
  const code = strOrNull(holding.code)
  if (code && quotes.has(code)) return quotes.get(code)!
  const short = (canonical || code || '').split('.')[0]
  if (short && quotes.has(short)) return quotes.get(short)!
  if (short && portfolioCanonicals.get(short) === 1) {
    for (const [key, value] of quotes) {
      if (key === short || key.startsWith(`${short}.`)) return value
    }
  }
  return null
}

export function mapQuoteItem(item: {
  code: string
  last?: number | null
  change?: number | null
  change_pct?: number | null
  prev_close?: number | null
  observed_at?: string | null
  trading_date?: string | null
  data_basis?: string | null
  quality?: string | null
  quality_flags?: string[] | null
  status?: string | null
}): V3QuoteVM | null {
  const code = strOrNull(item.code)
  if (!code) return null
  const last = numOrNull(item.last)
  const change = numOrNull(item.change)
  const changePct = numOrNull(item.change_pct)
  const quality = String(item.quality || '').trim().toUpperCase()
  const status = String(item.status || '').trim().toUpperCase()
  const flags = Array.isArray(item.quality_flags) ? item.quality_flags.map(flag => String(flag).trim().toUpperCase()) : []
  // 数字存在不等于质量可信；缺失、未知或冲突标识不能默认升级为有效行情。
  const trustedStatus = ['AVAILABLE', 'VALID', 'DEGRADED', 'STALE'].includes(status)
  const trustedQuality = ['A', 'B', 'C', 'D', 'VALID', 'DEGRADED', 'STALE'].includes(quality)
  const invalidFlag = flags.some(flag => /(^|_)(INVALID|CONFLICT)(_|$)/.test(flag)
    || ['FUTURE_OBSERVED_TIME', 'OBSERVED_DATE_MISMATCH', 'QUOTE_UNAVAILABLE', 'INSTRUMENT_SUSPENDED', 'INSTRUMENT_NOT_FOUND'].includes(flag))
  const staleFlag = flags.some(flag => /(^|_)STALE(_|$)/.test(flag) || flag === 'CLOSE_NOT_CONFIRMED')
  let quoteStatus: V3QuoteStatus = 'MISSING'
  if (last !== null && last > 0 && trustedStatus && trustedQuality && !invalidFlag) {
    if (status === 'STALE' || quality === 'STALE' || staleFlag) quoteStatus = 'STALE'
    else if (status === 'DEGRADED' || quality === 'DEGRADED' || quality === 'C' || quality === 'D') quoteStatus = 'DEGRADED'
    else quoteStatus = 'VALID'
  }
  return {
    code,
    last,
    change,
    changePct,
    prevClose: numOrNull(item.prev_close),
    observedAt: strOrNull(item.observed_at),
    tradingDate: strOrNull(item.trading_date),
    dataBasis: strOrNull(item.data_basis),
    quality: quality || 'MISSING',
    qualityFlags: flags,
    status: quoteStatus,
    direction: directionFromChange(change, changePct),
  }
}

/** 展示、当期估值和执行行情资格分开，历史数字不能冒充今日变化。 */
export function quoteEligibility(quote: V3QuoteVM | null, session: BuildHoldingsInput['session'], tradingDate: string | null): {
  displayable: boolean; valuation: boolean; execution: boolean
} {
  const displayable = Boolean(quote && quote.last !== null && quote.last > 0 && quote.status !== 'MISSING')
  const valuation = Boolean(displayable && quote && ['VALID', 'DEGRADED'].includes(quote.status)
    && tradingDate && quote.tradingDate === tradingDate
    && ['live', 'session_close'].includes(quote.dataBasis || '')
    && session && !['PRE_OPEN', 'NON_TRADING_DAY', 'DATA_ABNORMAL'].includes(session))
  const execution = Boolean(valuation && quote?.status === 'VALID' && quote.dataBasis === 'live'
    && session && ['OPEN_AUCTION', 'MORNING', 'AFTERNOON', 'CLOSE_AUCTION'].includes(session))
  return { displayable, valuation, execution }
}

function buildExecution(qty: number | null, availableQty: number | null, judgmentStatus: V3HoldingStatus, quoteExecutable: boolean): {
  availableQty: number | null
  unavailableQty: number | null
  text: string | null
  blocking: boolean
} {
  const available = availableQty
  let unavailable: number | null = null
  if (qty !== null && available !== null && qty > available) unavailable = qty - available
  let text: string | null = null
  let blocking = false
  const selling = judgmentStatus === 'REDUCE' || judgmentStatus === 'EXIT'
  if (available !== null && available <= 0 && selling) {
    text = `执行约束：当前可用 ${available}`
    blocking = true
  } else if (unavailable !== null && unavailable > 0) {
    text = `当前不可用 ${unavailable} · 由快照数量差计算`
  }
  if (isActionableStatus(judgmentStatus) && !quoteExecutable) {
    text = [text, '执行前需核对当前有效行情'].filter(Boolean).join('；')
    blocking = true
  }
  return { availableQty: available, unavailableQty: unavailable, text, blocking }
}

function buildSummary(input: BuildHoldingsInput, rows: V3HoldingRowVM[]): V3HoldingsSummaryVM | null {
  const { snapshot, dashboard } = input
  if (!snapshot) return null
  const portfolio = dashboard?.portfolio
  const totalAssets = numOrNull(snapshot.total_assets) ?? numOrNull(portfolio?.total_assets)
  const cash = input.accountState ? numOrNull(input.accountState.cash) : numOrNull(snapshot.broker_available_cash) ?? numOrNull(portfolio?.spendable_cash)
  const snapshotMv = numOrNull(snapshot.total_market_value)
  // 已清仓不再参与账户合计；缺数量或身份的行仍占覆盖分母，不能伪装成完整估值。
  const activeRows = rows.filter((row) => row.qty !== 0)
  const ratio = (covered: number) => activeRows.length ? covered / activeRows.length : null
  const identityCoverage = ratio(activeRows.filter(row => !row.unresolved).length)
  const quoteCoverage = ratio(activeRows.filter(row => row.quoteDisplayable).length)
  const valuationRows = activeRows.filter(row => row.liveMarketValueEstimate !== null)
  const valuationCoverage = ratio(valuationRows.length)
  let marketValue: number | null = null
  let marketValueSource: V3HoldingsSummaryVM['marketValueSource'] = 'none'
  if (activeRows.length && valuationRows.length === activeRows.length) {
    marketValue = valuationRows.reduce((sum, row) => sum + row.liveMarketValueEstimate!, 0)
    marketValueSource = 'quote_estimate'
  } else if (snapshotMv !== null) {
    marketValue = snapshotMv
    marketValueSource = 'snapshot'
  }

  const snapshotPnlComplete = activeRows.length > 0
    && activeRows.every((row) => row.snapshotPnlAmount !== null)
  const floatingPnlSnapshot = snapshotPnlComplete
    ? activeRows.reduce((sum, row) => sum + row.snapshotPnlAmount!, 0)
    : null

  const dayCovered = activeRows.filter((row) => row.dayPnlEstimate !== null)
  const dayPnlAvailable = activeRows.length > 0 && dayCovered.length === activeRows.length
  const dayPnlEstimate = dayPnlAvailable
    ? dayCovered.reduce((sum, row) => sum + (row.dayPnlEstimate || 0), 0)
    : null

  const riskFlags = Array.isArray(portfolio?.risk_flags)
    ? (portfolio!.risk_flags as unknown[]).map(String)
    : []
  const hardCapBreaches = Array.isArray(portfolio?.hard_cap_breaches)
    ? (portfolio!.hard_cap_breaches as unknown[]).map(String)
    : []

  const actionable = rows.filter((row) => isActionableStatus(row.judgment.status)).length
  const strategyState = dashboard?.decisions?.latest
    ? (actionable ? `需处理 ${actionable}` : decisionLabel(dashboardDecisionState(dashboard)))
    : '策略不可用'

  return {
    totalAssets,
    marketValue,
    marketValueSource,
    quoteCoverage,
    identityCoverage,
    valuationCoverage,
    spendableCash: cash,
    grossExposure: numOrNull(portfolio?.gross_exposure),
    dayPnlEstimate,
    dayPnlAvailable,
    floatingPnlSnapshot,
    floatingPnlAvailable: floatingPnlSnapshot !== null,
    positionCount: activeRows.length,
    riskFlags,
    hardCapBreaches,
    qualityStatus: strOrNull(portfolio?.quality_status) || 'UNKNOWN',
    strategyState,
  }
}

function buildDecisionBar(input: BuildHoldingsInput, rows: V3HoldingRowVM[]): V3HoldingsDecisionBarVM {
  const { dashboard, hasPortfolio, snapshot } = input
  const snapshotId = snapshot?.id ?? null
  if (!hasPortfolio) {
    return {
      kind: 'NO_PORTFOLIO',
      title: '尚未选择投资组合',
      subtitle: '创建或选择组合后查看系统判断',
      conclusion: null,
      quality: null,
      confidence: null,
      decisionAt: null,
      analysisRunId: null,
      decisionSnapshotId: null,
      targetRangeText: null,
      actionableCount: 0,
      conditionalCount: 0,
      riskBlockedCount: 0,
      dataInsufficientCount: 0,
      riskFlags: [],
    }
  }
  const source = dashboardDecisionSource(dashboard)
  const latest = Object.keys(source).length ? source : null
  const decisionSnap = decisionSnapshotId(dashboard)
  const quality = decisionQuality(dashboard)
  const conclusion = decisionConclusion(dashboard)
  const unbound = Boolean(latest && snapshotId !== null && decisionSnap !== snapshotId)
  const blocked = String(quality || '').toUpperCase() === 'BLOCKED' || String(conclusion || '').toUpperCase() === 'BLOCKED'
  const actionableCount = rows.filter((row) => isActionableStatus(row.judgment.status)).length
  const conditionalCount = rows.filter((row) => isConditionalStatus(row.judgment.status)).length
  const riskBlockedCount = rows.filter((row) => row.judgment.status === 'RISK_BLOCKED').length
  const dataInsufficientCount = rows.filter((row) => row.judgment.status === 'DATA_INSUFFICIENT').length
  const riskFlags = Array.isArray(dashboard?.portfolio?.risk_flags)
    ? (dashboard!.portfolio!.risk_flags as unknown[]).map(String)
    : []

  if (!snapshot) {
    return {
      kind: 'MISSING',
      title: '还没有确认持仓快照',
      subtitle: '上传或粘贴持仓截图后确认快照',
      conclusion: null,
      quality,
      confidence: null,
      decisionAt: null,
      analysisRunId: null,
      decisionSnapshotId: decisionSnap,
      targetRangeText: null,
      actionableCount: 0,
      conditionalCount: 0,
      riskBlockedCount: 0,
      dataInsufficientCount: 0,
      riskFlags,
    }
  }

  if (!latest) {
    return {
      kind: 'MISSING',
      title: '暂无系统判断',
      subtitle: '策略数据暂不可用，持仓与行情仍可查看',
      conclusion: null,
      quality,
      confidence: null,
      decisionAt: null,
      analysisRunId: null,
      decisionSnapshotId: null,
      targetRangeText: null,
      actionableCount,
      conditionalCount,
      riskBlockedCount,
      dataInsufficientCount,
      riskFlags,
    }
  }

  if (unbound) {
    return {
      kind: 'STALE_SNAPSHOT',
      title: '策略基于旧或未绑定持仓快照',
      subtitle: '待重新分析 · 旧或未绑定动作不得套用到新快照',
      conclusion: null,
      quality,
      confidence: numOrNull(latest.confidence),
      decisionAt: strOrNull(latest.decision_at),
      analysisRunId: numOrNull(latest.analysis_run_id),
      decisionSnapshotId: decisionSnap,
      targetRangeText: null,
      actionableCount: 0,
      conditionalCount: 0,
      riskBlockedCount: 0,
      dataInsufficientCount: rows.length,
      riskFlags,
    }
  }

  if (blocked) {
    return {
      kind: 'BLOCKED',
      title: '策略暂不可执行',
      subtitle: '质量门控未通过，不提供可执行动作',
      conclusion: strOrNull(conclusion),
      quality,
      confidence: numOrNull(latest.confidence),
      decisionAt: strOrNull(latest.decision_at),
      analysisRunId: numOrNull(latest.analysis_run_id),
      decisionSnapshotId: decisionSnap,
      targetRangeText: null,
      actionableCount,
      conditionalCount,
      riskBlockedCount,
      dataInsufficientCount,
      riskFlags,
    }
  }

  const kind = String(conclusion).toUpperCase() === 'NO_ACTION' ? 'NO_ACTION' : 'ACTIONABLE'
  return {
    kind,
    title: kind === 'NO_ACTION' ? '组合级无需操作 (NO_ACTION)' : '系统建议行动',
    subtitle: kind === 'NO_ACTION' ? '当前无需操作，持仓可继续观察' : `需处理 ${actionableCount} · 条件观察 ${conditionalCount}`,
    conclusion: strOrNull(conclusion),
    quality,
    confidence: numOrNull(latest.confidence),
    decisionAt: strOrNull(latest.decision_at),
    analysisRunId: numOrNull(latest.analysis_run_id),
    decisionSnapshotId: decisionSnap,
    // Authoritative target range is not currently exposed — never front-end derive.
    targetRangeText: null,
    actionableCount,
    conditionalCount,
    riskBlockedCount,
    dataInsufficientCount,
    riskFlags,
  }
}

function buildTimestamps(input: BuildHoldingsInput): V3HoldingsTimestampsVM {
  return {
    snapshotAt: strOrNull(input.snapshot?.snapshot_time),
    quoteAt: input.quotesAsOf,
    strategyAt: strOrNull(input.dashboard?.decisions?.latest?.decision_at)
      || strOrNull(input.dashboard?.strategy_analysis_at),
    session: input.session,
    sessionLabel: input.sessionLabel,
  }
}

function filterRows(rows: V3HoldingRowVM[], filter: V3HoldingsViewModel['filter']): V3HoldingRowVM[] {
  if (filter === 'actionable') return rows.filter((row) => isActionableStatus(row.judgment.status))
  if (filter === 'conditional') return rows.filter((row) => isConditionalStatus(row.judgment.status))
  if (filter === 'risk_data') {
    return rows.filter((row) => row.judgment.status === 'RISK_BLOCKED'
      || row.judgment.status === 'DATA_INSUFFICIENT'
      || row.quoteStatus === 'MISSING'
      || row.quoteStatus === 'STALE'
      || row.unresolved)
  }
  return rows
}

function sortRows(rows: V3HoldingRowVM[], sort: V3HoldingSort): V3HoldingRowVM[] {
  const copy = [...rows]
  if (sort === 'weight') return copy.sort((a, b) => (b.weight ?? -1) - (a.weight ?? -1))
  if (sort === 'pnl') return copy.sort((a, b) => (b.snapshotPnlRatio ?? -999) - (a.snapshotPnlRatio ?? -999))
  if (sort === 'change') return copy.sort((a, b) =>
    (b.quoteValuationEligible ? b.quote?.changePct ?? -999 : -999)
    - (a.quoteValuationEligible ? a.quote?.changePct ?? -999 : -999))
  if (sort === 'judgment') {
    return copy.sort((a, b) => judgmentRank(a.judgment.status) - judgmentRank(b.judgment.status)
      || (b.weight ?? 0) - (a.weight ?? 0))
  }
  return copy.sort((a, b) => judgmentRank(a.judgment.status) - judgmentRank(b.judgment.status)
    || (b.weight ?? 0) - (a.weight ?? 0))
}

export function buildHoldingsViewModel(input: BuildHoldingsInput): V3HoldingsViewModel {
  const snapshot = input.snapshot
  const originals: Holding[] = Array.isArray(snapshot?.holdings) ? snapshot!.holdings : []
  const state = input.accountState
  const key = (value: unknown) => String(value || '').toUpperCase().split('.')[0]
  const holdings: Holding[] = originals.map(holding => {
    const derived = state?.positions.find(item => key(item.code) === key(holding.canonical_code || holding.code))
    return derived ? { ...holding, qty: derived.qty, available_qty: derived.available_qty } : holding
  })
  for (const position of state?.positions || []) {
    if (!position.code || holdings.some(item => key(item.code) === key(position.code)) || position.qty === 0) continue
    holdings.push({ code: position.code, name: position.name, qty: position.qty, available_qty: position.available_qty,
      canonical_code: typeof position.canonical_code === 'string' ? position.canonical_code : null,
      resolution_status: position.canonical_code ? 'RESOLVED' : 'UNRESOLVED', asset_type: String(position.security_type || 'UNKNOWN') })
  }
  const portfolioCanonicals = new Map<string, number>()
  for (const holding of holdings) {
    const key = (strOrNull(holding.canonical_code) || strOrNull(holding.code) || '').split('.')[0]
    if (key) portfolioCanonicals.set(key, (portfolioCanonicals.get(key) || 0) + 1)
  }

  const rows: V3HoldingRowVM[] = holdings.map((holding) => {
    const code = strOrNull(holding.code) || ''
    const canonicalCode = strOrNull(holding.canonical_code)
    const unresolved = holding.resolution_status !== 'RESOLVED' || !canonicalCode
    const qty = numOrNull(holding.qty)
    const availableQty = numOrNull(holding.available_qty)
    const position = state?.positions.find(item => key(item.code) === key(code))
    // 净数量不变也可能已卖出再买回；旧服务缺少事件证明时保守撤下快照成本。
    const changed = Boolean(state && (position?.position_changed === true || Number(position?.qty_delta || 0) !== 0
      || position?.position_changed !== false && (numOrNull(state.account_derivation?.entry_count) ?? 1) > 0))
    const cost = position?.cost_status
      ? position.cost_status === 'KNOWN' ? numOrNull(position.current_cost) : null
      : changed ? null : numOrNull(holding.cost)
    const costSource: V3HoldingRowVM['costSource'] = cost === null ? 'none' : position?.cost_status === 'KNOWN' ? 'account' : 'snapshot'
    const snapshotMarketValue = changed ? null : numOrNull(holding.market_value)
    const snapshotWeight = changed ? null : numOrNull(holding.weight)
    const snapshotPnlRatio = changed ? null : numOrNull(holding.pnl)
    const snapshotPnlAmount = changed ? null : numOrNull(holding.pnl_amount)
    const quote = joinQuote(holding, input.quotes, portfolioCanonicals)
    const eligibility = quoteEligibility(quote, input.session,
      input.sessionTradingDate ?? input.dashboard?.market_session?.trading_date ?? input.dashboard?.trade_date ?? null)
    const usableQuote = !unresolved && eligibility.valuation && qty !== null && qty >= 0
    const liveMarketValueEstimate = usableQuote ? (quote!.last as number) * qty! : null
    const dayPnlEstimate = !changed && usableQuote && quote!.prevClose !== null && quote!.prevClose > 0 && quote!.last !== null
      ? (quote!.last - quote!.prevClose) * qty!
      : null
    const markedPnlEstimate = usableQuote && qty !== null && cost !== null && quote!.last !== null
      ? (quote!.last - cost) * qty!
      : null
    const judgment = resolveRowJudgment({ holding, dashboard: input.dashboard, snapshotId: input.snapshot?.id ?? null })
    const execution = buildExecution(qty, availableQty, judgment.status, !unresolved && eligibility.execution)
    if (judgment.strategyStatus === 'BLOCKED' && isActionableStatus(judgment.status)) {
      execution.blocking = true
      execution.text = [execution.text, '策略暂不可执行'].filter(Boolean).join('；')
    }
    const marketValueSource: V3HoldingRowVM['marketValueSource'] = liveMarketValueEstimate !== null
      ? 'quote_estimate'
      : snapshotMarketValue !== null
        ? 'snapshot'
        : 'none'
    const pnlSource: V3HoldingRowVM['pnlSource'] = snapshotPnlRatio !== null || snapshotPnlAmount !== null
      ? 'snapshot'
      : markedPnlEstimate !== null
        ? 'marked_estimate'
        : 'none'

    return {
      portfolioId: input.portfolioId,
      snapshotId: snapshot?.id ?? null,
      code,
      canonicalCode,
      name: strOrNull(holding.display_name) || strOrNull(holding.name),
      instrumentType: strOrNull(holding.asset_type),
      resolutionStatus: strOrNull(holding.resolution_status) || 'UNRESOLVED',
      qty,
      availableQty,
      cost,
      costSource,
      positionVersion: strOrNull(position?.position_version),
      snapshotMarketValue,
      snapshotWeight,
      snapshotPnlRatio,
      snapshotPnlAmount,
      quote: unresolved ? null : quote,
      quoteDisplayable: !unresolved && eligibility.displayable,
      quoteValuationEligible: !unresolved && eligibility.valuation,
      quoteExecutionEligible: !unresolved && eligibility.execution,
      liveMarketValueEstimate: unresolved ? null : liveMarketValueEstimate,
      dayPnlEstimate: unresolved ? null : dayPnlEstimate,
      markedPnlEstimate: unresolved ? null : markedPnlEstimate,
      marketValueSource,
      pnlSource,
      judgment,
      execution,
      quoteStatus: quoteStatusFrom(quote, unresolved),
      strategyStatus: judgment.strategyStatus,
      unresolved,
      canOpenDetail: !unresolved && Boolean(canonicalCode),
      weight: snapshotWeight,
      sparklineCode: !unresolved && canonicalCode ? canonicalCode : null,
    }
  })

  const sorted = sortRows(rows, input.sort)
  const filtered = filterRows(sorted, input.filter)
  const identityIncomplete = rows.some(row => row.unresolved && row.qty !== 0)
    || Boolean(snapshot?.identity_status && snapshot.identity_status !== 'RESOLVED')
  const decision = buildDecisionBar(input, rows)
  const decisionState = dashboardDecisionState(input.dashboard)
  if (decision.kind !== 'NO_PORTFOLIO' && decision.kind !== 'STALE_SNAPSHOT' && decision.kind !== 'MISSING'
    && ['WAITING', 'EXPIRED', 'DATA_GAP', 'INCOMPLETE', 'UNKNOWN'].includes(decisionState)) {
    decision.kind = decisionState as typeof decision.kind
    decision.title = decisionLabel(decisionState)
    decision.subtitle = decisionSummary(decisionState)
    decision.actionableCount = 0
  }

  return {
    hasPortfolio: input.hasPortfolio,
    portfolioId: input.portfolioId,
    portfolioName: input.portfolioName,
    snapshotId: snapshot?.id ?? null,
    snapshotTime: strOrNull(snapshot?.snapshot_time),
    identityIncomplete,
    emptySnapshot: Boolean(snapshot && holdings.length === 0),
    noSnapshot: Boolean(input.hasPortfolio && !snapshot),
    timestamps: buildTimestamps(input),
    summary: buildSummary(input, rows),
    decision,
    rows: filtered,
    filter: input.filter,
    sort: input.sort,
  }
}

export function holdingsPollIntervals(session: string | null): {
  sessionMs: number
  quotesMs: number | null
  strategyMs: number | null
} {
  switch (session) {
    case 'MORNING':
    case 'AFTERNOON':
    case 'OPEN_AUCTION':
    case 'CLOSE_AUCTION':
      return { sessionMs: 5_000, quotesMs: 6_000, strategyMs: 45_000 }
    case 'LUNCH_BREAK':
      return { sessionMs: 15_000, quotesMs: 30_000, strategyMs: 60_000 }
    case 'PRE_OPEN':
      return { sessionMs: 15_000, quotesMs: 30_000, strategyMs: 60_000 }
    case 'CLOSED':
    case 'NON_TRADING_DAY':
      return { sessionMs: 60_000, quotesMs: null, strategyMs: 120_000 }
    default:
      return { sessionMs: 15_000, quotesMs: 30_000, strategyMs: 60_000 }
  }
}

export { CONDITIONAL, ACTIONABLE }
