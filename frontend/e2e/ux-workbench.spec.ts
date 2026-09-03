import { test, expect, login, selectPortfolio } from './fixtures'

test('Single-user navigation exposes only the workbench pages and preserves legacy aliases', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)

  const nav = page.locator('nav.top-nav')
  await expect(nav.getByRole('link')).toHaveCount(5)
  await expect(nav).toContainText('首页')
  await expect(nav).toContainText('持仓')
  await expect(nav).toContainText('分析')
  await expect(nav).toContainText('模拟')
  await expect(nav).toContainText('历史')
  await expect(nav).not.toContainText('治理')
  await expect(nav).not.toContainText('系统')
  await expect(page.locator('.icon-link[aria-label="设置"]')).toBeVisible()

  const aliases = [
    ['/reports?portfolio=' + facts.portfolios.action + '&run=' + facts.runs.action, '/analysis', '今日分析'],
    ['/shadow?portfolio=' + facts.portfolios.action, '/simulation', '模拟跟随'],
    ['/research', '/history', '历史'],
    ['/governance', '/settings', '设置'],
    ['/system', '/settings', '设置'],
  ] as const
  for (const [alias, canonical, heading] of aliases) {
    await page.goto(alias)
    await expect(page).toHaveURL(new RegExp(canonical.replaceAll('/', '\\/')))
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
  }

  await page.goto('/upload?portfolio=' + facts.portfolios.action)
  await expect(page).toHaveURL(/\/holdings\?/)
  await expect(page.getByRole('heading', { name: '我的持仓' })).toBeVisible()
  await expect(page.locator('.n-drawer').last()).toBeVisible()
  await page.getByRole('button', { name: '关闭更新持仓' }).click()
})

test('Shell keeps email private and hides the portfolio selector for one portfolio', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.b)
  await expect(page.locator('header.topbar')).not.toContainText(facts.users.b.email)
  await expect(page.locator('header.topbar')).not.toContainText('当前用户')
  await expect(page.locator('.global-portfolio-select')).toHaveCount(0)
  await expect(page.locator('nav.top-nav')).toBeVisible()
})

test('Shell system status follows authoritative readiness instead of portfolio existence', async ({ acceptancePage: page, facts }) => {
  let readinessRequests = 0
  await page.route('**/api/v3/system/health', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'OK', components: {}, as_of: '2026-08-21T06:00:00Z' }),
  }))
  await page.route('**/api/v3/system/live-validation-readiness', (route) => {
    readinessRequests += 1
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'NOT_READY',
        ready: false,
        blockers: [{ key: 'market_provider', reason: 'quote_provider_not_observed' }],
        warnings: [],
        checks: { market_provider: { status: 'BLOCKED', reason: 'quote_provider_not_observed' } },
        evaluated_at: '2026-08-21T06:00:00Z',
      }),
    })
  })

  await login(page, facts.users.a)
  await expect(page.locator('.system-status-button')).toContainText('需要配置')
  await expect(page.locator('.system-status-button')).not.toContainText('正常')
  expect(readinessRequests).toBeGreaterThan(0)
})

test('Shell shows verification pending instead of data-limited when Fuyao is configured', async ({ acceptancePage: page, facts }) => {
  await page.route('**/api/v3/system/health', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'OK', components: {}, as_of: '2026-08-21T06:00:00Z' }),
  }))
  await page.route('**/api/v3/system/live-validation-readiness', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'NOT_READY',
      ready: false,
      blockers: [
        { key: 'portfolio_snapshot', reason: 'confirmed_portfolio_snapshot_missing' },
        { key: 'analysis_smoke', reason: 'successful_analysis_run_not_observed' },
      ],
      warnings: [],
      checks: {
        market_provider: { status: 'OK' },
        quote_pipeline: { status: 'OK' },
        market_refresh: { status: 'OK' },
        portfolio_snapshot: { status: 'BLOCKED', reason: 'confirmed_portfolio_snapshot_missing' },
      },
      evaluated_at: '2026-08-21T06:00:00Z',
    }),
  }))
  await page.route('**/api/v3/fuyao/status*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      provider: 'fuyao',
      configured: true,
      connection_status: '已连接',
      capabilities: { quotes: { status: '已连接' } },
    }),
  }))

  await login(page, facts.users.a)
  await expect(page.locator('.system-status-button')).toContainText('需要完成验证')
  await expect(page.locator('.system-status-button')).not.toContainText('数据受限')
  await expect(page.locator('.system-status-button')).not.toContainText('正常')
  await expect(page.locator('.system-status-button')).not.toContainText('系统异常')
})

