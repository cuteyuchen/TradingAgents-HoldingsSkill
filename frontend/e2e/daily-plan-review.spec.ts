import { expect, test } from './fixtures'

const plan = {
  version: 'daily-action-plan-v1',
  analysis_run_id: 7,
  decision_status: 'ACTION',
  reason_codes: ['SELL_DEPENDENCY_UNFILLED'],
  as_of: '2026-08-21T14:00:00+08:00',
  account_version: 'portfolio-account-v1:10:fixture',
  summary: { ACTION: 1, NO_ACTION: 0, WAITING: 1, DATA_GAP: 0, INCOMPLETE: 0, EXPIRED: 0 },
  actions: [
    {
      plan_id: 21, code: '600000', name: '示例持仓', target_type: 'HOLDING', action: 'reduce', side: 'SELL',
      decision_status: 'ACTION', reason_codes: [], planned_quantity: 300, filled_quantity: 100,
      remaining_quantity: 200, executable_quantity: 200, fill_entry_ids: [91], depends_on_plan_ids: [],
      condition: null, expires_at: '2026-08-21T15:00:00+08:00',
    },
    {
      plan_id: 22, code: '600001', name: '待加仓标的', target_type: 'HOLDING', action: 'conditional_add', side: 'BUY',
      decision_status: 'WAITING', reason_codes: ['SELL_DEPENDENCY_UNFILLED'], planned_quantity: 200,
      filled_quantity: 0, remaining_quantity: 200, executable_quantity: 0, fill_entry_ids: [], depends_on_plan_ids: [21],
      condition: { condition: 'price_below', metric: 'price', operator: 'LT', threshold: 10.5 },
      expires_at: '2026-08-21T15:00:00+08:00',
    },
  ],
  comparisons: [{ label: '继续持有现金', reason: '等待条件更明确前不新增风险。' }],
}

const review = {
  version: 'performance-p4-v1', as_of: '2026-08-21T15:40:00+08:00',
  trade_date: '2026-08-21', previous_trade_date: '2026-08-20',
  account_return: {
    status: 'COMPLETE', net_pnl: 1234.5, return_rate: 0.0123, realized_pnl: 480.0, unrealized_pnl: 754.5,
    fees: 12.5, taxes: 6.0, dividends: 0, cash_in: 0, cash_out: 0, reason_codes: [],
  },
  recommendation_effect: { items: [], count: 0, basis: 'MARKET_EFFECT_NOT_ACCOUNT_PNL' },
  simulation_comparison: { items: [], basis: 'HYPOTHETICAL_LONG_VS_CASH_BEFORE_COSTS' },
  day_comparison: {
    today: [{ code: '600000', action: 'reduce', quantity: 200 }],
    yesterday: [{ code: '600000', action: 'buy', quantity: 300 }],
    changes: [{ code: '600000', action: 'reduce', previous_action: 'buy', quantity: 200, previous_quantity: 300, quantity_change: -100 }],
  },
  review_dimensions: { items: [], traceable_does_not_mean_correct: true, conditional_untriggered_is_not_failure: true },
  weekly_review: { start_date: '2026-08-15', end_date: '2026-08-21', repeated_issues: [], dedupe_basis: 'trade_date+security+dimension' },
  learning: {
    version: 'learning-p4-v1', status_counts: { pending: 0, reference: 1, verified: 0, disabled: 0 },
    validation_basis: 'FORWARD_INDEPENDENT_GROSS_LONG_VS_CASH_SIMULATION', model_weights_changed: false,
    strategy_changes_require_governance: true,
    hypotheses: [{
      id: 'hypothesis-fixture', hypothesis_key: 'avoid-adding-into-weakening-sector', revision: 2,
      status: 'reference', statement: '板块同步转弱时的加仓建议需要补查相对强弱',
      scope: { market_regime: 'NEUTRAL', security_type: 'STOCK', action: 'add', horizon: 5, roles: ['trader'] },
      support: { sample_count: 6 }, counterexamples: [], validation: { status: 'independent' }, weight: 0.8,
      extraction_cutoff: '2026-08-01T07:00:00', available_at: '2026-08-02T07:00:00', reason: '', version: 'learning-p4-v1',
    }],
  },
}

