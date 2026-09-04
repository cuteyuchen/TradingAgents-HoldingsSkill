import { test, expect, captureScreenshot, login, allowExpectedHttpError } from './fixtures'

test('Auth happy path, logout, and both session expiry branches', async ({ acceptancePage: page, facts }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await captureScreenshot(page, 'login')

  await login(page, facts.users.a)
  await expect(page.locator('header.topbar')).not.toContainText(facts.users.a.email)
  await expect(page.getByText('今日市场', { exact: true })).toBeVisible()
  await captureScreenshot(page, 'dashboard-authenticated')

  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('heading', { name: '进入投资驾驶舱' })).toBeVisible()

  await login(page, facts.users.b)
  await expect.poll(async () => page.evaluate(() => localStorage.getItem('advisor_selected_portfolio_id'))).toBe(String(facts.portfolios.user_b))
  await expect(page.locator('.global-portfolio-select')).toHaveCount(0)
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login/)

  await login(page, facts.users.a)
  allowExpectedHttpError(page, 401)
  await page.evaluate(() => localStorage.setItem('advisor_v2_access_token', 'acceptance-expired-access'))
  await page.reload()
  await expect(page).toHaveURL(/\/dashboard(?:\?.*)?$/)
  await expect(page.getByRole('heading', { name: /今天/ })).toBeVisible()
  await expect(page.evaluate(() => localStorage.getItem('advisor_v2_access_token'))).not.toBe('acceptance-expired-access')
  await page.waitForLoadState('networkidle')

  await page.evaluate(() => {
    localStorage.setItem('advisor_v2_access_token', 'acceptance-expired-access-again')
    localStorage.setItem('advisor_v2_refresh_token', 'acceptance-expired-refresh')
  })
  await page.reload()
  await expect(page).toHaveURL(/\/login\?expired=1/)
  await expect(page.getByText('登录状态已过期，请重新登录。', { exact: true })).toBeVisible()
})
