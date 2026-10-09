import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'

async function mockHome(page: Page, options: {
  complete?: boolean; today?: boolean; older?: boolean; riskUnknown?: boolean;
  medianHistory?: boolean; pendingMemory?: boolean; portfolioReturns?: boolean;
  missingCostBasis?: boolean; importedPnlOnly?: boolean;
} = {}) {
  await page.addInitScript(() => localStorage.setItem('advisor_v2_access_token', 'home-regression'))
  const reportDate = options.older ? '2026-10-06' : options.today ? '2026-10-09' : '2026-10-08'
  const analysis = {
    analysis_run_id: 3, portfolio_snapshot_id: 10, status: 'SUCCESS', mode: 'standard',
    finished_at: `${reportDate}T07:10:00`, portfolio_action: 'NO_ACTION', final_rating: 'NO_ACTION',
    quality: 'A', validity_status: options.today ? 'VALID' : 'EXPIRED',
    decision_status: options.today ? 'NO_ACTION' : 'EXPIRED', report_date: reportDate, is_historical: !options.today,
  }
  const indices = ['000001.SH', '399001.SZ', '399006.SZ', '000300.SH', '000852.SH', '000688.SH'].map(code => ({
    code, last: 3000, change: 0, change_pct: 0, status: 'available', quality: 'DEGRADED',
  }))
  const median = {
    status: 'available', current_value: 989.899, daily_median_return: -0.010101,
    trend_20d: options.medianHistory ? -0.025 : null, percentile_250d: options.medianHistory ? 62.5 : null,
  }
  const risk = { risk_level: options.riskUnknown ? 'UNKNOWN' : 'HIGH', risk_score: options.riskUnknown ? null : 70, risk_factors: ['BREADTH_WEAK', 'DATA_DEGRADED', 'MEDIAN_STOCK_WEAK'] }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    let body: unknown = []
    if (path === '/api/v2/portfolios') body = [{ id: 1, name: '首页回归组合', is_default: true, latest_snapshot_id: 10 }]
    else if (path.endsWith('/dashboard/today')) body = {
      as_of: '2026-10-09T09:00:00+08:00', trade_date: '2026-10-09',
      portfolio: {
        status: 'DEGRADED', snapshot_id: 10, total_assets: 180289.5, market_value: 180289.5,
        spendable_cash: 0, gross_exposure: 1, cash_ratio: 0,
        day_return: options.portfolioReturns ? 0 : null,
        floating_pnl: options.missingCostBasis || options.importedPnlOnly ? null : options.portfolioReturns ? 0 : -41493.3,
        imported_pnl_amount: options.missingCostBasis ? null : -41493.3,
        imported_cost_basis: options.missingCostBasis ? null : 221798.6,
        pnl_is_realtime: Boolean(options.portfolioReturns),
      },
      market: {}, candidates: {}, triggers: {}, data_health: {},
      analysis: { status: 'AVAILABLE', latest: options.today ? analysis : null, last_analysis: analysis, analysis_in_progress: false },
      decisions: { status: 'MISSING', latest: null },
      timeline: { as_of: options.complete ? '2026-10-09T21:00:00+08:00' : '2026-10-09T09:00:00+08:00', timeline: [
        { key: 'maintenance', label: 'Data Maintenance', kind: 'maintenance', time: '08:45', scheduled_at: '2026-10-09T08:45:00+08:00', is_current: true, status: options.complete ? 'SUCCESS' : 'RUNNING' },
        { key: 'pre_market', label: 'Pre-market Snapshot', kind: 'snapshot', time: '09:20', scheduled_at: '2026-10-09T09:20:00+08:00', status: options.complete ? 'SUCCESS' : 'PENDING' },
        { key: '09:35', label: 'Standard Analysis', kind: 'analysis', time: '09:35', scheduled_at: '2026-10-09T09:35:00+08:00', status: options.complete ? 'SUCCESS' : 'PENDING' },
      ] },
      notifications: { items: [] },
    }
    else if (path.endsWith('/market/session')) body = { session: 'PRE_OPEN', data_basis: 'previous_session_close', trading_date: '2026-10-09', previous_trading_date: '2026-10-08' }
    else if (path.endsWith('/market/major-indices')) body = indices
    else if (path.endsWith('/market/systemic-risk')) body = risk
    else if (path.endsWith('/market/overview')) body = { major_indices: indices, systemic_risk: risk, all_a_median: median }
    else if (path === '/api/v3/triggers/daily-plan') body = {
      as_of: '2026-10-09T09:00:00+08:00', decision_status: 'EXPIRED', summary: { EXPIRED: 7 },
      actions: Array.from({ length: 7 }, (_, index) => ({
        plan_id: index + 1, code: `60000${index}`, name: `历史持仓${index}`, action: 'hold', decision_status: 'EXPIRED',
        planned_quantity: null, remaining_quantity: null, executable_quantity: 0,
        condition: { metric: 'price', operator: 'LT', threshold: 2.95 },
        rationale: '分析师具体理由：量价结构偏弱，等待风险释放后再评估。',
        reason_codes: ['PLAN_VALIDITY_EXPIRED'], expires_at: '2026-10-08T15:00:00+08:00',
      })),
    }
    else if (path.endsWith('/memory/performance')) body = {
      as_of: '2026-10-09T01:00:00+00:00', trade_date: '2026-10-09', previous_trade_date: '2026-10-08',
      account_return: { status: 'INCOMPLETE', reason_codes: ['CLOSE_SNAPSHOT_PAIR_MISSING', 'COST_OR_RAW_MARK_MISSING', 'CLOSE_SNAPSHOT_PAIR_MISSING'] },
      day_comparison: options.pendingMemory
        ? { today_status: 'PENDING_ANALYSIS', today: [], yesterday: [{ code: '600000', action: 'hold', quantity: null }], changes: [] }
        : { today: [], changes: [{ code: '600000', previous_action: 'hold', action: null, previous_quantity: null, quantity: null }] },
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.goto('/dashboard')
}

test('首页显示历史成功报告，今日行动保持待分析，指标准确汉化', async ({ acceptancePage: page }) => {
  await mockHome(page)
  const latest = page.getByTestId('v3-latest-analysis')
  await expect(latest).toContainText('上一交易日报告')
  await expect(latest).toContainText('2026-10-08 15:10:00 北京时间')
  await expect(latest).toContainText('无需操作')
  await expect(latest).toContainText('建议有效至昨日收盘')
  await expect(page.getByTestId('v3-decision-hero')).toContainText('暂无今日计划')
  await expect(page.getByTestId('v3-action-list')).toContainText('最新分析为 2026-10-08 报告，结论无需操作')
  await expect(page.getByTestId('v3-portfolio-exposure')).toContainText('100.0%')
  await expect(page.getByTestId('v3-portfolio-snapshot')).toContainText('0.0%')
  await expect(page.getByTestId('v3-portfolio-day-return')).toContainText('待产生相邻交易日收盘快照对比')
  await expect(page.getByTestId('v3-portfolio-day-return')).not.toContainText('成本')
  await expect(page.getByTestId('v3-portfolio-floating-pnl')).toContainText('-¥41,493.30')
  await expect(page.getByTestId('v3-portfolio-floating-pnl')).toContainText('导入快照口径 (非实时)')
  await expect(page.getByTestId('v3-portfolio-floating-pnl')).not.toContainText('待记录持仓成本基准')
  await expect(page.getByTestId('v3-all-a-median')).toHaveText('-1.01%')
  await expect(page.getByTestId('v3-all-a-20d')).toContainText('历史积累中')
  await expect(page.getByTestId('v3-all-a-250d')).toContainText('历史积累中')
  await expect(page.getByTestId('v3-risk-factors')).toContainText('涨跌家数偏弱')
  await expect(page.getByTestId('v3-risk-factors')).toContainText('数据源降级')
  await expect(page.getByTestId('v3-risk-factors')).not.toContainText('BREADTH_WEAK')
  await expect(page.getByTestId('v3-next-checkpoint')).toContainText('盘前快照')
  await expect(page.getByTestId('v3-next-checkpoint')).toContainText('09:20')
  await expect(page.getByTestId('v3-next-checkpoint')).not.toContainText('08:45')
})

test('七项过期计划显示中文条件、完整理由与不调整股数，复盘去重', async ({ acceptancePage: page }) => {
  await mockHome(page)
  const card = page.locator('.section-card').filter({ has: page.getByRole('heading', { name: '今日行动计划', exact: true }) })
  await expect(card).toContainText('暂无今日有效行动计划（以下为上一交易日 2026-10-08 历史计划，已过有效期并阻断执行）')
  await expect(card.locator('.plan-row')).toHaveCount(7)
  const row = card.locator('.plan-row').first()
  await expect(row).toContainText('价格 < 2.95 元')
  await expect(row).toContainText('维持现有持仓（无需调整股数）')
  await expect(row).toContainText('分析师具体理由：量价结构偏弱，等待风险释放后再评估。')
  await expect(row).toContainText('计划已过有效期')
  await expect(row).not.toContainText('PLAN_VALIDITY_EXPIRED')
  await expect(row).not.toContainText('计划 — 股')
  await expect(row).not.toContainText('"threshold"')
  const review = page.locator('.section-card').filter({ has: page.getByRole('heading', { name: '昨日执行与今日变化', exact: true }) })
  await expect(review.locator('.review-note')).toHaveText(/缺少相邻收盘快照对比 · 缺少成本基准或原始行情盯市数据$/)
  await expect(review).toContainText('昨日结论：持有（今日待新一轮分析）')
  await expect(review).toContainText('维持持仓')
  await expect(review).not.toContainText('— → — 股')
})

test('今日检查点全部结束后展示完成说明', async ({ acceptancePage: page }) => {
  await mockHome(page, { complete: true })
  await expect(page.getByTestId('v3-important-events')).toContainText('今日常规检查点已全部完成')
  await expect(page.getByTestId('v3-next-checkpoint')).toHaveCount(0)
})

test('今日成功分析正常作为今日结论展示', async ({ acceptancePage: page }) => {
  await mockHome(page, { today: true })
  await expect(page.getByTestId('v3-decision-title')).toContainText('今日无需操作')
  await expect(page.getByTestId('v3-latest-analysis')).not.toContainText('上一交易日报告')
})

test('更早报告明确标注报告日期，避免误称上一交易日', async ({ acceptancePage: page }) => {
  await mockHome(page, { older: true })
  await expect(page.getByTestId('v3-latest-analysis')).toContainText('历史报告')
  await expect(page.getByTestId('v3-latest-analysis')).not.toContainText('上一交易日报告')
  await expect(page.getByTestId('v3-latest-analysis')).toContainText('2026-10-06')
})

test('盘前风险待评分有明确说明，已有历史趋势按比例显示', async ({ acceptancePage: page }) => {
  await mockHome(page, { riskUnknown: true, medianHistory: true })
  await expect(page.getByTestId('v3-systemic-risk-panel')).toContainText('盘前时段尚未生成当日风险评分（参考上一交易日市场表现）')
  await expect(page.getByTestId('v3-all-a-20d')).toHaveText('-2.50%')
  await expect(page.getByTestId('v3-all-a-250d')).toHaveText('62.5%')
})

test('持仓无成本与导入盈亏时，明确提示待记录成本基准', async ({ acceptancePage: page }) => {
  await mockHome(page, { missingCostBasis: true })
  const floatingPnl = page.getByTestId('v3-portfolio-floating-pnl')
  await expect(floatingPnl.locator('.v3-metric__value')).toHaveText('—')
  await expect(floatingPnl).toContainText('待记录持仓成本基准')
  await expect(floatingPnl).not.toContainText('导入快照口径 (非实时)')
  await expect(page.getByTestId('v3-portfolio-day-return')).toContainText('待产生相邻交易日收盘快照对比')
})

test('仅有导入盈亏时，首页仍显示快照金额与非实时说明', async ({ acceptancePage: page }) => {
  await mockHome(page, { importedPnlOnly: true })
  const floatingPnl = page.getByTestId('v3-portfolio-floating-pnl')
  await expect(floatingPnl).toContainText('-¥41,493.30')
  await expect(floatingPnl).toContainText('导入快照口径 (非实时)')
  await expect(floatingPnl).not.toContainText('待记录持仓成本基准')
})

test('账户收益已补齐时，实时零收益保留并去掉缺失与导入快照提示', async ({ acceptancePage: page }) => {
  await mockHome(page, { portfolioReturns: true })
  await expect(page.getByTestId('v3-portfolio-day-return')).toContainText('0.00%')
  await expect(page.getByTestId('v3-portfolio-day-return')).not.toContainText('待产生相邻交易日收盘快照对比')
  await expect(page.getByTestId('v3-portfolio-floating-pnl')).toContainText('¥0.00')
  await expect(page.getByTestId('v3-portfolio-floating-pnl')).not.toContainText('待记录持仓成本基准')
  await expect(page.getByTestId('v3-portfolio-floating-pnl')).not.toContainText('导入快照口径 (非实时)')
})

test.describe('北京时间与待分析复盘衔接', () => {
  test.use({ timezoneId: 'Asia/Shanghai' })

  test('UTC时间显示北京时间，今日无新决策时只展示昨日结论', async ({ acceptancePage: page }) => {
    await mockHome(page, { pendingMemory: true })
    await expect(page.getByTestId('v3-latest-analysis')).toContainText('2026-10-08 15:10:00 北京时间')
    const review = page.locator('.section-card').filter({ has: page.getByRole('heading', { name: '昨日执行与今日变化', exact: true }) })
    await expect(review).toContainText('2026-10-09 09:00:00 北京时间')
    await expect(review).toContainText('昨日结论：持有（今日待新一轮分析）')
    await expect(review).toContainText('维持持仓')
    await expect(review).not.toContainText('持有 →')
    await expect(review).not.toContainText('— → — 股')
  })
})