test('First run shows one actionable checklist instead of empty dashboard cards', async ({ acceptancePage: page }) => {
  await page.goto('/login')
  await page.getByRole('button', { name: '首次使用？创建账户', exact: true }).click()
  const email = `first-run-${Date.now()}@example.com`
  await page.locator('input[type="text"]').fill('First Run User')
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').nth(0).fill('AcceptancePass123!')
  await page.locator('input[type="password"]').nth(1).fill('AcceptancePass123!')
  await page.getByRole('button', { name: '创建账户并登录', exact: true }).click()

  await expect(page).toHaveURL(/\/dashboard/)
  await expect(page.getByRole('heading', { name: '开始使用' })).toBeVisible()
  await expect(page.locator('.setup-card')).toBeVisible()
  await expect(page.getByText('今日市场', { exact: true })).toHaveCount(0)
  await expect(page.getByText('当前没有明显的新机会', { exact: true })).toHaveCount(0)
})

test('Home prioritizes market, final decision, freshness, and a legal no-candidate state', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await selectPortfolio(page, 'Acceptance Freshness')
  await expect(page.getByText('今日市场', { exact: true })).toBeVisible()
  await expect(page.locator('.freshness-label').first()).toBeVisible()

  const homeText = await page.locator('main').innerText()
  expect(homeText.indexOf('今日建议')).toBeGreaterThanOrEqual(0)
  expect(homeText.indexOf('今日建议')).toBeLessThan(homeText.indexOf('关注机会'))
  await expect(page.getByText('当前没有明显的新机会', { exact: true })).toBeVisible()
})

test('Technical details stay collapsed until requested and analysis progress remains user-readable', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await page.goto(`/analysis?portfolio=${facts.portfolios.action}&run=${facts.runs.action}`)
  await expect(page.getByRole('heading', { name: '今日分析' })).toBeVisible()
  const technical = page.getByText('查看详细证据与量化指标', { exact: true })
  await expect(technical).toBeVisible()
  const technicalPanel = page.locator('.technical-details').filter({ hasText: '查看详细证据与量化指标' }).first()
  await expect(technicalPanel.locator('.n-collapse-item__content-wrapper')).toBeHidden()
  await technical.click()
  await expect(technicalPanel.locator('.n-collapse-item__content-wrapper')).toBeVisible()
  await expect(technicalPanel).toContainText('来源链')

  const jobId = 990001
  const job = {
    id: jobId,
    portfolio_id: facts.portfolios.action,
    snapshot_id: 1,
    trigger_type: 'manual',
    checkpoint: '10:30',
    mode: 'deep',
    status: 'running',
    progress_percent: 52,
    current_stage: 'investment_debate',
    notify: false,
    retry_count: 0,
    created_at: '2026-08-21T06:00:00Z',
  }
  await page.route(`**/api/v2/analysis/jobs/${jobId}`, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(job) }))
  await page.goto(`/analysis?portfolio=${facts.portfolios.action}&job=${jobId}`)
  await expect(page.getByText('正在分析你的组合', { exact: true })).toBeVisible()
  await expect(page.getByText('市场环境', { exact: true })).toBeVisible()
  await expect(page.getByText('组合决策', { exact: true })).toBeVisible()
})
