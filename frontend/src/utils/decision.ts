import type { DailyDashboard, Holding, PortfolioSnapshot } from '../api/types'

export type DecisionState = 'ACTION' | 'NO_ACTION' | 'WAITING' | 'BLOCKED' | 'DATA_GAP' | 'INCOMPLETE' | 'EXPIRED' | 'UNKNOWN'

export function decisionLabel(state: string): string {
  return ({
    ACTION: '需要调整',
    NO_ACTION: '无需操作',
    WAITING: '等待条件',
    BLOCKED: '暂不可形成可靠行动',
    DATA_GAP: '数据不足',
    INCOMPLETE: '分析未完成',
    EXPIRED: '建议过期',
    UNKNOWN: '建议时效待核对',
  } as Record<string, string>)[state] || '分析未完成'
}

export function decisionSummary(state: DecisionState): string {
  return {
    ACTION: '组合层已给出调整建议，请核对具体持仓动作和执行前提。',
    NO_ACTION: '已完成的分析明确建议当前无需操作。',
    WAITING: '建议依赖的条件尚未满足，请先核对触发条件。',
    BLOCKED: '本次分析被门控阻断，暂不能形成可靠行动建议。',
    DATA_GAP: '可靠分析所需的数据不足，请补全数据后重新分析。',
    INCOMPLETE: '今天尚未完成分析，现有组合数据仍可查看。',
    EXPIRED: '这份建议已过期或对应旧持仓，请使用当前快照重新分析。',
    UNKNOWN: '无法核对这份建议的来源或有效期，请重新分析后再确认动作。',
  }[state]
}

export function validityReasonText(reason: string | null | undefined): string {
  return ({
    PORTFOLIO_ACCOUNT_CHANGED: '记录的实际成交或资金变化已改变当前账户，原建议需要复核后再执行。',
    PORTFOLIO_SNAPSHOT_CHANGED: '当前持仓快照已更新，原建议基于旧快照。',
    PORTFOLIO_SNAPSHOT_STALE: '持仓快照已超过时效，请更新后再行动。',
    VALIDITY_WINDOW_EXPIRED: '建议的有效期已经结束。',
    PORTFOLIO_LINEAGE_UNAVAILABLE: '无法核对建议对应的持仓来源。',
    VALIDITY_WINDOW_UNAVAILABLE: '无法核对建议的有效期。',
  } as Record<string, string>)[String(reason || '').toUpperCase()] || ''
}

export function normalizeDecisionState(action: unknown): DecisionState {
  const value = String(action || '').trim().toUpperCase()
  if (['ACTION', 'ADD', 'BUY', 'REDUCE', 'SELL', 'EXIT', 'REBALANCE', 'OVERWEIGHT', 'UNDERWEIGHT', 'ROTATE', 'NEW_POSITION', 'ADD_EXISTING'].includes(value)) return 'ACTION'
  if (['NO_ACTION', 'HOLD', 'HOLD_ONLY'].includes(value)) return 'NO_ACTION'
  if (['WAIT', 'WAITING', 'WATCH', 'WATCH_ONLY', 'ROTATION_WATCH', 'CONDITIONAL_ADD'].includes(value)) return 'WAITING'
  if (value === 'BLOCKED') return 'BLOCKED'
  if (['DATA_GAP', 'MISSING', 'INVALID', 'CONFLICT'].includes(value)) return 'DATA_GAP'
  if (['EXPIRED', 'STALE'].includes(value)) return 'EXPIRED'
  if (value === 'UNKNOWN') return 'UNKNOWN'
  return 'INCOMPLETE'
}

export function dashboardDecisionSource(dashboard: DailyDashboard | null): Record<string, any> {
  const decision = dashboard?.decisions?.latest
  const analysis = dashboard?.analysis?.latest
  const analysisStatus = String(analysis?.status || '').toUpperCase()
  const completedAnalysis = analysis && !['QUEUED', 'RUNNING', 'FAILED', 'CANCELLED', 'CANCELED', 'PENDING', 'INCOMPLETE'].includes(analysisStatus) ? analysis : null
  const completedAt = (value: unknown) => {
    const text = String(value || '')
    return Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/i.test(text) ? text : `${text}Z`)
  }
  const decisionFinished = completedAt(decision?.finished_at || decision?.decision_at)
  const analysisFinished = completedAt(completedAnalysis?.finished_at)
  if (completedAnalysis && (!decision || Number.isFinite(analysisFinished) && (!Number.isFinite(decisionFinished) || analysisFinished > decisionFinished))) return completedAnalysis
  return decision || completedAnalysis || {}
}

