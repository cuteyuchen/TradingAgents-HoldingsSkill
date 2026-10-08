import { test, expect, login, openPage } from './fixtures'

test('System renders the real NOT_READY readiness result and blockers', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await openPage(page, '/system', '设置')

  const card = page.locator('.live-readiness-card')
  await expect(card.getByText('未就绪', { exact: true }).first()).toBeVisible()
  await expect(card).toContainText('阻断项')
  await expect(card).toContainText('暂不能进入真实验证')
  await expect(card).not.toContainText('可以进入真实验证前置阶段')
  await expect(card).toContainText('行情数据源')
  await expect(card).toContainText('行情处理流程')
  await expect(card).toContainText('尚未观察到真实行情数据源成功返回数据')
  await expect(card).toContainText('受阻')
  await expect(card).not.toContainText('quote_provider_not_observed')
  await expect(card).not.toContainText('BLOCKED')
  await expect(card).not.toContainText('NOT_READY')
})
