import { test, expect } from './fixtures'

test('leaving analysis prevents follow-up requests from a delayed report list', async ({ acceptancePage: page }) => {
  let releaseList!: () => void
  let markListStarted!: () => void
  const listGate = new Promise<void>((resolve) => { releaseList = resolve })
  const listStarted = new Promise<void>((resolve) => { markListStarted = resolve })
  let detailRequests = 0
  await page.addInitScript(() => localStorage.setItem('advisor_v2_access_token', 'analysis-lifecycle-fixture'))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v2/auth/logout') {
      await route.fulfill({ status: 204 })
      return
    }
    let body: unknown = {}
    if (path === '/api/v2/portfolios') body = [{ id: 1, name: 'Lifecycle Fixture', is_default: true }]
    else if (path === '/api/v3/fuyao/market-brief') body = { brief: { major_indices: [], industry: {}, sentiment: {} } }
    else if (path === '/api/v2/analysis/runs') {
      markListStarted()
      await listGate
      body = [{ id: 8, portfolio_snapshot_id: 1, created_at: '2026-08-21T06:00:00Z' }]
    } else if (path === '/api/v2/analysis/runs/8') {
      detailRequests += 1
      body = { id: 8, portfolio_snapshot_id: 1, structured_result: {}, markdown: '' }
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => undefined)
  })

  try {
    await page.goto('/analysis?portfolio=1')
    await listStarted
    await page.getByRole('button', { name: '退出登录', exact: true }).click()
    await expect(page).toHaveURL(/\/login/)
    releaseList()
    await page.waitForLoadState('networkidle')
    expect(detailRequests).toBe(0)
    await expect(page).toHaveURL(/\/login/)
  } finally {
    releaseList()
  }
})
