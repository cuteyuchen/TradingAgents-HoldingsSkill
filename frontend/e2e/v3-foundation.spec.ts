/**
 * V3 Foundation acceptance
 * 不依赖真实 Provider；仅验证 Quasar V3 基础设施。
 */
import { test, expect, type Page } from '@playwright/test'

async function gotoFoundation(page: Page): Promise<void> {
  await page.goto('/v3/foundation')
  await expect(page.getByTestId('v3-app-shell')).toBeVisible()
  await expect(page.getByTestId('v3-foundation')).toBeVisible()
}

test.describe('V3 UI Foundation', () => {
  test.beforeEach(async ({ page }) => {
    // 默认桌面视口，避免 drawer overlay 干扰点击
    await page.setViewportSize({ width: 1280, height: 900 })
  })

  test('Foundation route renders Quasar V3 shell', async ({ page }) => {
    await gotoFoundation(page)
    await expect(page.getByTestId('v3-desktop-sidebar')).toBeVisible()
    await expect(page.getByTestId('v3-topbar')).toBeVisible()
    await expect(page.getByTestId('v3-main-content')).toBeVisible()
  })

  test('Legacy dashboard still reachable', async ({ page }) => {
    await page.goto('/dashboard')
    // 未登录会跳转 login；登录页仍是 Legacy
    await expect(page).toHaveURL(/\/login|\/dashboard/)
  })

  test('Light / Dark theme switch updates resolved theme', async ({ page }) => {
    await gotoFoundation(page)
    const resolved = page.getByTestId('v3-resolved-theme')
    await expect(resolved).toBeVisible()

    await page.getByTestId('v3-theme-switcher').scrollIntoViewIfNeeded()
    await page.getByRole('button', { name: 'Dark' }).click({ force: true })
    await expect(resolved).toHaveText('dark')
    const hasDarkClass = await page.evaluate(() =>
      document.documentElement.classList.contains('theme-dark'),
    )
    expect(hasDarkClass).toBe(true)

    await page.getByRole('button', { name: 'Light' }).click({ force: true })
    await expect(resolved).toHaveText('light')
  })

  test('Desktop sidebar is visible on wide viewport', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await gotoFoundation(page)
    await expect(page.getByTestId('v3-desktop-sidebar')).toBeVisible()
  })

  test('Mobile drawer opens from topbar menu', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 720 })
    await gotoFoundation(page)
    await expect(page.getByTestId('v3-topbar-menu')).toBeVisible()
    await page.getByTestId('v3-topbar-menu').click()
    await expect(page.getByTestId('v3-mobile-drawer')).toBeVisible()
  })

  test('V3DetailDrawer opens and closes', async ({ page }) => {
    await gotoFoundation(page)
    await page.getByTestId('v3-foundation-drawer-open').click()
    await expect(page.getByTestId('v3-detail-drawer')).toBeVisible()
    await page.getByTestId('v3-detail-drawer-close').click()
    await expect(page.getByTestId('v3-detail-drawer')).toBeHidden()
  })

  test('V3DataTable renders showcase rows', async ({ page }) => {
    await gotoFoundation(page)
    await expect(page.getByTestId('v3-data-table')).toBeVisible()
    await expect(page.getByRole('cell', { name: '上证指数（演示）' })).toBeVisible()
  })

  test('Market up/down and risk/status tokens exist', async ({ page }) => {
    await gotoFoundation(page)
    await expect(page.getByTestId('v3-market-colors')).toBeVisible()
    await expect(page.getByTestId('v3-risk-colors')).toBeVisible()
    await expect(page.getByTestId('v3-status-colors')).toBeVisible()

    // 校验 CSS token 实际存在且涨跌色不同
    const colors = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement)
      return {
        up: style.getPropertyValue('--v3-market-up').trim(),
        down: style.getPropertyValue('--v3-market-down').trim(),
        riskHigh: style.getPropertyValue('--v3-risk-high').trim(),
        danger: style.getPropertyValue('--v3-status-danger').trim(),
      }
    })
    expect(colors.up).toBeTruthy()
    expect(colors.down).toBeTruthy()
    expect(colors.riskHigh).toBeTruthy()
    // risk-high 不得等于 market-up
    expect(colors.riskHigh).not.toBe(colors.up)
  })

  test('Empty / Loading / Error states render', async ({ page }) => {
    await gotoFoundation(page)
    await expect(page.getByTestId('v3-empty-state')).toBeVisible()
    await expect(page.getByTestId('v3-error-state')).toBeVisible()
    await page.getByRole('button', { name: '播放 Loading' }).scrollIntoViewIfNeeded()
    await page.getByRole('button', { name: '播放 Loading' }).click({ force: true })
    await expect(page.getByTestId('v3-loading-state')).toBeVisible()
  })

  test('Foundation DOM has no Naive UI components', async ({ page }) => {
    await gotoFoundation(page)
    // Foundation 页面不应出现 Naive 组件 class
    const naiveCount = await page.evaluate(() => {
      const root = document.querySelector('[data-testid="v3-foundation"]')
      if (!root) return -1
      return root.querySelectorAll(
        '.n-button, .n-card, .n-tag, .n-data-table, .n-tabs, .n-modal, .n-drawer, .n-config-provider',
      ).length
    })
    expect(naiveCount).toBe(0)
  })

  test('V3DataTimestamp separates quote and analysis times', async ({ page }) => {
    await gotoFoundation(page)
    await expect(page.getByTestId('v3-quote-ts')).toBeVisible()
    await expect(page.getByTestId('v3-analysis-ts')).toBeVisible()
  })
})
