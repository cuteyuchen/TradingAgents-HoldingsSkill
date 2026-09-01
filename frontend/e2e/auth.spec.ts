import { test, expect, captureScreenshot, login, allowExpectedHttpError } from './fixtures'

test('Auth happy path, logout, and both session expiry branches', async ({ acceptancePage: page, facts }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await captureScreenshot(page, 'login')

  await login(page, facts.users.a)
  await expect(page.locator('.user-copy')).toContainText(facts.users.a.email)
  await captureScreenshot(page, 'dashboard-authenticated')

  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('heading', { name: '登录持仓投研系统' })).toBeVisible()

  await login(page, facts.users.a)
  allowExpectedHttpError(page, 401)
  await page.evaluate(() => localStorage.setItem('advisor_v2_access_token', 'acceptance-expired-access'))
  await page.reload()
  await expect(page).toHaveURL(/\/dashboard(?:\?.*)?$/)
  await expect(page.getByRole('heading', { name: '今日操作台' })).toBeVisible()
  await expect(page.evaluate(() => localStorage.getItem('advisor_v2_access_token'))).not.toBe('acceptance-expired-access')

  await page.evaluate(() => {
    localStorage.setItem('advisor_v2_access_token', 'acceptance-expired-access-again')
    localStorage.setItem('advisor_v2_refresh_token', 'acceptance-expired-refresh')
  })
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/login\?expired=1/)
  await expect(page.getByText('登录状态已过期，请重新登录。', { exact: true })).toBeVisible()
})
