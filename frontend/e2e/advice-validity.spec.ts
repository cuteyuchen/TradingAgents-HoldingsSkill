import { expect, test } from '@playwright/test'
import type { DailyDashboard, Holding, PortfolioSnapshot } from '../src/api/types'
import { dashboardDecisionSource, dashboardDecisionState, holdingAdvice, normalizeDecisionState, snapshotFreshness, validityReasonText } from '../src/utils/decision'

const holding: Holding = { code: '600519', canonical_code: '600519.SH', resolution_status: 'RESOLVED' }
const snapshot = { id: 10, status: 'confirmed', snapshot_time: '2026-08-20T06:00:00Z', holdings: [holding] } as PortfolioSnapshot

function dashboard(latest: Record<string, unknown> | null): DailyDashboard {
  return {
    as_of: '2026-08-21T14:00:00+08:00',
    portfolio: { snapshot_id: 10, freshness: 'STALE' },
    decisions: { latest, final_action: 'NO_ACTION' },
    analysis: {},
  } as DailyDashboard
}

test('a confirmed snapshot does not imply fresh data', () => {
  expect(snapshotFreshness(snapshot, null)).toBe('UNKNOWN')
  expect(snapshotFreshness(snapshot, dashboard(null))).toBe('STALE')
  const differentSnapshot = dashboard(null)
  differentSnapshot.portfolio = { status: 'AVAILABLE', snapshot_id: 11, freshness: 'FRESH' }
  expect(snapshotFreshness(snapshot, differentSnapshot)).toBe('UNKNOWN')
})

test('missing or unknown conclusions do not imply no action', () => {
  expect(dashboardDecisionState(dashboard(null))).toBe('INCOMPLETE')
  expect(dashboardDecisionState(dashboard({ status: 'SUCCESS', analysis_run_id: 1 }))).toBe('INCOMPLETE')
  expect(normalizeDecisionState(undefined)).toBe('INCOMPLETE')
  expect(normalizeDecisionState('unrecognized')).toBe('INCOMPLETE')
})

test('explicit hold and conditional actions keep distinct meanings', () => {
  expect(normalizeDecisionState('hold')).toBe('NO_ACTION')
  expect(normalizeDecisionState('watch')).toBe('WAITING')
  expect(normalizeDecisionState('conditional_add')).toBe('WAITING')
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'watch', conclusion: 'NO_ACTION' }))).toBe('WAITING')
})

test('data gaps and incomplete analyses override a fallback hold', () => {
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'hold', quality: 'DATA_GAP' }))).toBe('DATA_GAP')
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'hold', status: 'RUNNING' }))).toBe('INCOMPLETE')
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'hold', decision_status: 'INCOMPLETE' }))).toBe('INCOMPLETE')
})

test('source snapshot changes and elapsed validity expire a suggestion', () => {
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'reduce', portfolio_snapshot_id: 9 }))).toBe('EXPIRED')
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'reduce', validity_status: 'EXPIRED' }))).toBe('EXPIRED')
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'reduce', valid_until: '2026-08-21T05:59:00' }))).toBe('EXPIRED')
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'reduce', valid_until: '2026-08-21T06:01:00Z' }))).toBe('ACTION')
})

test('account changes after the advice require review before acting', () => {
  const changed = dashboard({ portfolio_action: 'reduce', portfolio_snapshot_id: 10, validity_status: 'EXPIRED', validity_reason: 'PORTFOLIO_ACCOUNT_CHANGED' })
  expect(dashboardDecisionState(changed)).toBe('EXPIRED')
  expect(validityReasonText('PORTFOLIO_ACCOUNT_CHANGED')).toContain('需要复核')
  expect(validityReasonText('PORTFOLIO_SNAPSHOT_CHANGED')).toContain('旧快照')
})

