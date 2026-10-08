import { test, expect, login } from './fixtures'

test.describe('Fuyao context and degradation', () => {
  test('missing key stays explicit while context and live marks remain missing-aware', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)

    await page.goto('/settings')
    const fuyaoCard = page.getByRole('heading', { name: '同花顺金融数据', exact: true }).locator('xpath=ancestor::section[contains(@class, "section-card")]')
    await expect(page.getByRole('heading', { name: '同花顺金融数据', exact: true })).toBeVisible()
    await expect(fuyaoCard).toContainText('未配置')
    await expect(fuyaoCard).toContainText('行情')

    await page.goto(`/dashboard?portfolio=${facts.portfolios.action}`)
    // V3 Dashboard uses MARKET-1 canonical market surface, not Fuyao brief.
    await expect(page.getByTestId('v3-systemic-risk-panel')).toBeVisible()
    await expect(page.getByTestId('v3-market-breadth')).toBeVisible()
    await expect(page.getByTestId('v3-limit-up')).toBeVisible()
    await expect(page.getByTestId('v3-limit-down')).toBeVisible()
    await expect(page.getByTestId('v3-typical-stock')).toContainText('典型个股表现')
    await expect(page.getByTestId('fuyao-market-context')).toHaveCount(0)

    await page.goto(`/holdings?portfolio=${facts.portfolios.action}`)
    // V3 Holdings uses MARKET-2 unified quotes as the primary quote surface.
    await expect(page.getByTestId('v3-holdings')).toBeVisible()
    await expect(page.getByTestId('v3-holdings-table')).toBeVisible()
    await expect(page.getByTestId('v3-holdings-quote-ts')).toBeVisible()

    const firstResolved = page.locator('[data-testid^="v3-holding-row-"]').first()
    await expect(firstResolved).toBeVisible()
    // Missing-aware: quote cells may be —, but never a fabricated Fuyao contribution column.
    await expect(page.locator('.holdings-table')).toHaveCount(0)
    await expect(page.getByText('实时价格', { exact: true })).toHaveCount(0)
  })

  test('permission and upstream states are distinguished in settings', async ({ acceptancePage: page, facts }) => {
    let mode: 'permission' | 'degraded' = 'permission'
    await page.route('**/api/v3/fuyao/status*', (route) => {
      const status = mode === 'permission' ? '未授权' : '上游异常'
      const capabilities = Object.fromEntries(['quotes', 'calendar', 'historical', 'financials', 'valuation', 'index', 'fund', 'special_data'].map((key) => [key, { status }]))
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ provider: 'fuyao', configured: true, connection_status: status, capabilities }) })
    })
    await login(page, facts.users.a)
    await page.goto('/settings')
    const heading = page.getByRole('heading', { name: '同花顺金融数据', exact: true })
    await expect(heading).toBeVisible()
    const card = heading.locator('xpath=ancestor::section[contains(@class, "section-card")]')
    await expect(card).toContainText('未授权')

    mode = 'degraded'
    await page.reload()
    await expect(card).toContainText('上游异常')
  })
})
