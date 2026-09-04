import { test, expect, login, openPage, allowExpectedHttpError } from './fixtures'

test('Ownership returns a non-leaking not-found response for another user', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  allowExpectedHttpError(page, 403)
  allowExpectedHttpError(page, 404)
  const resources = await page.evaluate(async (portfolioId) => {
    const token = localStorage.getItem('advisor_v2_access_token')
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined
    const paths = {
      snapshots: `/api/v2/portfolios/${portfolioId}/snapshots`,
      reports: `/api/v2/analysis/runs?portfolio_id=${portfolioId}`,
      research: `/api/v3/research/backtests?portfolio_id=${portfolioId}`,
      shadow: `/api/v3/shadow/accounts?portfolio_id=${portfolioId}`,
    }
    const entries = await Promise.all(Object.entries(paths).map(async ([name, path]) => {
      const response = await fetch(path, { headers })
      let body: unknown = null
      try { body = await response.json() } catch { /* status-only response */ }
      return [name, { status: response.status, body }] as const
    }))
    return Object.fromEntries(entries)
  }, facts.portfolios.user_b)
  expect([403, 404]).toContain(resources.snapshots.status)
  expect(resources.research.status).toBe(404)
  expect(resources.shadow.status).toBe(404)
  expect(resources.reports.status).toBe(200)
  expect(resources.reports.body).toEqual([])

  await page.goto('/reports')
  await expect(page).toHaveURL(/\/analysis/)
  await expect(page.getByRole('heading', { name: '今日分析' })).toBeVisible()
  await expect(page.locator('body')).not.toContainText('User B private fixture')

  await page.goto('/shadow')
  await expect(page).toHaveURL(/\/simulation/)
  await expect(page.locator('.metric-grid.six .metric-tile').filter({ hasText: '样本天数' }).locator('strong')).toHaveText('—')

  await page.goto('/history')
  await expect(page).toHaveURL(/\/history/)
  await expect(page.getByRole('heading', { name: '历史表现', exact: true })).toBeVisible()
  await expect(page.locator('.metric-grid.six .metric-tile').filter({ hasText: '样本天数' }).locator('strong')).toHaveText('—')
})

test('Backend error renders ErrorState and retry recovers without clearing the session', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  allowExpectedHttpError(page, 500)
  let shouldFail = true
  await page.route('**/api/v3/portfolios/*/dashboard/today', async (route) => {
    if (!shouldFail) return route.continue()
    await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'forced acceptance failure' }) })
  })
  await page.goto('/holdings')
  await page.goto('/dashboard')
  await expect(page.getByRole('alert').filter({ hasText: '数据暂时加载失败' })).toBeVisible()
  shouldFail = false
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByRole('heading', { name: /今天/ })).toBeVisible()
  await expect(page.evaluate(() => Boolean(localStorage.getItem('advisor_v2_refresh_token')))).resolves.toBe(true)
  await page.unroute('**/api/v3/portfolios/*/dashboard/today')
})

test('SPA deep links load and survive refresh', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const routes = [
    ['/dashboard', /今天/],
    ['/reports', '今日分析'],
    ['/shadow', '模拟跟随'],
    ['/research', '历史'],
    ['/system', '设置'],
    ['/settings', '设置'],
  ] as const
  for (const [route, heading] of routes) {
    await openPage(page, route, heading)
    await page.reload()
    const aliases: Record<string, string> = { '/reports': '/analysis', '/shadow': '/simulation', '/research': '/history', '/system': '/settings' }
    await expect(page).toHaveURL(new RegExp((aliases[route] || route).replaceAll('/', '\\/')))
    const headingLocator = typeof heading === 'string'
      ? page.getByRole('heading', { name: heading, exact: true })
      : page.getByRole('heading', { name: heading })
    await expect(headingLocator).toBeVisible()
  }
})
