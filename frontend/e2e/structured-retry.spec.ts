import type { Page } from '@playwright/test'
import { test, expect, login } from './fixtures'

async function latestSnapshotId(page: Page, portfolioId: number): Promise<number> {
  const snapshotId = await page.evaluate(async (id) => {
    const token = localStorage.getItem('advisor_v2_access_token')
    const response = await fetch(`/api/v2/portfolios/${id}/snapshots`, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    })
    if (!response.ok) throw new Error(`snapshot list failed: ${response.status}`)
    const rows = await response.json() as Array<{ id: number; status: string }>
    return rows.find((item) => item.status === 'confirmed')?.id || null
  }, portfolioId)
  expect(snapshotId).not.toBeNull()
  return snapshotId as number
}

async function createRetryJob(
  page: Page,
  portfolioId: number,
  snapshotId: number,
  checkpoint: string,
): Promise<number> {
  const jobId = await page.evaluate(async ({ portfolio, snapshot, marker }) => {
    const token = localStorage.getItem('advisor_v2_access_token')
    const response = await fetch('/api/v2/analysis/jobs', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        snapshot_id: snapshot,
        mode: 'fast',
        checkpoint: marker,
        notify: false,
      }),
    })
    if (!response.ok) throw new Error(`analysis job create failed: ${response.status}`)
    const row = await response.json() as { id: number; portfolio_id: number }
    return row.id
  }, { portfolio: portfolioId, snapshot: snapshotId, marker: checkpoint })
  expect(jobId).toBeGreaterThan(0)
  return jobId
}

async function openJobInDrawer(page: Page, portfolioId: number, jobId: number): Promise<void> {
  await page.goto(`/upload?portfolio=${portfolioId}&job=${jobId}&focus=analysis`)
  await expect(page).toHaveURL(/\/holdings\?/)
  const drawer = page.locator('.n-drawer').last()
  await expect(drawer).toBeVisible()
  await expect(drawer.locator('.job-status')).toBeVisible({ timeout: 30_000 })
}

test('Analysis structured retry succeeds after one malformed/truncated response', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const portfolioId = facts.portfolios.action
  const snapshotId = await latestSnapshotId(page, portfolioId)
  const jobId = await createRetryJob(page, portfolioId, snapshotId, 'retry-success')
  await openJobInDrawer(page, portfolioId, jobId)

  const drawer = page.locator('.n-drawer').last()
  await expect(drawer.getByText('分析完成', { exact: true })).toBeVisible({ timeout: 60_000 })
  await expect(drawer.getByRole('button', { name: '查看今日分析', exact: true })).toBeVisible()
  await expect(page.locator('body')).not.toContainText('acceptance truncation fixture')
  await expect(page.locator('body')).not.toContainText('模型没有返回有效 JSON')
})

test('Analysis structured retry exhaustion shows a safe error and retry action', async ({ acceptancePage: page, facts }) => {
  await login(page, facts.users.a)
  const portfolioId = facts.portfolios.action
  const snapshotId = await latestSnapshotId(page, portfolioId)
  const jobId = await createRetryJob(page, portfolioId, snapshotId, 'retry-exhausted')
  await openJobInDrawer(page, portfolioId, jobId)

  const drawer = page.locator('.n-drawer').last()
  await expect(drawer.getByText(/分析暂时失败/)).toBeVisible({ timeout: 60_000 })
  await expect(drawer.getByRole('button', { name: '重新分析', exact: true })).toBeVisible()
  await expect(page.locator('body')).not.toContainText('acceptance truncation fixture')
  await expect(page.locator('body')).not.toContainText('bull_claims')
})
