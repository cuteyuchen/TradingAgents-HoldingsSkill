import { test, expect, login, openPage } from './fixtures'

test('System renders the real NOT_READY readiness result and blockers', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await openPage(page, '/system', '系统运维')

  const card = page.locator('.live-readiness-card')
  await expect(card.getByText('NOT_READY', { exact: true }).first()).toBeVisible()
  await expect(card).toContainText('Blockers')
  await expect(card).toContainText('暂不能进入真实验证')
  await expect(card).not.toContainText('可以进入真实验证前置阶段')
})
