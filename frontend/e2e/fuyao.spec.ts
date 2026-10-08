import { test, captureScreenshot, expect, login } from './fixtures'

test.describe('Fuyao context and degradation', () => {
  test('settings saves a key, hides it on reload, and restores environment configuration', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await page.goto('/settings')
    // Password fields have no implicit textbox role.
    const keyInput = page.locator('input[aria-label="同花顺金融数据 API Key"]')
    await expect(keyInput).toHaveAttribute('type', 'password')
    await expect(keyInput).toHaveValue('')
    await expect(page.getByRole('button', { name: '保存 API Key', exact: true })).toBeDisabled()
    const secret = 'acceptance-fuyao-fixture-key'
    try {
      await keyInput.fill(secret)
      const saved = page.waitForResponse((response) => response.url().endsWith('/api/v3/fuyao/config') && response.request().method() === 'PUT')
      await page.getByRole('button', { name: '保存 API Key', exact: true }).click()
      const response = await saved
      expect(response.status()).toBe(200)
      expect(await response.text()).not.toContain(secret)
      await expect(page.getByText('当前来源：系统设置')).toBeVisible()
      await expect(keyInput).toHaveValue('')
      await page.reload()
      await expect(keyInput).toHaveValue('')
      await expect(page.getByText('当前来源：系统设置')).toBeVisible()
      expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(secret)
      await captureScreenshot(page, 'settings-fuyao-configured-light')
      page.once('dialog', (dialog) => dialog.accept())
      await page.getByRole('button', { name: '恢复环境配置', exact: true }).click()
      await expect(page.getByRole('button', { name: '恢复环境配置', exact: true })).toHaveCount(0)
    } finally {
      const token = await page.evaluate(() => localStorage.getItem('advisor_v2_access_token'))
      if (token) await page.request.delete('/api/v3/fuyao/config', { headers: { Authorization: `Bearer ${token}` } })
    }
  })

  test('ordinary users can see status but cannot replace shared credentials', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.b)
    await page.goto('/settings')
    await expect(page.locator('input[aria-label="同花顺金融数据 API Key"]')).toBeDisabled()
    await expect(page.getByRole('button', { name: '保存 API Key', exact: true })).toBeDisabled()
    await expect(page.getByText('共享行情密钥由实例管理员（首个注册账户）配置。')).toBeVisible()
  })

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