export function dashboardDecisionState(dashboard: DailyDashboard | null): DecisionState {
  const source = dashboardDecisionSource(dashboard)
  if (!Object.keys(source).length) return 'INCOMPLETE'
  const sourceSnapshot = source.portfolio_snapshot_id
  const currentSnapshot = dashboard?.portfolio?.snapshot_id
  if (sourceSnapshot != null && currentSnapshot != null && Number(sourceSnapshot) !== Number(currentSnapshot)) return 'EXPIRED'
  const validity = String(source.validity_status || '').toUpperCase()
  if (validity === 'EXPIRED') return 'EXPIRED'
  if (source.valid_until && dashboard?.as_of) {
    const expiryText = String(source.valid_until)
    const expiry = Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/i.test(expiryText) ? expiryText : `${expiryText}Z`)
    if (Number.isFinite(expiry) && expiry <= Date.parse(dashboard.as_of)) return 'EXPIRED'
  }
  if (source.decision_status) {
    const state = normalizeDecisionState(source.decision_status)
    if (['ACTION', 'NO_ACTION', 'WAITING'].includes(state) && validity === 'UNVERIFIED' && (sourceSnapshot == null || currentSnapshot == null)) return 'UNKNOWN'
    return state
  }
  const quality = String(source.quality || '').toUpperCase()
  if (quality === 'BLOCKED') return 'BLOCKED'
  if (['DATA_GAP', 'MISSING', 'INVALID', 'CONFLICT', 'D', 'F'].includes(quality)) return 'DATA_GAP'
  const status = String(source.status || '').toUpperCase()
  if (['QUEUED', 'RUNNING', 'FAILED', 'CANCELLED', 'CANCELED', 'PENDING', 'INCOMPLETE'].includes(status)) return 'INCOMPLETE'
  const freshness = String(source.freshness || '').toUpperCase()
  if (freshness === 'STALE' || freshness === 'EXPIRED') return 'EXPIRED'
  if (validity === 'UNVERIFIED' && (sourceSnapshot == null || currentSnapshot == null)) return 'UNKNOWN'
  return normalizeDecisionState(source.portfolio_action || source.conclusion || source.final_action || source.final_rating)
}

export function snapshotFreshness(snapshot: PortfolioSnapshot | null, dashboard: DailyDashboard | null): string {
  if (!snapshot) return 'MISSING'
  if (Number(dashboard?.portfolio?.snapshot_id) !== snapshot.id) return 'UNKNOWN'
  const value = String(dashboard?.portfolio?.freshness || '').toUpperCase()
  return ['FRESH', 'STALE', 'FROZEN', 'MISSING'].includes(value) ? value : 'UNKNOWN'
}

export function holdingAdvice(holding: Holding, snapshot: PortfolioSnapshot | null, dashboard: DailyDashboard | null): { label: string; detail: string; tone: 'info' | 'warning' | 'error' } {
  const state = dashboardDecisionState(dashboard)
  const source = dashboardDecisionSource(dashboard)
  const unavailable = (status: DecisionState, detail = decisionSummary(status)) => ({ label: decisionLabel(status), detail, tone: status === 'INCOMPLETE' ? 'info' as const : 'warning' as const })
  if (holding.resolution_status && holding.resolution_status !== 'RESOLVED') return unavailable('DATA_GAP', '证券身份尚未确认，不能匹配可靠的持仓建议。')
  if (!dashboard) return unavailable('UNKNOWN', '暂时无法读取最新分析状态，请刷新或查看完整分析。')
  if (['EXPIRED', 'DATA_GAP', 'BLOCKED', 'UNKNOWN'].includes(state)) return unavailable(state)
  const raw = source.holding_actions
  const code = String(holding.canonical_code || holding.code || '').toUpperCase().split('.')[0]
  const row = Array.isArray(raw) ? raw.find((item) => item && String(item.canonical_code || item.code || item.security_code || '').toUpperCase().split('.')[0] === code) : null
  if (!row) {
    const historicalAction = holding.extra?.holding_action || holding.extra?.action
    return historicalAction
      ? unavailable('UNKNOWN', `快照附带历史动作：${String(historicalAction)}；缺少可核对的当前分析，不能作为最新建议。`)
      : unavailable('INCOMPLETE', '当前分析尚未提供该标的的持仓建议，不能据此推断应继续持有。')
  }
  const sourceSnapshot = source.portfolio_snapshot_id
  if (sourceSnapshot == null || !snapshot) return unavailable('UNKNOWN')
  if (Number(sourceSnapshot) !== snapshot.id) return unavailable('EXPIRED')
  if (state === 'INCOMPLETE') return unavailable('INCOMPLETE')
  const action = row.action || row.recommended_action || row.holding_action
  const holdingState = normalizeDecisionState(row.decision_status || action)
  const labels: Record<string, string> = { BUY: '买入', ADD: '加仓', ADD_EXISTING: '加仓', REDUCE: '减仓', SELL: '卖出', EXIT: '清仓', ROTATE: '轮动', REBALANCE: '调整仓位', NEW_POSITION: '新建仓位', OVERWEIGHT: '增配', UNDERWEIGHT: '低配 / 减仓' }
  const flags = Array.isArray(row.risk_flags) ? row.risk_flags.join('、') : ''
  const unverified = source.validity_status === 'UNVERIFIED'
  const label = holdingState === 'ACTION' ? labels[String(action || '').toUpperCase()] || decisionLabel(holdingState) : decisionLabel(holdingState)
  return {
    label: unverified ? `${label} · 时效待核对` : label,
    detail: [row.reason || row.summary || row.rationale || decisionSummary(holdingState), flags, unverified ? '建议来自同一持仓快照，但有效期尚未核对，执行前请重新核对行情与条件。' : ''].filter(Boolean).join('；'),
    tone: unverified || ['EXPIRED', 'UNKNOWN', 'DATA_GAP', 'BLOCKED'].includes(holdingState) ? 'warning' : 'info',
  }
}
