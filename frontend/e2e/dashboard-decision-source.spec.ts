import { test, expect } from './fixtures'

test('dashboard conclusion, evidence, validity and navigation use the same newest report', async ({ acceptancePage: page }) => {
  await page.addInitScript(() => localStorage.setItem('advisor_v2_access_token', 'dashboard-source-fixture'))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown = {}
    if (path === '/api/v2/portfolios') body = [{ id: 1, name: 'Source Fixture', is_default: true, latest_snapshot_id: 10 }]
    else if (path.endsWith('/providers') || path.endsWith('/profiles') || path === '/api/v2/analysis/runs') body = []
    else if (path.endsWith('/dashboard/today')) body = {
      as_of: '2026-08-21T14:00:00+08:00', trade_date: '2026-08-21', market_open: true,
      portfolio: { snapshot_id: 10 }, market: {}, candidates: {}, data_health: {},
      decisions: { status: 'AVAILABLE', latest: {
        analysis_run_id: 1, portfolio_snapshot_id: 10, decision_status: 'EXPIRED', validity_status: 'EXPIRED', quality: 'BLOCKED',
        finished_at: '2026-08-21T05:00:00Z', portfolio_conclusion: '旧报告的调整建议', reasons: ['旧报告依据'],
      } },
      analysis: { status: 'AVAILABLE', latest: {
        analysis_run_id: 2, portfolio_snapshot_id: 10, status: 'SUCCESS', decision_status: 'WAITING', validity_status: 'UNVERIFIED', quality: 'VALID',
        finished_at: '2026-08-21T06:00:00Z', summary: '新报告要求等待条件满足', reasons: ['新报告依据'],
      } },
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })

  await page.goto('/dashboard')
  const card = page.getByTestId('v3-decision-hero')
  await expect(card.getByRole('heading', { name: '等待条件', exact: true })).toBeVisible()
  await expect(card).toContainText('新报告要求等待条件满足')
  await expect(card).toContainText('新报告依据')
  await expect(card).toContainText('建议时效待核对')
  await expect(card).not.toContainText('旧报告')
  await expect(card).not.toContainText('建议已过期')
  await expect(card).not.toContainText('BLOCKED')
  await card.getByRole('button', { name: '查看完整分析', exact: true }).click()
  await expect(page).toHaveURL(/\/analysis\?portfolio=1&run=2$/)
})
