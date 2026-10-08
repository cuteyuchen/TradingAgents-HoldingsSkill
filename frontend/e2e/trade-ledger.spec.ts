import { expect, test } from './fixtures'

const snapshot = {
  id: 10,
  portfolio_id: 1,
  source: 'screenshot',
  snapshot_time: '2026-10-02T02:30:00Z',
  status: 'confirmed',
  total_assets: 30_000,
  total_market_value: 10_000,
  broker_available_cash: 20_000,
  corrected_unused_funds: null,
  repo_or_standard_bond_value: null,
  excluded_items: [],
  notes: [],
  holdings: [{
    code: '600519', canonical_code: null, name: '贵州茅台', resolution_status: 'RESOLVED',
    qty: 1000, available_qty: 1000, cost: 10, price: 10, market_value: 10_000, weight: 0.333,
  }],
}

const sellEntry = {
  id: 41, portfolio_id: 1, entry_type: 'TRADE', security_code: '600519', security_name: '贵州茅台',
  side: 'SELL', quantity: 300, price: 10, gross_amount: 3000, fees: 5, taxes: 0, net_amount: 2995,
  currency: 'CNY', executed_at: '2026-10-02T02:35:00Z', trade_date: '2026-10-02', available_at: '2026-10-02T02:35:00Z',
  source: 'MANUAL', source_ref: null, broker_order_id: null, status: 'CONFIRMED', notes: null,
  analysis_run_id: null, trigger_event_id: null, created_at: '2026-10-02T02:35:00Z', updated_at: '2026-10-02T02:35:00Z',
}

const buyEntry = {
  ...sellEntry, id: 42, side: 'BUY', quantity: 100, price: 9, gross_amount: 900, fees: 5, taxes: 0,
  net_amount: 905, broker_order_id: 'LEDGER-BUY-1',
}

function accountState(entryCount: number, cash: number, qty: number) {
  return {
    portfolio_id: 1, snapshot_id: 10, snapshot_time: snapshot.snapshot_time, as_of: '2026-10-02T06:00:00Z',
    cash, pending_sell_proceeds: 2995, frozen_cash: null,
    account_version: `portfolio-account-v1:10:${entryCount}`,
    account_derivation: { version: 'portfolio-account-v1', snapshot_id: 10, entry_count: entryCount, applied_entry_ids: entryCount ? [41, ...(entryCount > 1 ? [42] : [])] : [], cash_delta: -905, flags: [] },
    positions: [{ code: '600519', name: '贵州茅台', qty, available_qty: qty, qty_delta: qty - 1000, source: entryCount ? 'snapshot+ledger' : 'snapshot', flags: [] }],
  }
}

function ledgerRoutes(page: import('@playwright/test').Page) {
  const posted: Record<string, any>[] = []
  let entryCount = 1
  return page.route(/\/api\/v[23]\//, async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    let body: unknown = {}
    if (path === '/api/v2/portfolios') body = [{ id: 1, name: 'Ledger Fixture', is_default: true, latest_snapshot_id: 10 }]
    else if (path === '/api/v2/snapshots/10') body = snapshot
    else if (path === '/api/v3/portfolios/1/state') body = accountState(entryCount, entryCount > 1 ? 19_095 : 20_000, entryCount > 1 ? 800 : 700)
    else if (path === '/api/v3/portfolios/1/ledger' && method === 'GET') body = entryCount > 1 ? [buyEntry, sellEntry] : [sellEntry]
    else if (path === '/api/v3/portfolios/1/ledger' && method === 'POST') {
      posted.push(request.postDataJSON())
      entryCount = 2
      body = buyEntry
    } else if (path === '/api/v3/fuyao/portfolios/1/contribution') {
      body = { portfolio_id: 1, snapshot_id: 10, snapshot_time: snapshot.snapshot_time, confirmed: true, items: [], requested_count: 1, quoted_count: 0, coverage: 0, missing_quote_count: 1, quality_status: 'MISSING' }
    } else if (path === '/api/v3/portfolios/1/dashboard/today') {
      body = {
        as_of: '2026-10-02T06:00:00Z', trade_date: '2026-10-02', market_open: true,
        portfolio: { snapshot_id: 10, freshness: 'FRESH' }, market: {}, candidates: {}, analysis: {}, decisions: {},
        executions: {}, triggers: {}, memory: {}, data_health: {}, notifications: {},
      }
    } else if (path.endsWith('/providers') || path.endsWith('/profiles') || path === '/api/v2/analysis/runs') body = []
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  }).then(() => posted)
}

