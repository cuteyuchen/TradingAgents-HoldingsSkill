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
  const drawer = page.locator('.n-drawer').last()
  await expect(drawer).toBeVisible()
  await page.locator('input[type="file"]').setInputFiles({
    name: 'identity.png',
    mimeType: 'image/png',
    buffer: Buffer.concat([validPngBytes(), Buffer.from(marker, 'utf8')]),
  })
  await drawer.getByRole('button', { name: '上传并识别', exact: true }).click()
  await expect(page.getByText('待人工确认', { exact: true })).toBeVisible({ timeout: 20_000 })
}

test('Case A/D: seven no-code holdings resolve to canonical codes and keep Chinese names', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const id = await createPortfolio(page, `Identity Seven ${Date.now()}`)
  await uploadIdentityFixture(page, 'identity-7cn', id)

  const rows = page.locator('.edit-table tbody tr')
  await expect(rows).toHaveCount(7)
  await expect(rows.nth(0).locator('input[placeholder="名称"]')).toHaveValue('创业板ETF')
  await expect(rows.nth(1).locator('input[placeholder="名称"]')).toHaveValue('通信ETF')
  await expect(rows.nth(0)).toContainText('已匹配')
  await expect(rows.nth(1)).toContainText('已匹配')
  await expect(rows.nth(0).locator('input[placeholder="证券代码"]')).toHaveValue('159915')
  await expect(rows.nth(1).locator('input[placeholder="证券代码"]')).toHaveValue('515880')

  const drawer = page.locator('.n-drawer').last()
  const confirm = drawer.getByRole('button', { name: '仅确认快照', exact: true })
  await expect(confirm).toBeEnabled()
  await confirm.click()
  await expect(page.getByText(/持仓快照已确认/)).toBeVisible({ timeout: 10_000 })
})

test('Case B: ambiguous identity blocks confirm until the user selects one security', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const id = await createPortfolio(page, `Identity Ambiguous ${Date.now()}`)
  await uploadIdentityFixture(page, 'identity-ambiguous', id)

  const drawer = page.locator('.n-drawer').last()
  const confirm = drawer.getByRole('button', { name: '仅确认快照', exact: true })
  await expect(confirm).toBeDisabled()
  await expect(page.getByText('需要选择', { exact: true })).toBeVisible()
  await drawer.getByRole('button', { name: '选择证券', exact: true }).click()

  const dialog = page.locator('.n-modal').filter({ hasText: '选择证券' })
  await expect(dialog).toBeVisible()
  const candidateRows = dialog.locator('.candidate-table tbody tr')
  await expect(candidateRows).toHaveCount(2)
  await expect(candidateRows.nth(0)).toContainText('同名验收ETF')
  await candidateRows.nth(0).getByRole('button', { name: '选择', exact: true }).click()

  await expect(page.getByText('已匹配', { exact: true }).first()).toBeVisible()
  await expect(confirm).toBeEnabled()
})

test('Case C: unresolved identity fails closed and keeps confirm disabled', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const id = await createPortfolio(page, `Identity Unresolved ${Date.now()}`)
  await uploadIdentityFixture(page, 'identity-unresolved', id)

  const drawer = page.locator('.n-drawer').last()
  await expect(page.getByText('未找到', { exact: true })).toBeVisible()
  await expect(page.getByText(/还有 1 个持仓未确认证券身份/)).toBeVisible()
  await expect(drawer.getByRole('button', { name: '仅确认快照', exact: true })).toBeDisabled()
  await expect(drawer.getByRole('button', { name: '确认并立即分析', exact: true })).toBeDisabled()
})