test('unverified advice preserves same-snapshot meaning and makes validity explicit', () => {
  expect(dashboardDecisionState(dashboard({ portfolio_action: 'reduce', decision_status: 'ACTION', validity_status: 'UNVERIFIED' }))).toBe('UNKNOWN')
  const sameSnapshot = dashboard({ portfolio_action: 'reduce', decision_status: 'ACTION', validity_status: 'UNVERIFIED', portfolio_snapshot_id: 10, holding_actions: [{ code: '600519', action: 'reduce' }] })
  expect(dashboardDecisionState(sameSnapshot)).toBe('ACTION')
  expect(holdingAdvice(holding, snapshot, sameSnapshot).label).toBe('减仓 · 时效待核对')
  const historical = dashboard({ portfolio_action: 'reduce', holding_actions: [{ code: '600519', action: 'reduce' }] })
  expect(holdingAdvice(holding, snapshot, historical).label).toBe('建议时效待核对')
})

test('a missing holding recommendation never defaults to hold', () => {
  expect(holdingAdvice(holding, snapshot, dashboard(null)).label).toBe('分析未完成')
  const noHolding = dashboard({ portfolio_action: 'hold', portfolio_snapshot_id: 10, holding_actions: [] })
  expect(holdingAdvice(holding, snapshot, noHolding).label).toBe('分析未完成')
  const historicalHolding = { ...holding, extra: { action: 'Hold' } }
  expect(holdingAdvice(historicalHolding, snapshot, noHolding).label).toBe('建议时效待核对')
  expect(holdingAdvice(historicalHolding, snapshot, noHolding).detail).toContain('历史动作')
})

test('a matching current suggestion renders its explicit action and reason', () => {
  const current = dashboard({
    portfolio_action: 'reduce', decision_status: 'ACTION', validity_status: 'VALID', portfolio_snapshot_id: 10,
    holding_actions: [{ code: '600519', action: 'reduce', reason: '持仓集中度偏高' }],
  })
  expect(holdingAdvice(holding, snapshot, current)).toMatchObject({ label: '减仓', detail: '持仓集中度偏高' })
  current.decisions.latest.holding_actions[0] = { code: '600519', action: 'conditional_add', reason: '等待价格条件' }
  expect(holdingAdvice(holding, snapshot, current)).toMatchObject({ label: '等待条件', detail: '等待价格条件' })
  current.decisions.latest.holding_actions[0] = { code: '600519', action: 'hold', decision_status: 'INCOMPLETE' }
  expect(holdingAdvice(holding, snapshot, current).label).toBe('分析未完成')
})

test('the latest completed report wins over older decision memory by completion time', () => {
  const current = dashboard({
    analysis_run_id: 20, finished_at: '2026-08-21T04:35:00', decision_status: 'NO_ACTION',
    portfolio_snapshot_id: 10, validity_status: 'UNVERIFIED',
  })
  current.analysis.latest = {
    analysis_run_id: 19, status: 'SUCCESS', finished_at: '2026-08-21T13:50:00+08:00',
    decision_status: 'ACTION', portfolio_snapshot_id: 10, validity_status: 'UNVERIFIED',
    holding_actions: [{ code: '600519', action: 'reduce', reason: '最新报告减仓' }],
  }
  expect(dashboardDecisionSource(current).analysis_run_id).toBe(19)
  expect(dashboardDecisionState(current)).toBe('ACTION')
  expect(holdingAdvice(holding, snapshot, current).detail).toContain('最新报告减仓')
  current.analysis.latest.decision_status = 'DATA_GAP'
  expect(dashboardDecisionState(current)).toBe('DATA_GAP')
})

test('in-progress and older reports cannot replace completed decision memory', () => {
  const current = dashboard({
    analysis_run_id: 20, finished_at: '2026-08-21T05:50:00Z', decision_status: 'NO_ACTION',
    portfolio_snapshot_id: 10, validity_status: 'UNVERIFIED',
  })
  current.analysis.latest = {
    analysis_run_id: 21, status: 'RUNNING', finished_at: '2026-08-21T05:55:00Z', decision_status: 'ACTION',
  }
  expect(dashboardDecisionSource(current).analysis_run_id).toBe(20)
  current.analysis.latest.status = 'SUCCESS'
  current.analysis.latest.finished_at = '2026-08-21T05:40:00Z'
  expect(dashboardDecisionSource(current).analysis_run_id).toBe(20)
})
