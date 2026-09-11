import { test, expect, login, selectPortfolio } from './fixtures'

function dashboard(score: number) {
  return {
    trade_date: '2026-09-11',
    as_of: '2026-09-11T10:00:00+08:00',
    market_open: true,
    market: { score, quality_status: 'VALID', freshness: 'FRESH', health_status: 'VALID' },
    portfolio: { total_assets: score * 1000, market_value: score * 800, spendable_cash: score * 200, gross_exposure: 0.8 },
    data_health: { overall: 'OK' },
    decisions: {},
    analysis: {},
    candidates: {},
  }
}

test('Dashboard ignores a stale portfolio response during initialization', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)

  let releaseA!: () => void
  let markAStarted!: () => void
  const aGate = new Promise<void>((resolve) => { releaseA = resolve })
  const aStarted = new Promise<void>((resolve) => { markAStarted = resolve })
  await page.route('**/api/v3/portfolios/*/dashboard/today', async (route) => {
    const match = route.request().url().match(/portfolios\/(\d+)\/dashboard/)
    const portfolioId = Number(match?.[1])
    if (portfolioId === facts.portfolios.action) {
      markAStarted()
      await aGate
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(dashboard(11)) }).catch(() => undefined)
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(dashboard(22)) })
  })

  await page.goto(`/dashboard?portfolio=${facts.portfolios.action}`)
  await aStarted
  await selectPortfolio(page, 'Acceptance States')
  await expect(page.locator('.market-score')).toHaveText('22.0')

  releaseA()
  await page.waitForTimeout(100)
  await expect(page.locator('.market-score')).toHaveText('22.0')
  await expect(page.locator('.n-message').filter({ hasText: /超时|失败|错误/ })).toHaveCount(0)
})
