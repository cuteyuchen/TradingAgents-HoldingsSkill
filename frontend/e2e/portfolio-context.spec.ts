import { test, expect, login, openPage, selectPortfolio } from './fixtures'

test('Portfolio context follows the selected portfolio across business pages', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await expect(page.getByTestId('v3-portfolio-select')).toBeVisible()
  await expect(page.getByTestId('v3-portfolio-name')).toContainText('Acceptance Action')

  await selectPortfolio(page, 'Acceptance States')
  await page.goto('/holdings')
  await expect(page.getByTestId('v3-holdings')).toBeVisible()
  await expect(page.getByTestId('v3-holdings-portfolio-select')).toHaveValue(/.+/)
  const selectedHoldingsText = await page.getByTestId('v3-holdings-portfolio-select').evaluate((el) => (el as HTMLSelectElement).selectedOptions[0]?.textContent || '')
  expect(selectedHoldingsText).toContain('Acceptance States')

  await openPage(page, '/reports', '今日分析')

  await openPage(page, '/research', '历史')
  await expect(page.locator('.n-tabs-tab').filter({ hasText: '策略研究' })).toBeVisible()

  await openPage(page, '/shadow', '模拟跟随')
  await expect(page.locator('.simulation-banner')).toContainText('不会发送真实订单')
  await expect(page.getByText('还没有模拟记录', { exact: true }).first()).toBeVisible()

  await selectPortfolio(page, 'Acceptance Action')
  await expect(page.getByText('不会发送真实订单', { exact: true })).toBeVisible()
})