async function mockDashboardApi(page: import('@playwright/test').Page, writes: string[]) {
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() !== 'GET') writes.push(`${request.method()} ${path}`)
    let body: unknown = request.method() === 'GET' ? [] : {}
    if (path === '/api/v2/portfolios') body = [{ id: 1, name: '复盘 Fixture', is_default: true, latest_snapshot_id: 10 }]
    else if (path.endsWith('/dashboard/today')) body = {
      as_of: '2026-08-21T14:00:00+08:00', trade_date: '2026-08-21', market_open: true,
      portfolio: { snapshot_id: 10, account_version: 'portfolio-account-v1:10:fixture' },
      market: {}, candidates: {}, data_health: {},
      decisions: { latest: {
        analysis_run_id: 7, portfolio_snapshot_id: 10, decision_status: 'ACTION',
        validity_status: 'VALID', quality: 'VALID', finished_at: '2026-08-21T06:00:00Z',
        portfolio_conclusion: '先完成减仓，再复核加仓条件。', reasons: ['板块转弱'],
      } },
      analysis: { latest: { analysis_run_id: 7, status: 'SUCCESS', finished_at: '2026-08-21T06:00:00Z' } },
    }
    else if (path === '/api/v3/triggers/daily-plan') body = plan
    else if (path === '/api/v3/triggers/plans/21/recheck') body = { decision_status: 'ACTION', remaining_quantity: 200 }
    else if (path === '/api/v3/triggers/plans/21/fills') body = { plan_id: 21, fill_entry_ids: [91], review_required: true }
    else if (path === '/api/v3/portfolios/1/ledger') body = [{
      id: 91, portfolio_id: 1, entry_type: 'TRADE', security_code: '600000', side: 'SELL', quantity: 100,
      price: 10.2, executed_at: '2026-08-21T10:30:00+08:00', status: 'CONFIRMED', source: 'MANUAL',
    }]
    else if (path === '/api/v3/portfolios/1/memory/performance') body = review
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
}

test('每日行动计划展示条件、剩余数量并支持复核与关联成交', async ({ acceptancePage: page }) => {
  const writes: string[] = []
  await page.addInitScript(() => localStorage.setItem('advisor_v2_access_token', 'daily-plan-fixture'))
  await mockDashboardApi(page, writes)
  await page.goto('/dashboard')

  const card = page.locator('.section-card').filter({ has: page.getByRole('heading', { name: '今日行动计划', exact: true }) })
  await expect(card).toBeVisible()
  await expect(card).toContainText('本次计划：需要调整 1 项 · 等待条件 1 项')

  const reduce = card.locator('.plan-row').filter({ hasText: '600000' })
  await expect(reduce).toContainText('计划 300 股')
  await expect(reduce).toContainText('已成交 100 股')
  await expect(reduce).toContainText('剩余 200 股')
  await expect(reduce).toContainText('当前可执行 200 股')
  await expect(reduce).toContainText('有效至')
  await expect(reduce).toContainText('减仓 · 需要调整')

  const add = card.locator('.plan-row').filter({ hasText: '600001' })
  await expect(add).toContainText('先完成关联卖出并确认可用资金，再复核买入。')
  await expect(add).toContainText('等待条件')

  await reduce.getByRole('button', { name: '复核剩余计划' }).click()
  await expect.poll(() => writes.includes('POST /api/v3/triggers/plans/21/recheck')).toBe(true)

  await reduce.locator('.n-base-selection').click()
  await page.locator('.n-base-select-option').first().click()
  await reduce.getByRole('button', { name: '关联成交' }).click()
  await expect.poll(() => writes.includes('POST /api/v3/triggers/plans/21/fills')).toBe(true)
})

test('复盘面板区分真实账户、昨日今日变化与经验状态', async ({ acceptancePage: page }) => {
  const writes: string[] = []
  await page.addInitScript(() => localStorage.setItem('advisor_v2_access_token', 'review-panel-fixture'))
  await mockDashboardApi(page, writes)
  await page.goto('/dashboard')

  const compact = page.locator('.section-card').filter({ has: page.getByRole('heading', { name: '昨日执行与今日变化', exact: true }) })
  await expect(compact).toBeVisible()
  await expect(compact).toContainText('2026-08-20 → 2026-08-21')
  await expect(compact).toContainText('当日账户净收益')
  await expect(compact).toContainText('买入 → 减仓')
  await expect(compact).toContainText('300 → 200 股')
  await expect(compact).not.toContainText('自学习经验')

  await page.goto('/history')
  const full = page.locator('.section-card').filter({ has: page.getByRole('heading', { name: '真实账户与建议复盘', exact: true }) })
  await expect(full).toBeVisible()
  await expect(full).toContainText('自学习经验')
  const hypothesis = full.locator('.learning-item')
  await expect(hypothesis).toContainText('板块同步转弱时的加仓建议需要补查相对强弱')
  await expect(hypothesis).toContainText('可参考')
  await expect(hypothesis).toContainText('市场 NEUTRAL')
  await expect(full).toContainText('经验不会自动修改模型权重和仓位上限')

  await full.getByRole('button', { name: '评估到期建议' }).click()
  await expect.poll(() => writes.includes('POST /api/v3/portfolios/1/memory/learning/refresh')).toBe(true)
})