test('recorded trades update the current account and stay visible on the holdings page', async ({ acceptancePage: page }) => {
  await page.addInitScript(() => localStorage.setItem('advisor_v2_access_token', 'trade-ledger-fixture'))
  const routeReady = ledgerRoutes(page)
  await page.goto('/holdings')
  await expect(page.getByRole('heading', { name: '我的持仓' })).toBeVisible()
  await expect(page.getByTestId('v3-account-derived')).toContainText('1 条账户记录')
  await expect(page.getByTestId('v3-holdings-table')).toContainText('700')

  await page.getByTestId('v3-holdings-trades').click()
  await expect(page.getByText('已记录事实')).toBeVisible()
  await expect(page.getByText('卖出 贵州茅台')).toBeVisible()

  await page.getByText('手动录入', { exact: true }).click()
  await page.getByPlaceholder('如 600519').fill('600519')
  await page.getByPlaceholder('股数').fill('100')
  await page.getByPlaceholder('价格').fill('9')
  await page.getByPlaceholder('手续费').fill('5')
  await page.getByRole('button', { name: '记录这笔事实' }).click()
  await expect(page.locator('.n-message')).toContainText('已记录')

  const posted = await routeReady
  expect(posted).toHaveLength(1)
  expect(posted[0]).toMatchObject({ entry_type: 'TRADE', security_code: '600519', side: 'BUY', quantity: 100, price: 9, fees: 5, source: 'MANUAL' })
  await expect(page.getByTestId('v3-account-derived')).toContainText('2 条账户记录')
  await expect(page.getByTestId('v3-holdings-table')).toContainText('800')
})

test('statement import previews mapping, duplicates and commits ready rows only', async ({ acceptancePage: page }) => {
  await page.addInitScript(() => localStorage.setItem('advisor_v2_access_token', 'trade-ledger-import-fixture'))
  const committed: Record<string, any>[] = []
  await page.route(/\/api\/v[23]\//, async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    let body: unknown = {}
    if (path === '/api/v2/portfolios') body = [{ id: 1, name: 'Ledger Fixture', is_default: true, latest_snapshot_id: 10 }]
    else if (path === '/api/v2/snapshots/10') body = snapshot
    else if (path === '/api/v3/portfolios/1/state') body = accountState(1, 20_000, 700)
    else if (path === '/api/v3/portfolios/1/ledger' && method === 'GET') body = [sellEntry]
    else if (path === '/api/v3/portfolios/1/ledger/import/preview') {
      body = {
        source_ref: 'preview-ref-1', encoding: 'utf-8-sig', delimiter: ',',
        headers: ['成交日期', '证券代码', '买卖方向', '成交数量', '成交价格'],
        mapping: { trade_date: '成交日期', security_code: '证券代码', side: '买卖方向', quantity: '成交数量', price: '成交价格' },
        mapping_issues: [],
        rows: [
          { row_number: 2, status: 'DUPLICATE', raw: {}, normalized: { entry_type: 'TRADE', security_code: '600519', side: 'SELL' }, issues: [], duplicate_kind: 'LEDGER_ORDER_ID', duplicate_of_entry_id: 41 },
          { row_number: 3, status: 'READY', raw: {}, normalized: { entry_type: 'TRADE', security_code: '600519', side: 'BUY', quantity: 100, price: 9, executed_at: '2026-10-02T07:00:00+00:00', trade_date: '2026-10-02' }, issues: [], duplicate_kind: null, duplicate_of_entry_id: null },
        ],
        summary: { total: 2, ready: 1, duplicates: 1, invalid: 0 },
      }
    } else if (path === '/api/v3/portfolios/1/ledger/import') {
      committed.push(request.postDataJSON())
      body = { source_ref: 'preview-ref-1', created_entry_ids: [43], skipped_entry_ids: [], errors: [], created: 1, skipped: 0 }
    } else if (path === '/api/v3/fuyao/portfolios/1/contribution') {
      body = { portfolio_id: 1, snapshot_id: 10, snapshot_time: snapshot.snapshot_time, confirmed: true, items: [], requested_count: 1, quoted_count: 0, coverage: 0, missing_quote_count: 1, quality_status: 'MISSING' }
    } else if (path === '/api/v3/portfolios/1/dashboard/today') {
      body = {
        as_of: '2026-10-02T06:00:00Z', trade_date: '2026-10-02', market_open: true,
        portfolio: { snapshot_id: 10, freshness: 'FRESH' }, market: {}, candidates: {}, analysis: {}, decisions: {},
        executions: {}, triggers: {}, memory: {}, data_health: {}, notifications: {},
      }
    } else if (path.endsWith('/providers') || path.endsWith('/profiles') || path === '/api/v2/analysis/runs') body = []
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })

  await page.goto('/holdings')
  await page.getByTestId('v3-holdings-trades').click()
  await page.getByText('导入流水', { exact: true }).click()
  await page.locator('.import-drop input[type="file"]').setInputFiles({
    name: 'trades.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('成交日期,证券代码,买卖方向,成交数量,成交价格\n2026-10-02,600519,买入,100,9\n'),
  })
  await page.getByRole('button', { name: '预览解析结果' }).click()
  await expect(page.locator('.import-summary')).toContainText('可用 1')
  await expect(page.locator('.import-summary')).toContainText('重复 1')
  await expect(page.locator('.import-row.duplicate')).toContainText('重复（已有记录）')

  await page.getByRole('button', { name: '导入 1 条可用记录' }).click()
  await expect(page.getByRole('alert').filter({ hasText: '已写入' })).toContainText('已写入 1 条')
  expect(committed).toHaveLength(1)
  expect(committed[0].source_ref).toBe('preview-ref-1')
  expect(committed[0].rows).toHaveLength(1)
  expect(committed[0].rows[0]).toMatchObject({ security_code: '600519', side: 'BUY', quantity: 100 })
})
