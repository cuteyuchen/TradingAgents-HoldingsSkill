import { test, captureScreenshot, expect, login } from './fixtures'

async function waitForVisualContent(page: Parameters<typeof login>[0], route: string): Promise<void> {
  await expect(page.locator('.shared-loading')).toHaveCount(0, { timeout: 20_000 })
  if (route.startsWith('/dashboard')) await expect(page.getByText('今日市场', { exact: true })).toBeVisible()
  if (route.startsWith('/holdings')) await expect(page.getByText('持仓列表', { exact: true })).toBeVisible()
  if (route.startsWith('/analysis')) await expect(page.locator('.decision-hero, .shared-empty, .progress-panel, .failed-panel').first()).toBeVisible()
  if (route.startsWith('/simulation')) await expect(page.locator('.simulation-banner')).toBeVisible()
  if (route.startsWith('/history')) await expect(page.getByRole('heading', { name: '历史表现', exact: true })).toBeVisible()
  if (route.startsWith('/settings')) {
    if (route.includes('section=system')) {
      await expect(page.getByRole('heading', { name: '系统运维', exact: true })).toBeVisible()
      await expect(page.getByText('Live Validation Readiness', { exact: true })).toBeVisible()
    } else {
      await expect(page.getByRole('heading', { name: '数据与行情', exact: true })).toBeVisible()
    }
  }
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  await expect.poll(() => page.evaluate(() => {
    const header = document.querySelector('header.topbar')
    return !header || header.scrollWidth <= header.clientWidth + 1
  })).toBe(true)
}

test('Desktop visual smoke at 1440, 1366, and 1920', async ({ acceptancePage: page, facts }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '进入投资驾驶舱' })).toBeVisible()
  await captureScreenshot(page, 'login-light')

  await page.getByRole('button', { name: '首次使用？创建账户', exact: true }).click()
  const unique = `visual-${Date.now()}@example.com`
  await page.locator('input[type="text"]').fill('Visual User')
  await page.locator('input[type="email"]').fill(unique)
  await page.locator('input[type="password"]').nth(0).fill('AcceptancePass123!')
  await page.locator('input[type="password"]').nth(1).fill('AcceptancePass123!')
  await page.getByRole('button', { name: '创建账户并登录', exact: true }).click()
  await expect(page).toHaveURL(/\/dashboard/)
  await expect(page.getByRole('heading', { name: '开始使用' })).toBeVisible()
  await captureScreenshot(page, 'home-first-run-light')
  await page.getByRole('button', { name: '退出登录' }).click()
  await login(page, facts.users.a)

  const pages = [
    ['/dashboard', /今天/, 'home-normal-light'],
    ['/holdings', '我的持仓', 'holdings-light'],
    ['/holdings?action=update', '我的持仓', 'holdings-update-light'],
    [`/analysis?portfolio=${facts.portfolios.states}&run=${facts.runs.no_action}`, '今日分析', 'analysis-no-action-light'],
    [`/analysis?portfolio=${facts.portfolios.action}&run=${facts.runs.action}`, '今日分析', 'analysis-action-light'],
    ['/simulation', '模拟跟随', 'simulation-light'],
    ['/history', '历史', 'history-light'],
    ['/settings', '设置', 'settings-light'],
    ['/settings?section=system', '设置', 'settings-system-light'],
  ] as const
  for (const [route, heading, name] of pages) {
    await page.goto(route)
    const headingLocator = typeof heading === 'string'
      ? page.getByRole('heading', { name: heading, exact: true })
      : page.getByRole('heading', { name: heading })
    await expect(headingLocator).toBeVisible()
    await waitForVisualContent(page, route)
    if (route === '/holdings?action=update') {
      const drawer = page.locator('.n-drawer').last()
      await expect(drawer).toBeVisible()
      await expect.poll(() => drawer.evaluate((element) => {
        const rect = element.getBoundingClientRect()
        return rect.left >= 0 && rect.right <= window.innerWidth + 1 && rect.top >= 0 && rect.bottom <= window.innerHeight + 1
      })).toBe(true)
    }
    await captureScreenshot(page, name)
  }

  const runningJob = {
    id: 990002,
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
  await page.route(`**/api/v2/analysis/jobs/${runningJob.id}`, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runningJob) }))
  await page.goto(`/analysis?portfolio=${facts.portfolios.action}&job=${runningJob.id}`)
  await expect(page.getByText('正在分析你的组合', { exact: true })).toBeVisible()
  await waitForVisualContent(page, '/analysis')
  await captureScreenshot(page, 'analysis-running-light')
  await page.unroute(`**/api/v2/analysis/jobs/${runningJob.id}`)

  await page.evaluate(() => localStorage.setItem('advisor_theme', 'dark'))
  await page.reload()
  await expect(page.locator('.app-root.theme-dark')).toBeVisible()
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('heading', { name: '进入投资驾驶舱' })).toBeVisible()
  await captureScreenshot(page, 'login-dark')
  await login(page, facts.users.a)
  for (const [route, heading, name] of pages) {
    await page.goto(route)
    const headingLocator = typeof heading === 'string'
      ? page.getByRole('heading', { name: heading, exact: true })
      : page.getByRole('heading', { name: heading })
    await expect(headingLocator).toBeVisible()
    await waitForVisualContent(page, route)
    await captureScreenshot(page, name.replace(/-light$/, '-dark'))
  }

  await page.route(`**/api/v2/analysis/jobs/${runningJob.id}`, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runningJob) }))
  await page.goto(`/analysis?portfolio=${facts.portfolios.action}&job=${runningJob.id}`)
  await expect(page.getByText('正在分析你的组合', { exact: true })).toBeVisible()
  await waitForVisualContent(page, '/analysis')
  await captureScreenshot(page, 'analysis-running-dark')
  await page.unroute(`**/api/v2/analysis/jobs/${runningJob.id}`)

  await page.evaluate(() => localStorage.setItem('advisor_theme', 'light'))
  await page.reload()
  await expect(page.locator('.app-root.theme-light')).toBeVisible()

  for (const [width, height, routes] of [
    [1366, 768, [['/dashboard', /今天/, 'home-1366'], ['/holdings', '我的持仓', 'holdings-1366'], ['/analysis', '今日分析', 'analysis-1366']]],
    [1920, 1080, [['/dashboard', /今天/, 'home-1920'], ['/simulation', '模拟跟随', 'simulation-1920']]],
  ] as const) {
    await page.setViewportSize({ width, height })
    for (const [route, heading, name] of routes) {
      await page.goto(route)
      const headingLocator = typeof heading === 'string'
        ? page.getByRole('heading', { name: heading, exact: true })
        : page.getByRole('heading', { name: heading })
      await expect(headingLocator).toBeVisible()
      await waitForVisualContent(page, route)
      await captureScreenshot(page, name)
    }
  }
})
