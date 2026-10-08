/**
 * Holdings pure formatters and adapters.
 * Frozen status enum + deterministic action mapping.
 * Missing never becomes 0. Frontend never invents BUY/SELL/REDUCE/ADD.
 */
import type { DailyDashboard, Holding, PortfolioSnapshot } from '../../api/types'
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
  const latest = dashboard?.decisions?.latest
  if (!latest) return null
  return strOrNull(latest.conclusion)
}

function decisionQuality(dashboard: DailyDashboard | null): string | null {
  return strOrNull(dashboard?.decisions?.latest?.quality)
}

function decisionSnapshotId(dashboard: DailyDashboard | null): number | null {
  return numOrNull(dashboard?.decisions?.latest?.portfolio_snapshot_id)
}

function decisionAt(dashboard: DailyDashboard | null): string | null {
  return strOrNull(dashboard?.decisions?.latest?.decision_at)
}

function portfolioHoldingAction(dashboard: DailyDashboard | null, code: string, canonicalCode: string | null): string | null {
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
  const latest = dashboard?.decisions?.latest
  if (!latest) return { action: null, trigger: null, reason: null }
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
  const latest = dashboard?.decisions?.latest || null
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
      secondary = '组合级 NO_ACTION'
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
  const quality = String(item.quality || '').toUpperCase()
  const status = String(item.status || '').toUpperCase()
  const flags = Array.isArray(item.quality_flags) ? item.quality_flags.map(String) : []
  let quoteStatus: V3QuoteStatus = 'MISSING'
  if (last !== null) {
    if (status === 'UNAVAILABLE' || quality === 'MISSING' || quality === 'F') quoteStatus = 'MISSING'
    else if (status === 'STALE' || quality === 'STALE' || flags.includes('STALE')) quoteStatus = 'STALE'
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
    dataBasis: strOrNull(item.data_basis),
    quality: quality || 'MISSING',
    qualityFlags: flags,
    status: quoteStatus,
    direction: directionFromChange(change, changePct),
  }
}

function buildExecution(qty: number | null, availableQty: number | null, judgmentStatus: V3HoldingStatus): {
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
  if (available !== null && available <= 0 && isActionableStatus(judgmentStatus)) {
    text = `执行约束：当前可用 ${available}`
    blocking = true
  } else if (unavailable !== null && unavailable > 0) {
    text = `当前不可用 ${unavailable} · 由快照数量差计算`
  }
  return { availableQty: available, unavailableQty: unavailable, text, blocking }
}

function buildSummary(input: BuildHoldingsInput, rows: V3HoldingRowVM[]): V3HoldingsSummaryVM | null {
  const { snapshot, dashboard, quoteCoverage } = input
  if (!snapshot) return null
  const portfolio = dashboard?.portfolio
  const totalAssets = numOrNull(snapshot.total_assets) ?? numOrNull(portfolio?.total_assets)
  const cash = numOrNull(snapshot.broker_available_cash) ?? numOrNull(portfolio?.spendable_cash)
  const snapshotMv = numOrNull(snapshot.total_market_value)
  const resolvedRows = rows.filter((row) => !row.unresolved)
  const quotedRows = resolvedRows.filter((row) => row.quote && row.quote.status !== 'MISSING')
  let marketValue: number | null = null
  let marketValueSource: V3HoldingsSummaryVM['marketValueSource'] = 'none'
  if (quotedRows.length && resolvedRows.length && quotedRows.length === resolvedRows.length) {
    marketValue = quotedRows.reduce((sum, row) => sum + (row.liveMarketValueEstimate || 0), 0)
    marketValueSource = 'quote_estimate'
  } else if (snapshotMv !== null) {
    marketValue = snapshotMv
    marketValueSource = 'snapshot'
  }

  const snapshotPnlComplete = resolvedRows.length > 0
    && resolvedRows.every((row) => row.snapshotPnlAmount !== null)
  const floatingPnlSnapshot = snapshotPnlComplete
    ? resolvedRows.reduce((sum, row) => sum + (row.snapshotPnlAmount || 0), 0)
    : null

  const dayCovered = resolvedRows.filter((row) => row.dayPnlEstimate !== null)
  const dayPnlAvailable = resolvedRows.length > 0 && dayCovered.length === resolvedRows.length
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
    ? (actionable ? `需处理 ${actionable}` : '无需处理')
    : '策略不可用'

  return {
    totalAssets,
    marketValue,
    marketValueSource,
    quoteCoverage,
    spendableCash: cash,
    grossExposure: numOrNull(portfolio?.gross_exposure),
    dayPnlEstimate,
    dayPnlAvailable,
    floatingPnlSnapshot,
    floatingPnlAvailable: floatingPnlSnapshot !== null,
    positionCount: numOrNull(portfolio?.position_count) ?? rows.length,
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
  const latest = dashboard?.decisions?.latest || null
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
    title: kind === 'NO_ACTION' ? '组合级 NO_ACTION' : '系统建议行动',
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
  if (sort === 'change') return copy.sort((a, b) => (b.quote?.changePct ?? -999) - (a.quote?.changePct ?? -999))
  if (sort === 'judgment') {
    return copy.sort((a, b) => judgmentRank(a.judgment.status) - judgmentRank(b.judgment.status)
      || (b.weight ?? 0) - (a.weight ?? 0))
  }
  return copy.sort((a, b) => judgmentRank(a.judgment.status) - judgmentRank(b.judgment.status)
    || (b.weight ?? 0) - (a.weight ?? 0))
}

export function buildHoldingsViewModel(input: BuildHoldingsInput): V3HoldingsViewModel {
  const snapshot = input.snapshot
  const holdings: Holding[] = Array.isArray(snapshot?.holdings) ? snapshot!.holdings : []
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
    const cost = numOrNull(holding.cost)
    const snapshotMarketValue = numOrNull(holding.market_value)
    const snapshotWeight = numOrNull(holding.weight)
    const snapshotPnlRatio = numOrNull(holding.pnl)
    const snapshotPnlAmount = numOrNull(holding.pnl_amount)
    const quote = joinQuote(holding, input.quotes, portfolioCanonicals)
    const usableQuote = Boolean(quote && quote.last !== null && !unresolved)
    const liveMarketValueEstimate = usableQuote && qty !== null ? (quote!.last as number) * qty : null
    const dayPnlEstimate = usableQuote && qty !== null && quote!.prevClose !== null && quote!.last !== null
      ? (quote!.last - quote!.prevClose) * qty
      : null
    const markedPnlEstimate = usableQuote && qty !== null && cost !== null && quote!.last !== null
      ? (quote!.last - cost) * qty
      : null
    const judgment = resolveRowJudgment({ holding, dashboard: input.dashboard, snapshotId: input.snapshot?.id ?? null })
    const execution = buildExecution(qty, availableQty, judgment.status)
    const marketValueSource: V3HoldingRowVM['marketValueSource'] = usableQuote
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
      snapshotMarketValue,
      snapshotWeight,
      snapshotPnlRatio,
      snapshotPnlAmount,
      quote: unresolved ? null : quote,
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
  const identityIncomplete = Boolean(snapshot?.identity_status && snapshot.identity_status !== 'RESOLVED')

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
    decision: buildDecisionBar(input, rows),
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
