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
  await expect(page.getByRole('heading', { name: '分析报告' })).toBeVisible()
  await expect(page.locator('body')).not.toContainText('User B private fixture')
})

test('Backend error renders ErrorState and retry recovers without clearing the session', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  allowExpectedHttpError(page, 500)
  let failOnce = true
  await page.route('**/api/v3/portfolios/*/dashboard/today', async (route) => {
    if (!failOnce) return route.continue()
    failOnce = false
    await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'forced acceptance failure' }) })
  })
  await page.goto('/dashboard')
  await expect(page.getByRole('alert').filter({ hasText: '读取失败' })).toBeVisible()
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByRole('heading', { name: '今日操作台' })).toBeVisible()
  await expect(page.evaluate(() => Boolean(localStorage.getItem('advisor_v2_refresh_token')))).resolves.toBe(true)
  await page.unroute('**/api/v3/portfolios/*/dashboard/today')
})

test('SPA deep links load and survive refresh', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const routes = [
    ['/dashboard', '今日操作台'],
    ['/reports', '分析报告'],
    ['/shadow', 'Shadow 验证'],
    ['/research', '历史回放与参数校准'],
    ['/system', '系统运维'],
    ['/settings', '系统设置'],
  ] as const
  for (const [route, heading] of routes) {
    await openPage(page, route, heading)
    await page.reload()
    await expect(page).toHaveURL(new RegExp(route.replaceAll('/', '\\/')))
    await expect(page.getByRole('heading', { name: heading })).toBeVisible()
  }
})
