import { test, expect } from '@playwright/test'
import { actionLabel, ratingLabel, analystLabel, candidateStageLabel, ledgerSourceLabel, ledgerEntryTypeLabel, riskLevelLabel } from '../src/utils/ui'
import { businessLabel } from '../src/utils/businessLocale'

test('模拟页状态徽章显示中文，保留原始状态，并对未知状态安全兜底', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('advisor_v2_access_token', 'locale-fixture'))
  const account = { id: 1, name: '中文状态验证账户', portfolio_id: 1, status: 'FUTURE_UNKNOWN_STATUS', shadow_generation: 1, current_cash: 100000 }
  await page.route('**/api/v*/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    const responses: Record<string, unknown> = {
      '/api/v2/auth/me': { id: 1, email: 'locale@example.com' },
      '/api/v2/portfolios': [{ id: 1, name: '中文测试组合', is_default: true, latest_snapshot_id: 1 }],
      '/api/v3/fuyao/status': { configured: false, capabilities: {} },
      '/api/v3/shadow/accounts': [account],
      '/api/v3/shadow/accounts/1': account,
      '/api/v3/shadow/performance': { sample_days: 1, performance_quality: 'DATA_GAP', snapshots: [] },
      '/api/v3/shadow/validation': { live_sample_days: 1, decision_count: 0, cohorts: [] },
      '/api/v3/shadow/decisions': [],
      '/api/v3/shadow/orders': [{ id: 1, code: '600519.SH', side: 'BUY', status: ' succeeded ' }],
      '/api/v3/shadow/fills': [],
      '/api/v3/shadow/daily': [],
    }
    await route.fulfill({ json: pathname.startsWith('/api/v3/system/') ? { status: 'OK', components: {} } : responses[pathname] ?? {} })
  })
  await page.goto('/simulation?portfolio=1')
  await expect(page.locator('[data-status="FUTURE_UNKNOWN_STATUS"]')).toHaveText('待确认')
  await expect(page.locator('[data-status="SUCCEEDED"]')).toHaveText('成功')
  await expect(page.locator('.execution-panel')).toContainText('买入 600519.SH')
  await expect(page.locator('.performance-note')).toContainText('数据不足')
  await expect(page.locator('.workbench-page')).not.toContainText('FUTURE_UNKNOWN_STATUS')
})

test('决策、候选、交易流水和研究枚举采用统一中文口径', () => {
  expect([
    actionLabel('CONDITIONAL_BUY'), ratingLabel('Overweight'),
    analystLabel('technical/VPA'), candidateStageLabel('BLOCKED'),
    ledgerSourceLabel('CSV_IMPORT'), ledgerEntryTypeLabel('CASH_IN'),
    riskLevelLabel('EXTREME'), businessLabel('DETERMINISTIC_RECOMPUTE'),
    businessLabel('HARD_CAP_BREACH:600519.SH'),
  ]).toEqual(['条件买入', '增配', '技术／量价分析', '门禁受阻', '流水导入', '现金转入', '极高风险', '确定性重算', '超过仓位硬上限：600519.SH'])
  expect(businessLabel('0-20')).toBe('0-20')
  expect(businessLabel('09:30 开盘检查')).toBe('09:30 开盘检查')
})

test('中文组件在小屏、中屏和深色模式下保持表格滚动与数字完整', async ({ page }) => {
  await page.goto('/v3/foundation')
  await expect(page.getByRole('heading', { name: '组件示例' })).toBeVisible()
  await page.getByRole('button', { name: '深色', exact: true }).click({ force: true })
  await expect(page.getByTestId('v3-resolved-theme')).toHaveText('深色')
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    const layout = await page.evaluate(() => {
      const scroller = document.querySelector('.q-table__middle') as HTMLElement
      const metric = document.querySelector('.v3-metric__value') as HTMLElement
      return {
        overflow: document.documentElement.scrollWidth - window.innerWidth,
        tableScroll: getComputedStyle(scroller).overflowX,
        numericWhitespace: getComputedStyle(metric).whiteSpace,
      }
    })
    expect(layout.overflow).toBeLessThanOrEqual(1)
    expect(layout.tableScroll).toBe('auto')
    expect(layout.numericWhitespace).toBe('nowrap')
  }
})
