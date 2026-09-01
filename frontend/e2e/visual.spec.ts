import { test, captureScreenshot, expect, login } from './fixtures'

test('Desktop visual smoke at 1440, 1366, and 1920', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const screens = [
    [1440, 900, ['login', 'dashboard', 'upload', 'reports', 'research', 'governance', 'system', 'shadow', 'settings']],
    [1366, 768, ['dashboard', 'reports', 'shadow', 'system']],
    [1920, 1080, ['dashboard', 'reports', 'shadow', 'system']],
  ] as const
  const pages = [
    ['/dashboard', '今日操作台'],
    ['/upload', '上传与手动分析'],
    ['/reports', '分析报告'],
    ['/research', '历史回放与参数校准'],
    ['/governance', '参数治理'],
    ['/system', '系统运维'],
    ['/shadow', 'Shadow 验证'],
    ['/settings', '系统设置'],
  ] as const

  for (const [width, height, names] of screens) {
    await page.setViewportSize({ width, height })
    if (names.includes('login')) {
      await page.getByRole('button', { name: '退出登录' }).click()
      await expect(page).toHaveURL(/\/login/)
      await captureScreenshot(page, 'login')
      await login(page, facts.users.a)
    }
    for (const [route, heading] of pages) {
      const name = route.slice(1)
      if (!names.includes(name)) continue
      await page.goto(route)
      await expect(page.getByRole('heading', { name: heading })).toBeVisible()
      await captureScreenshot(page, name)
    }
  }
})
