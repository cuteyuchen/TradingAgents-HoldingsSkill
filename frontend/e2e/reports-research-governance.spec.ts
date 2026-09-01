import { test, expect, login, openPage } from './fixtures'

test('Reports exposes final decision, checkpoint, mode, quality, market context, and lineage', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await page.goto(`/reports?portfolio=${facts.portfolios.action}&run=${facts.runs.action}`)
  await expect(page.getByRole('heading', { name: '分析报告' })).toBeVisible()
  await expect(page.locator('.decision-hero')).toContainText('ACTION')
  await expect(page.locator('.action-table-panel')).toContainText('今日持仓操作')
  await expect(page.locator('.n-tabs-tab').filter({ hasText: '完整分析流程' })).toBeVisible()
  await expect(page.locator('.workflow-heading')).toContainText('分析与辩论记录')
  await expect(page.locator('.flow-rail')).toContainText('风控')
  await expect(page.locator('.workflow-stage').filter({ hasText: '组合经理最终决策' })).toContainText('ACTION')
  await page.locator('.n-tabs-tab').filter({ hasText: '结构化证据' }).click()
  await expect(page.locator('.json-panel')).toContainText('Acceptance deterministic fixture')
})

test('Research creates a deterministic run, recovers the durable result, and shows PARTIAL PIT capability', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await openPage(page, '/research', '历史回放与参数校准')
  await expect(page.getByText('PARTIAL_PIT_RECOMPUTE', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('部分历史输入缺失，仅供研究', { exact: false })).toBeVisible()

  const scope = page.locator('.form-grid > label').filter({ hasText: 'Scope' }).locator('.n-base-selection')
  await scope.click()
  await page.locator('.n-base-select-option').filter({ hasText: 'Portfolio Decision' }).last().click()
  const replay = page.locator('.form-grid > label').filter({ hasText: 'Replay Mode' }).locator('.n-base-selection')
  await replay.click()
  await page.locator('.n-base-select-option').filter({ hasText: 'Deterministic Recompute' }).last().click()
  await page.locator('input[type="date"]').nth(0).fill('2026-08-20')
  await page.locator('input[type="date"]').nth(1).fill('2026-08-21')
  await page.getByRole('button', { name: '启动研究 Run', exact: true }).click()
  await expect(page.getByText('COMPLETED', { exact: true }).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.recompute-result')).toContainText('部分历史输入缺失，仅供研究')
  await page.reload()
  await expect(page.getByText('COMPLETED', { exact: true }).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.recompute-result')).toContainText('PARTIAL_PIT_RECOMPUTE')
})

test('Governance keeps proposal evidence and requires explicit activation confirmation', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  await openPage(page, '/governance', '参数治理')
  await expect(page.getByText('不会自动应用参数', { exact: false })).toBeVisible()
  await expect(page.locator('.proposal-row').filter({ hasText: `#${facts.governance.proposal_id}` })).toBeVisible()
  await expect(page.getByText('Config Hash', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Runtime', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Decision', { exact: true }).first()).toBeVisible()

  const approvedVersion = page.locator('.version-row').filter({ hasText: 'APPROVED' })
  if (await approvedVersion.count()) {
    const dialogs: string[] = []
    page.on('dialog', async (dialog) => {
      dialogs.push(dialog.message())
      await dialog.accept(dialog.type() === 'prompt' ? 'Acceptance safety confirmation' : undefined)
    })
    await approvedVersion.getByRole('button', { name: '激活', exact: true }).click()
    await expect.poll(() => dialogs.length).toBe(3)
    expect(dialogs.some((item) => item.includes('准备激活参数版本'))).toBe(true)
    expect(dialogs.some((item) => item.includes('二次确认'))).toBe(true)
  }
  await expect(page.getByText('不会自动应用参数', { exact: false })).toBeVisible()
})
