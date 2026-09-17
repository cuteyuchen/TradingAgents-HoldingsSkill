import type { Page } from '@playwright/test'
import { test, expect, login, validPngBytes } from './fixtures'

async function createPortfolio(page: Page, name: string): Promise<number> {
  const id = await page.evaluate(async (portfolioName) => {
    const token = localStorage.getItem('advisor_v2_access_token')
    const response = await fetch('/api/v2/portfolios', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ name: portfolioName }),
    })
    if (!response.ok) throw new Error(`create portfolio failed: ${response.status}`)
    const row = await response.json() as { id: number }
    return row.id
  }, name)
  expect(id).toBeGreaterThan(0)
  return id
}

async function uploadIdentityFixture(page: Page, marker: string, portfolioId: number): Promise<void> {
  await page.goto(`/upload?portfolio=${portfolioId}`)
  const drawer = page.getByTestId('v3-holdings-update-drawer')
  await expect(drawer).toBeVisible({ timeout: 20_000 })
  await page.locator('input[type="file"]').setInputFiles({
    name: 'identity.png',
    mimeType: 'image/png',
    buffer: Buffer.concat([validPngBytes(), Buffer.from(marker, 'utf8')]),
  })
  await drawer.getByRole('button', { name: '上传并识别', exact: true }).click()
  await expect(page.getByTestId('v3-update-status')).toContainText('待人工确认', { timeout: 20_000 })
}

function identityRows(page: Page) {
  return page.locator('[data-testid^="v3-identity-row-"]')
}

test('Case A/D: seven no-code holdings resolve to canonical codes and keep Chinese names', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const id = await createPortfolio(page, `Identity Seven ${Date.now()}`)
  await uploadIdentityFixture(page, 'identity-7cn', id)

  const rows = identityRows(page)
  await expect(rows).toHaveCount(7)
  await expect(rows.nth(0).locator('input[placeholder="名称"]')).toHaveValue('创业板ETF')
  await expect(rows.nth(1).locator('input[placeholder="名称"]')).toHaveValue('通信ETF')
  await expect(rows.nth(0)).toContainText('已匹配')
  await expect(rows.nth(1)).toContainText('已匹配')
  await expect(rows.nth(0).locator('input[placeholder="证券代码"]')).toHaveValue('159915')
  await expect(rows.nth(1).locator('input[placeholder="证券代码"]')).toHaveValue('515880')

  const drawer = page.getByTestId('v3-holdings-update-drawer')
  const confirm = drawer.getByRole('button', { name: '仅确认快照', exact: true })
  await expect(confirm).toBeEnabled()
  await confirm.click()
  await expect(drawer).toContainText('当前使用快照', { timeout: 15_000 })
})

test('Case B: ambiguous identity blocks confirm until the user selects one security', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const id = await createPortfolio(page, `Identity Ambiguous ${Date.now()}`)
  await uploadIdentityFixture(page, 'identity-ambiguous', id)

  const drawer = page.getByTestId('v3-holdings-update-drawer')
  const confirm = drawer.getByRole('button', { name: '仅确认快照', exact: true })
  await expect(confirm).toBeDisabled()
  await expect(page.getByText('需要选择', { exact: true })).toBeVisible()
  await page.getByTestId('v3-identity-select-candidate').click()

  const dialog = page.getByTestId('v3-security-candidate-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('同名验收ETF').first()).toBeVisible()
  await page.getByTestId('v3-candidate-pick').first().click()

  await expect(page.getByText('已匹配', { exact: true }).first()).toBeVisible()
  await expect(confirm).toBeEnabled()
})

test('Case C: unresolved identity fails closed and keeps confirm disabled', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const id = await createPortfolio(page, `Identity Unresolved ${Date.now()}`)
  await uploadIdentityFixture(page, 'identity-unresolved', id)

  const drawer = page.getByTestId('v3-holdings-update-drawer')
  await expect(page.getByText('未找到', { exact: true })).toBeVisible()
  await expect(page.getByText(/还有 1 个持仓未确认证券身份/)).toBeVisible()
  await expect(drawer.getByRole('button', { name: '仅确认快照', exact: true })).toBeDisabled()
  await expect(drawer.getByRole('button', { name: '确认并立即分析', exact: true })).toBeDisabled()
})

test('Historical auto reuse: same-portfolio confirmed alias fills a blank code', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const id = await createPortfolio(page, `Identity History Reuse ${Date.now()}`)

  await uploadIdentityFixture(page, 'identity-history-source', id)
  const firstDrawer = page.getByTestId('v3-holdings-update-drawer')
  await expect(firstDrawer.getByRole('button', { name: '仅确认快照', exact: true })).toBeEnabled()
  await firstDrawer.getByRole('button', { name: '仅确认快照', exact: true }).click()
  await expect(firstDrawer).toContainText('当前使用快照', { timeout: 15_000 })

  await uploadIdentityFixture(page, 'identity-history-reuse', id)
  const row = identityRows(page).first()
  await expect(row.locator('input[placeholder="证券代码"]')).toHaveValue('159915')
  await expect(row.locator('input[placeholder="名称"]')).toHaveValue('创业板ETF')
  await expect(row).toContainText('已匹配 · 历史')

  const drawer = page.getByTestId('v3-holdings-update-drawer')
  await expect(drawer.getByRole('button', { name: '仅确认快照', exact: true })).toBeEnabled()
})

test('Mostly automatic: six rows auto-resolve and one ambiguous row blocks confirm', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const id = await createPortfolio(page, `Identity Mostly ${Date.now()}`)
  await uploadIdentityFixture(page, 'identity-mostly', id)

  const rows = identityRows(page)
  await expect(rows).toHaveCount(7)
  await expect(page.getByText('需要选择', { exact: true })).toBeVisible()
  await expect(page.getByTestId('v3-holdings-identity-table')).toContainText('已匹配')

  const drawer = page.getByTestId('v3-holdings-update-drawer')
  await expect(drawer.getByRole('button', { name: '仅确认快照', exact: true })).toBeDisabled()
  await page.getByTestId('v3-identity-select-candidate').click()

  const dialog = page.getByTestId('v3-security-candidate-dialog')
  await expect(dialog).toBeVisible()
  await page.getByTestId('v3-candidate-pick').first().click()

  await expect(page.getByText('已匹配', { exact: true }).first()).toBeVisible()
  await expect(drawer.getByRole('button', { name: '仅确认快照', exact: true })).toBeEnabled()
})
