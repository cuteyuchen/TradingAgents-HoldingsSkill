import { test, expect, login, validPngBytes } from './fixtures'

test('Upload, deterministic recognition, confirm, and invalid parse stay in review', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await page.getByRole('button', { name: '新建组合', exact: true }).click()
  const portfolioName = 'Acceptance Upload Portfolio'
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('组合名称').locator('input').fill(portfolioName)
  await dialog.getByRole('button', { name: '创建组合', exact: true }).click()
  await expect(page.locator('.portfolio-context-bar')).toContainText(portfolioName)

  await page.goto('/upload')
  await page.locator('input[type="file"]').setInputFiles({
    name: 'acceptance-holdings.png',
    mimeType: 'image/png',
    buffer: validPngBytes(),
  })
  await page.getByRole('button', { name: '上传并识别', exact: true }).click()
  await expect(page.getByText('待人工确认', { exact: true })).toBeVisible({ timeout: 15_000 })
  const holdingRows = page.locator('.edit-table tbody tr')
  await expect(holdingRows.nth(0).locator('input[placeholder="名称"]')).toHaveValue('贵州茅台')
  await expect(holdingRows.nth(1).locator('input[placeholder="名称"]')).toHaveValue('沪深300ETF')
  await page.getByRole('button', { name: '仅确认快照', exact: true }).click()
  await expect(page.getByText(/持仓快照已确认/)).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.analysis-panel')).toContainText('当前使用快照')

  const uploadPortfolioId = await page.evaluate(async (name) => {
    const token = localStorage.getItem('advisor_v2_access_token')
    const response = await fetch('/api/v2/portfolios', {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    })
    const rows = await response.json() as Array<{ id: number; name: string }>
    return rows.find((item) => item.name === name)?.id || null
  }, portfolioName)
  expect(uploadPortfolioId).not.toBeNull()
  await page.goto(`/shadow?portfolio=${uploadPortfolioId}`)
  await expect(page.getByText('当前组合还没有 Shadow Account', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '创建 Shadow Account', exact: true }).first().click()
  const shadowDialog = page.getByRole('dialog')
  await expect(shadowDialog).toBeVisible()
  await shadowDialog.locator('input').fill('Acceptance UI Shadow')
  await shadowDialog.getByRole('button', { name: '确认创建', exact: true }).click()
  await expect(page.locator('.account-card')).toContainText('paper-only')

  await page.goto('/upload')
  await page.locator('input[type="file"]').setInputFiles({
    name: 'acceptance-invalid.png',
    mimeType: 'image/png',
    buffer: Buffer.concat([validPngBytes(), Buffer.from('acceptance-invalid', 'utf8')]),
  })
  await page.getByRole('button', { name: '上传并识别', exact: true }).click()
  await expect(page.getByText('没有识别到有效的股票或 ETF 持仓', { exact: true })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: '仅确认快照', exact: true })).toBeDisabled()
})
