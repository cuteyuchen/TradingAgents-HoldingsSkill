import { test, expect, login, openPage } from './fixtures'

test('Shadow keeps Decision, Intent, Fill, and Outcome separate and paper-only', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await page.goto(`/shadow?portfolio=${facts.portfolios.action}`)
  await expect(page).toHaveURL(/\/simulation/)
  await expect(page.getByRole('heading', { name: '模拟跟随' })).toBeVisible()
  await expect(page.getByText('SHADOW', { exact: true })).toBeVisible()
  await expect(page.getByText('不会发送真实订单', { exact: true })).toBeVisible()
  await expect(page.getByText('Decision', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Execution', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Outcome', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.performance-note')).toContainText('PENDING')
  await expect(page.getByText('FILLED', { exact: true })).toBeVisible()
  await expect(page.getByText('BLOCKED', { exact: true })).toBeVisible()
  await expect(page.getByText('EXPIRED', { exact: true })).toBeVisible()
  await expect(page.getByText('条件加仓仅记录建议，V1 暂不模拟条件触发成交。', { exact: true })).toBeVisible()
  await expect(page.getByText('—', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.metric-grid.six .metric-tile').filter({ hasText: '样本天数' }).locator('strong')).toHaveText('—')
  await expect(page.getByText('0%', { exact: true })).toHaveCount(0)

  const observationId = facts.shadow.observation_id
  expect(observationId).not.toBeNull()
  const detail = await page.evaluate(async (id) => {
    const token = localStorage.getItem('advisor_v2_access_token')
    const response = await fetch(`/api/v3/shadow/decisions/${id}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    })
    return { status: response.status, body: await response.json() }
  }, observationId)
  expect(detail.status).toBe(200)
  expect(detail.body.final_action).toBe('ACTION')
  const finalizedAt = Date.parse(detail.body.decision_finalized_at)
  const fills = detail.body.execution?.fills || []
  expect(fills.length).toBeGreaterThan(0)
  expect(fills.some((item: any) => item.quote_captured_at && Date.parse(item.quote_captured_at) > finalizedAt)).toBe(true)
  expect(detail.body.execution?.intents?.some((item: any) => item.status === 'FILLED')).toBe(true)
})
