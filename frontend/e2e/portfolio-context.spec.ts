import { test, expect, login, openPage, selectPortfolio } from './fixtures'

test('Portfolio context follows the selected portfolio across business pages', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await expect(page.locator('.portfolio-context-bar')).toContainText('Acceptance Action')

  await selectPortfolio(page, 'Acceptance States')
  await expect(page.locator('.as-of-line')).toContainText('Acceptance States')

  await openPage(page, '/reports', '分析报告')
  await expect(page.locator('.page-heading')).toContainText('Acceptance States')

  await openPage(page, '/research', '历史回放与参数校准')
  await expect(page.locator('.research-header')).toContainText('Acceptance States')

  await openPage(page, '/shadow', 'Shadow 验证')
  await expect(page.locator('.portfolio-context-bar')).toContainText('Acceptance States')
  await expect(page.getByText('当前组合还没有 Shadow Account', { exact: true })).toBeVisible()

  await selectPortfolio(page, 'Acceptance Action')
  await expect(page.getByText('不会发送真实订单', { exact: true })).toBeVisible()
})
