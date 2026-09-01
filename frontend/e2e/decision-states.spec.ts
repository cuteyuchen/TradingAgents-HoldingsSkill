import { test, expect, login, openPage, selectPortfolio } from './fixtures'

test.describe('decision states', () => {
  test('ACTION is visible on Dashboard and Reports with independent candidate evidence', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    const decisionCard = page.locator('.decision-card')
    await expect(decisionCard).toContainText('ACTION')
    await expect(decisionCard).toContainText('请查看最新报告中的持仓动作与执行前提')

    await page.goto(`/reports?portfolio=${facts.portfolios.action}&run=${facts.runs.action}`)
    await expect(page.locator('.decision-hero')).toContainText('ACTION')
    await expect(page.locator('.action-table-panel')).toContainText('减仓')
    await expect(page.locator('.candidate-panel')).toContainText('新增机会候选')
    await expect(page.locator('.candidate-panel')).toContainText('创业板ETF')
  })

  test('NO_ACTION, BLOCKED, DATA_GAP, and Candidate Veto remain explicit', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await selectPortfolio(page, 'Acceptance States')

    await page.goto(`/reports?portfolio=${facts.portfolios.states}&run=${facts.runs.no_action}`)
    await expect(page.locator('.decision-hero')).toContainText('NO_ACTION')
    await expect(page.locator('.decision-hero')).not.toContainText('暂无结果')

    await page.goto(`/reports?portfolio=${facts.portfolios.states}&run=${facts.runs.blocked}`)
    await expect(page.locator('.decision-hero')).toContainText('BLOCKED')
    await expect(page.locator('.decision-hero')).toContainText('数据质量门控阻断')

    await page.goto(`/reports?portfolio=${facts.portfolios.states}&run=${facts.runs.data_gap}`)
    await expect(page.locator('.decision-hero')).toContainText('DATA_GAP')
    await page.locator('.n-tabs-tab').filter({ hasText: '结构化证据' }).click()
    await expect(page.locator('.evidence-grid')).toContainText('关键行情不可用')
    await expect(page.locator('.candidate-panel')).not.toContainText('0.00')

  await page.goto(`/reports?portfolio=${facts.portfolios.states}&run=${facts.runs.veto}`)
  await expect(page.locator('.decision-hero')).toContainText('NO_ACTION')
  await expect(page.locator('.candidate-veto')).toContainText('Candidate Veto')
  await expect(page.locator('.candidate-veto')).toContainText('候选达到 ACTION，但组合层未批准')

  const shadowOrders = await page.evaluate(async (portfolioId) => {
    const token = localStorage.getItem('advisor_v2_access_token')
    const response = await fetch(`/api/v3/shadow/orders?portfolio_id=${portfolioId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    })
    return { status: response.status, body: await response.json() }
  }, facts.portfolios.states)
  expect(shadowOrders.status).toBe(200)
  expect(shadowOrders.body).toEqual([])
})

  test('Dashboard freshness does not promote yesterday ACTION to today', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await selectPortfolio(page, 'Acceptance Freshness')
    await expect(page.locator('.decision-card')).toContainText('NO_ACTION')
    await expect(page.locator('.decision-card')).toContainText('今日尚未完成分析')
  })
})
