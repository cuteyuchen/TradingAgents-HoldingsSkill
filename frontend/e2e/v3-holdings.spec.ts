/**
 * V3 Holdings Workstation acceptance.
 * Fully deterministic via page.route() — no live market providers.
 */
import type { Page } from '@playwright/test'
import { test, expect, allowExpectedHttpError } from './fixtures'

const TRADE_DATE = '2026-09-11'

function sessionPayload(kind = 'MORNING') {
  return {
    market: 'CN',
    timezone: 'Asia/Shanghai',
    session: kind,
    is_trading_day: true,
    is_market_open: kind === 'MORNING' || kind === 'AFTERNOON',
    trading_date: TRADE_DATE,
    resolved_at: `${TRADE_DATE}T10:00:00+08:00`,
    data_status: 'OK',
    data_basis: 'live',
    display_label: kind,
  }
}

function quoteItem(code: string, last: number, changePct: number, quality = 'A') {
  const stale = quality === 'STALE'
  return {
    code,
    instrument: null,
    last,
    change: Number((last * changePct * 0.01).toFixed(2)),
    change_pct: changePct,
    prev_close: Number((last / (1 + changePct / 100)).toFixed(3)),
    open: last,
    high: last * 1.01,
    low: last * 0.99,
    volume: 1000,
    turnover: 1e8,
    observed_at: `${TRADE_DATE}T14:31:05+08:00`,
    fetched_at: `${TRADE_DATE}T14:31:06+08:00`,
    provider: 'acceptance',
    provider_profile: 'acceptance',
    source: 'acceptance',
    fallback: false,
    quality: stale ? 'STALE' : quality,
    quality_flags: stale ? ['STALE'] : [],
    error_code: null,
    status: stale ? 'stale' : 'available',
    data_basis: 'live',
    trading_date: TRADE_DATE,
    volume_unit: 'shares',
    turnover_unit: 'CNY',
    amplitude_pct: 1.2,
    turnover_rate: 0.5,
  }
}

function holdingRow(overrides: Record<string, unknown>) {
  return {
    code: '600519.SH',
    name: '贵州茅台',
    qty: 100,
    available_qty: 100,
    price: 1680,
    market_value: 168000,
    weight: 0.28,
    pnl_ratio: 0.12,
    quote_quality: 'A',
    keep_score: 80,
    opportunity_reference: null,
    hard_cap: 0.3,
    headroom: 0.02,
    holding_action: null,
    risk_flags: [],
    ...overrides,
  }
}

function snapshotPayload(options: {
  id: number
  holdings?: Array<Record<string, unknown>>
  identityStatus?: string
  totalAssets?: number
}) {
  return {
    id: options.id,
    portfolio_id: 1,
    upload_id: 9,
    source: 'upload',
    snapshot_time: `${TRADE_DATE}T09:01:12+08:00`,
    status: 'confirmed',
    identity_status: options.identityStatus || 'RESOLVED',
    identity_issues: [],
    holdings: options.holdings || [
      {
        code: '600519.SH',
        canonical_code: '600519.SH',
        name: '贵州茅台',
        display_name: '贵州茅台',
        asset_type: 'STOCK',
        security_id: 1,
        resolution_status: 'RESOLVED',
        qty: 100,
        available_qty: 100,
        cost: 1500,
        price: 1680,
        market_value: 168000,
        pnl: 0.12,
        pnl_amount: 18000,
        weight: 0.28,
        extra: {},
      },
      {
        code: '510300.SH',
        canonical_code: '510300.SH',
        name: '沪深300ETF',
        display_name: '沪深300ETF',
        asset_type: 'ETF',
        security_id: 2,
        resolution_status: 'RESOLVED',
        qty: 1000,
        available_qty: 0,
        cost: 3.8,
        price: 4.0,
        market_value: 4000,
        pnl: 0.05,
        pnl_amount: 200,
        weight: 0.08,
        extra: {},
      },
      {
        code: '000001',
        canonical_code: null,
        name: '未知标的',
        display_name: '未知标的',
        asset_type: null,
        security_id: null,
        resolution_status: 'UNRESOLVED',
        qty: 10,
        available_qty: 10,
        cost: 10,
        price: null,
        market_value: null,
        pnl: null,
        pnl_amount: null,
        weight: null,
        extra: {},
      },
    ],
    total_assets: options.totalAssets ?? 500000,
    total_market_value: 172000,
    broker_available_cash: 128000,
    corrected_unused_funds: 128000,
    excluded_items: [],
    notes: [],
  }
}

function dashboardPayload(options: {
  decision?: Record<string, unknown> | null
  holdings?: Array<Record<string, unknown>>
  riskFlags?: string[]
} = {}) {
  const decision = options.decision ?? null
  return {
    as_of: `${TRADE_DATE}T14:30:00+08:00`,
    trade_date: TRADE_DATE,
    market_open: true,
    market_session: sessionPayload('MORNING'),
    quote_as_of: `${TRADE_DATE}T14:31:05+08:00`,
    strategy_analysis_at: decision?.decision_at || `${TRADE_DATE}T13:05:42+08:00`,
    workflow_state: 'MORNING_SESSION',
    market: { status: 'AVAILABLE' },
    portfolio: {
      status: 'AVAILABLE',
      quality_status: 'VALID',
      freshness: 'FRESH',
      snapshot_id: 100,
      snapshot_time: `${TRADE_DATE}T09:01:12+08:00`,
      total_assets: 500000,
      market_value: 172000,
      spendable_cash: 128000,
      cash_ratio: 0.256,
      gross_exposure: 0.344,
      position_count: 3,
      risk_flags: options.riskFlags || [],
      hard_cap_breaches: [],
      holdings: options.holdings || [
        holdingRow({ code: '600519.SH', holding_action: null }),
        holdingRow({ code: '510300.SH', holding_action: null, weight: 0.08 }),
      ],
    },
    candidates: { status: 'AVAILABLE' },
    triggers: { status: 'AVAILABLE' },
    analysis: { status: decision ? 'AVAILABLE' : 'MISSING', latest: null, jobs: [], analysis_in_progress: false },
    decisions: {
      status: decision ? 'AVAILABLE' : 'MISSING',
      latest: decision,
      today_count: decision ? 1 : 0,
      final_action: decision?.conclusion || 'NO_ACTION',
    },
    executions: { status: 'AVAILABLE', items: [] },
    memory: { status: 'MISSING' },
    data_health: { status: 'OK', overall: 'OK', components: [] },
    timeline: { as_of: `${TRADE_DATE}T14:30:00+08:00`, trade_date: TRADE_DATE, workflow_state: 'MORNING_SESSION', monitor: {}, timeline: [] },
    notifications: { items: [], count: 0, total_count: 0, unread_count: 0, critical_count: 0 },
  }
}

async function mockAuth(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('advisor_v2_access_token', 'acceptance-v3-holdings')
    localStorage.setItem('advisor_v2_refresh_token', 'acceptance-v3-holdings-refresh')
  })
  await page.route('**/api/v2/auth/me**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 1,
        email: 'a@example.com',
        username: 'a',
        status: 'active',
        timezone: 'Asia/Shanghai',
        created_at: `${TRADE_DATE}T00:00:00+08:00`,
      }),
    }),
  )
  await page.route('**/api/v3/system/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'OK', ready: true, components: {}, as_of: `${TRADE_DATE}T00:00:00+08:00` }) }),
  )
  await page.route('**/api/v3/fuyao/status*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ provider: 'fuyao', configured: false, connection_status: '未配置', capabilities: {} }),
    }),
  )
}

async function mockPortfolios(page: Page, snapshots: Record<number, number | null> = { 1: 100, 2: 200 }): Promise<void> {
  await page.route('**/api/v2/portfolios**', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 1,
          name: '组合A',
          market: 'CN',
          currency: 'CNY',
          is_default: true,
          latest_snapshot_id: snapshots[1] ?? null,
          latest_snapshot_time: snapshots[1] ? `${TRADE_DATE}T09:01:12+08:00` : null,
          created_at: `${TRADE_DATE}T00:00:00+08:00`,
          updated_at: `${TRADE_DATE}T00:00:00+08:00`,
        },
        {
          id: 2,
          name: '组合B',
          market: 'CN',
          currency: 'CNY',
          is_default: false,
          latest_snapshot_id: snapshots[2] ?? null,
          latest_snapshot_time: snapshots[2] ? `${TRADE_DATE}T08:30:00+08:00` : null,
          created_at: `${TRADE_DATE}T00:00:00+08:00`,
          updated_at: `${TRADE_DATE}T00:00:00+08:00`,
        },
      ]),
    })
  })
}

async function mockSession(page: Page, kind = 'MORNING'): Promise<void> {
  await page.route('**/api/v3/market/session**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sessionPayload(kind)) }),
  )
}

async function mockSnapshot(page: Page, id: number, payload: Record<string, unknown> | null, status = 200): Promise<void> {
  await page.route(`**/api/v2/snapshots/${id}`, (route) => {
    if (!payload || status >= 400) {
      return route.fulfill({ status: status || 404, contentType: 'application/json', body: JSON.stringify({ detail: 'snapshot_missing' }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
  })
}

async function mockDashboard(page: Page, payload: Record<string, unknown> | ((portfolioId: number) => Record<string, unknown>), status = 200): Promise<void> {
  await page.route('**/api/v3/portfolios/*/dashboard/today**', (route) => {
    const match = route.request().url().match(/portfolios\/(\d+)\/dashboard/)
    const portfolioId = Number(match?.[1] || 1)
    if (status >= 400) {
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ detail: 'dashboard_error' }) })
    }
    const body = typeof payload === 'function' ? payload(portfolioId) : payload
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...body, _portfolio_id: portfolioId }) })
  })
}

async function mockQuotes(
  page: Page,
  handler: (codes: string[], portfolioHint: string) => Record<string, unknown>,
  options: { status?: number; onCount?: (n: number) => void } = {},
): Promise<void> {
  await page.route('**/api/v3/market/instruments/quotes', async (route) => {
    const body = route.request().postDataJSON() as { codes?: string[] } | null
    const codes = body?.codes || []
    options.onCount?.(codes.length)
    if (options.status && options.status >= 400) {
      return route.fulfill({ status: options.status, contentType: 'application/json', body: JSON.stringify({ detail: 'quote_error' }) })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(handler(codes, route.request().url())),
    })
  })
}

async function mockBars(page: Page, mode: 'ok' | 'fail' = 'ok', failCode?: string): Promise<void> {
  await page.route('**/api/v3/market/instruments/*/bars**', (route) => {
    const url = route.request().url()
    if (mode === 'fail' && (!failCode || url.includes(failCode))) {
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'bars_error' }) })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        instrument: null,
        interval: '1d',
        adjustment: 'none',
        bars: Array.from({ length: 20 }, (_, i) => ({
          time: `2026-08-${String(i + 1).padStart(2, '0')}`,
          open: 100 + i,
          high: 101 + i,
          low: 99 + i,
          close: 100.5 + i,
          volume: 1000,
          turnover: 1e6,
        })),
        mixed_sources: false,
        quality: 'A',
        quality_flags: [],
        status: 'available',
        data_basis: 'session_close',
        volume_unit: 'shares',
        turnover_unit: 'CNY',
      }),
    })
  })
}

async function openHoldings(page: Page, query = ''): Promise<void> {
  await page.goto(`/holdings${query}`)
  await expect(page.getByTestId('v3-holdings')).toBeVisible({ timeout: 20_000 })
}

test.describe('V3 Holdings Workstation', () => {
  test('route migration: /holdings is V3 shell, name holdings, no Naive Holdings DOM', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: null, 2: null })
    await mockSession(page)
    await mockDashboard(page, dashboardPayload({ decision: null }))

    await openHoldings(page)
    await expect(page).toHaveURL(/\/holdings/)
    await expect(page.getByRole('heading', { name: '我的持仓' })).toBeVisible()
    await expect(page.getByTestId('v3-holdings')).toBeVisible()
    await expect(page.locator('.n-button, .n-select, .n-card, .n-drawer')).toHaveCount(0)
    await expect(page.getByTestId('v3-holdings-empty-snapshot')).toBeVisible()
  })

  test('/upload redirects to /holdings?action=update and opens V3 update drawer', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: null, 2: null })
    await mockSession(page)
    await mockDashboard(page, dashboardPayload({ decision: null }))

    await page.goto('/upload')
    await expect(page).toHaveURL(/\/holdings\?action=update/)
    await expect(page.getByTestId('v3-holdings-update-drawer')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.n-drawer')).toHaveCount(0)
  })

  test('normal workstation shows triple timestamps, summary, dense table, qty/available/cost/quote/judgment', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: 200 })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockSnapshot(page, 200, snapshotPayload({ id: 200 }))
    await mockDashboard(page, dashboardPayload({
      decision: {
        id: 1,
        decision_at: `${TRADE_DATE}T13:05:42+08:00`,
        decision_type: 'DAILY',
        final_rating: 'HOLD',
        portfolio_action: 'NO_ACTION',
        conclusion: 'NO_ACTION',
        holding_actions: [],
        candidate_actions: [],
        quality: 'A',
        confidence: 0.82,
        analysis_run_id: 11,
        portfolio_snapshot_id: 100,
      },
    }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => {
        if (code.startsWith('510300')) return quoteItem(code, 4.05, -1.2)
        return quoteItem(code, 1688.5, 1.25)
      }),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)

    await expect(page.getByTestId('v3-holdings-timestamps')).toBeVisible()
    await expect(page.getByTestId('v3-holdings-snapshot-ts')).toContainText('09:01:12')
    await expect(page.getByTestId('v3-holdings-quote-ts')).toContainText('14:31:05')
    await expect(page.getByTestId('v3-holdings-strategy-ts')).toContainText('13:05:42')

    await expect(page.getByTestId('v3-holdings-total-assets')).toContainText('500')
    await expect(page.getByTestId('v3-holdings-table')).toBeVisible()

    const maotai = page.getByTestId('v3-holding-row-600519.SH')
    await expect(maotai).toBeVisible()
    await expect(maotai.getByTestId('v3-holding-qty')).toContainText('100')
    await expect(maotai.getByTestId('v3-holding-available')).toContainText('100')
    await expect(maotai.getByTestId('v3-holding-price')).toContainText('1,688.50')
    await expect(maotai.getByTestId('v3-holding-change')).toHaveClass(/market-up/)
    await expect(maotai.getByTestId('v3-holding-judgment')).toHaveAttribute('data-status', 'HOLD')

    const etf = page.getByTestId('v3-holding-row-510300.SH')
    await expect(etf.getByTestId('v3-holding-change')).toHaveClass(/market-down/)
    await expect(etf.getByTestId('v3-holding-available')).toContainText('0')
  })

  test('A-share colors: up red class, down green class', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => code.startsWith('510300') ? quoteItem(code, 4.0, -0.8) : quoteItem(code, 1700, 2.1)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holding-row-600519.SH').getByTestId('v3-holding-change')).toHaveClass(/market-up/)
    await expect(page.getByTestId('v3-holding-row-510300.SH').getByTestId('v3-holding-change')).toHaveClass(/market-down/)
  })

  test('available_qty / T+1: qty=1000 available=0 strategy=EXIT still shows EXIT and execution constraint', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, {
      ...snapshotPayload({ id: 100 }),
      holdings: [{
        code: '600000.SH',
        canonical_code: '600000.SH',
        name: '浦发银行',
        display_name: '浦发银行',
        asset_type: 'STOCK',
        security_id: 3,
        resolution_status: 'RESOLVED',
        qty: 1000,
        available_qty: 0,
        cost: 8,
        price: 8.2,
        market_value: 8200,
        pnl: 0.025,
        pnl_amount: 200,
        weight: 0.05,
        extra: {},
      }],
    })
    await mockDashboard(page, dashboardPayload({
      decision: {
        id: 2,
        decision_at: `${TRADE_DATE}T13:05:42+08:00`,
        decision_type: 'DAILY',
        final_rating: 'EXIT',
        portfolio_action: 'EXIT',
        conclusion: 'EXIT',
        holding_actions: [{ code: '600000.SH', action: 'EXIT', reason: '止损' }],
        candidate_actions: [],
        quality: 'A',
        confidence: 0.7,
        analysis_run_id: 12,
        portfolio_snapshot_id: 100,
      },
      holdings: [holdingRow({ code: '600000.SH', holding_action: 'EXIT', weight: 0.05 })],
    }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 8.15, 0.4)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    const row = page.getByTestId('v3-holding-row-600000.SH')
    await expect(row.getByTestId('v3-holding-qty')).toContainText('1,000')
    await expect(row.getByTestId('v3-holding-available')).toContainText('0')
    await expect(row.getByTestId('v3-holding-constraint')).toContainText('当前可用 0')
    await expect(row.getByTestId('v3-holding-judgment')).toHaveAttribute('data-status', 'EXIT')
    await expect(row.getByTestId('v3-holding-judgment')).toContainText('不可执行')
    await expect(row.getByTestId('v3-holding-judgment')).not.toContainText('持有')
  })

  test('decision snapshot match shows REDUCE', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({
      decision: {
        id: 3,
        decision_at: `${TRADE_DATE}T13:05:42+08:00`,
        decision_type: 'DAILY',
        final_rating: 'ACTION',
        portfolio_action: 'REDUCE',
        conclusion: 'REDUCE',
        holding_actions: [{ code: '600519.SH', action: 'REDUCE', trigger: '权重逼近 Hard Cap' }],
        candidate_actions: [],
        quality: 'A',
        confidence: 0.75,
        analysis_run_id: 13,
        portfolio_snapshot_id: 100,
      },
    }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 1688, 0.5)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holding-row-600519.SH').getByTestId('v3-holding-judgment')).toHaveAttribute('data-status', 'REDUCE')
    await expect(page.getByTestId('v3-holdings-actionable-count')).toHaveText('1')
  })

  test('decision snapshot mismatch blocks stale actions as DATA_INSUFFICIENT', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 101, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 101, snapshotPayload({ id: 101 }))
    await mockDashboard(page, dashboardPayload({
      decision: {
        id: 4,
        decision_at: `${TRADE_DATE}T13:05:42+08:00`,
        decision_type: 'DAILY',
        final_rating: 'ACTION',
        portfolio_action: 'REDUCE',
        conclusion: 'REDUCE',
        holding_actions: [{ code: '600519.SH', action: 'REDUCE' }],
        candidate_actions: [],
        quality: 'A',
        confidence: 0.75,
        analysis_run_id: 14,
        portfolio_snapshot_id: 100,
      },
    }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 1688, 0.2)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holdings-stale-strategy')).toBeVisible()
    await expect(page.getByTestId('v3-holdings-decision-title')).toContainText('策略基于旧持仓快照')
    await expect(page.getByTestId('v3-holding-row-600519.SH').getByTestId('v3-holding-judgment')).toHaveAttribute('data-status', 'DATA_INSUFFICIENT')
    await expect(page.getByTestId('v3-holding-row-600519.SH')).not.toContainText('减仓')
  })

  test('explicit NO_ACTION maps unmatched rows to HOLD', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({
      decision: {
        id: 5,
        decision_at: `${TRADE_DATE}T13:05:42+08:00`,
        decision_type: 'DAILY',
        final_rating: 'HOLD',
        portfolio_action: 'NO_ACTION',
        conclusion: 'NO_ACTION',
        holding_actions: [],
        candidate_actions: [],
        quality: 'A',
        confidence: 0.9,
        analysis_run_id: 15,
        portfolio_snapshot_id: 100,
      },
    }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 100, 0)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holding-row-600519.SH').getByTestId('v3-holding-judgment')).toHaveAttribute('data-status', 'HOLD')
    await expect(page.getByTestId('v3-holdings-decision-title')).toContainText('NO_ACTION')
  })

  test('decision missing defaults rows to DATA_INSUFFICIENT, not HOLD', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 100, 0.1)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holding-row-600519.SH').getByTestId('v3-holding-judgment')).toHaveAttribute('data-status', 'DATA_INSUFFICIENT')
    await expect(page.getByTestId('v3-holding-row-600519.SH').getByTestId('v3-holding-judgment')).not.toContainText('持有')
  })

  test('BLOCKED quality shows 策略暂不可执行 and RISK_BLOCKED rows', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({
      decision: {
        id: 6,
        decision_at: `${TRADE_DATE}T13:05:42+08:00`,
        decision_type: 'DAILY',
        final_rating: 'BLOCKED',
        portfolio_action: 'BLOCKED',
        conclusion: 'BLOCKED',
        holding_actions: [],
        candidate_actions: [],
        quality: 'BLOCKED',
        confidence: null,
        analysis_run_id: 16,
        portfolio_snapshot_id: 100,
      },
    }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 100, 0)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holdings-decision-title')).toContainText('策略暂不可执行')
    await expect(page.getByTestId('v3-holding-row-600519.SH').getByTestId('v3-holding-judgment')).toHaveAttribute('data-status', 'RISK_BLOCKED')
  })

  test('batch quotes: one request for resolved codes, unresolved excluded', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    const codesSeen: string[] = []
    let requestCount = 0
    await mockQuotes(page, (codes) => {
      requestCount += 1
      codesSeen.push(...codes)
      return {
        items: codes.map((code) => quoteItem(code, 100, 0.2)),
        as_of: `${TRADE_DATE}T14:31:05+08:00`,
      }
    })
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holdings-table')).toBeVisible()
    expect(requestCount).toBe(1)
    expect(codesSeen).toContain('600519.SH')
    expect(codesSeen).toContain('510300.SH')
    expect(codesSeen.some((code) => code.includes('000001') && !code.includes('SH'))).toBe(false)
  })

  test('partial quote keeps snapshot qty/cost and marks coverage', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes
        .filter((code) => !code.startsWith('510300'))
        .map((code) => quoteItem(code, 1688, 1.1)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    const etf = page.getByTestId('v3-holding-row-510300.SH')
    await expect(etf.getByTestId('v3-holding-price')).toHaveText('—')
    await expect(etf.getByTestId('v3-holding-qty')).toContainText('1,000')
    await expect(etf).toContainText('3.80')
    await expect(page.getByTestId('v3-holdings-table')).toBeVisible()
  })

  test('stale quote shows 过期 badge and is not live', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 1688, 0.3, 'STALE')),
      as_of: `${TRADE_DATE}T11:00:00+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holding-row-600519.SH')).toContainText('过期')
  })

  test('portfolio switch ownership: A data hidden while B pending, late A cannot overwrite B', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: 200 })
    await mockSession(page)

    let resolveB: (() => void) | null = null
    const bGate = new Promise<void>((resolve) => { resolveB = resolve })
    let resolveA: (() => void) | null = null
    const aGate = new Promise<void>((resolve) => { resolveA = resolve })

    await page.route('**/api/v2/snapshots/100', async (route) => {
      await aGate
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...snapshotPayload({ id: 100 }),
          holdings: [{
            code: '600519.SH',
            canonical_code: '600519.SH',
            name: '组合A持仓',
            display_name: '组合A持仓',
            asset_type: 'STOCK',
            security_id: 1,
            resolution_status: 'RESOLVED',
            qty: 100,
            available_qty: 100,
            cost: 1500,
            price: 1680,
            market_value: 168000,
            pnl: 0.1,
            pnl_amount: 1000,
            weight: 0.3,
            extra: {},
          }],
        }),
      })
    })
    await page.route('**/api/v2/snapshots/200', async (route) => {
      await bGate
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...snapshotPayload({ id: 200 }),
          holdings: [{
            code: '000002.SZ',
            canonical_code: '000002.SZ',
            name: '组合B持仓',
            display_name: '组合B持仓',
            asset_type: 'STOCK',
            security_id: 9,
            resolution_status: 'RESOLVED',
            qty: 200,
            available_qty: 200,
            cost: 10,
            price: 11,
            market_value: 2200,
            pnl: 0.1,
            pnl_amount: 200,
            weight: 0.2,
            extra: {},
          }],
        }),
      })
    })
    await mockDashboard(page, (portfolioId) => dashboardPayload({
      decision: {
        id: 20 + portfolioId,
        decision_at: `${TRADE_DATE}T13:05:42+08:00`,
        decision_type: 'DAILY',
        final_rating: 'HOLD',
        portfolio_action: 'NO_ACTION',
        conclusion: 'NO_ACTION',
        holding_actions: [],
        candidate_actions: [],
        quality: 'A',
        confidence: 0.8,
        analysis_run_id: 20 + portfolioId,
        portfolio_snapshot_id: portfolioId === 1 ? 100 : 200,
      },
    }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 20, 0.1)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await openHoldings(page)
    resolveA?.()
    await expect(page.getByTestId('v3-holding-row-600519.SH')).toBeVisible()

    await page.getByTestId('v3-holdings-portfolio-select').selectOption({ label: '组合B' })
    // While B is pending, A rows must not remain visible.
    await expect(page.getByTestId('v3-holding-row-600519.SH')).toHaveCount(0)

    resolveB?.()
    await expect(page.getByTestId('v3-holding-row-000002.SZ')).toBeVisible()
    await expect(page.getByTestId('v3-holding-row-600519.SH')).toHaveCount(0)
  })

  test('B first request 500 shows localized error without falling back to A', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: 200 })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockSnapshot(page, 200, null, 500)
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 30, 0.1)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)
    allowExpectedHttpError(page, 500)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holding-row-600519.SH')).toBeVisible()
    await page.getByTestId('v3-holdings-portfolio-select').selectOption({ label: '组合B' })
    await expect(page.getByTestId('v3-holdings-snapshot-error')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('v3-holding-row-600519.SH')).toHaveCount(0)
  })

  test('sparkline: bars failure hides only that row sparkline, table survives', async ({ acceptancePage: page }) => {
    allowExpectedHttpError(page, 500)
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 100, 0.2)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page, 'fail', '510300')

    await openHoldings(page)
    await expect(page.getByTestId('v3-holdings-table')).toBeVisible()
    await expect(page.getByTestId('v3-holding-sparkline-na').first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('v3-holding-row-600519.SH')).toBeVisible()
    await expect(page.getByTestId('instrument-detail-drawer')).toHaveCount(0)
  })

  test('unified instrument drawer opens on resolved row only', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 100, 0.1)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)
    await page.route('**/api/v3/market/instruments/600519.SH**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          instrument_id: 'CN.600519',
          code: '600519.SH',
          symbol: '600519',
          exchange: 'SSE',
          name: '贵州茅台',
          instrument_type: 'STOCK',
          board: null,
          currency: 'CNY',
          lot_size: 100,
          is_st: false,
          is_suspended: false,
          status: 'active',
        }),
      }),
    )

    await openHoldings(page)
    await page.getByTestId('v3-holding-row-600519.SH').click()
    await expect(page.getByTestId('instrument-detail-drawer')).toBeVisible({ timeout: 15_000 })
    await page.getByTestId('v3-detail-drawer-close').click()
    await expect(page.getByTestId('instrument-detail-drawer')).toHaveCount(0)

    const unresolved = page.locator('[data-testid^="v3-holding-row-000001"]')
    if (await unresolved.count()) {
      await unresolved.first().click()
      await expect(page.getByTestId('instrument-detail-drawer')).toHaveCount(0)
    }
  })

  test('mobile 375 has no horizontal document overflow and keeps table scroller', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 100, 0.1)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    await page.setViewportSize({ width: 375, height: 720 })
    await openHoldings(page)
    await expect(page.getByTestId('v3-holdings-summary')).toBeVisible()
    await expect(page.getByTestId('v3-holdings-table')).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  })

  test('poller independence: session heartbeat does not starve quote/strategy cadence', async ({ acceptancePage: page }) => {
    await page.clock.install({ time: new Date(`${TRADE_DATE}T10:00:00+08:00`) })
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    let sessionCalls = 0
    let quoteCalls = 0
    let strategyCalls = 0
    await page.route('**/api/v3/market/session**', (route) => {
      sessionCalls += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sessionPayload('MORNING')) })
    })
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await page.route('**/api/v3/portfolios/*/dashboard/today**', (route) => {
      strategyCalls += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(dashboardPayload({ decision: null })) })
    })
    await page.route('**/api/v3/market/instruments/quotes', (route) => {
      quoteCalls += 1
      const body = route.request().postDataJSON() as { codes?: string[] } | null
      const codes = body?.codes || []
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: codes.map((code) => quoteItem(code, 100, 0.1)),
          as_of: `${TRADE_DATE}T10:00:00+08:00`,
        }),
      })
    })
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holdings-table')).toBeVisible()
    const quotesAfterBoot = quoteCalls
    const strategyAfterBoot = strategyCalls
    expect(quotesAfterBoot).toBeGreaterThan(0)
    expect(strategyAfterBoot).toBeGreaterThan(0)

    // Advance through several session heartbeats (5s) without full strategy cadence (45s).
    await page.clock.fastForward(12_000)
    await expect.poll(() => sessionCalls, { timeout: 5_000 }).toBeGreaterThan(1)
    // Quotes may refresh; strategy must not fire on every session tick.
    expect(strategyCalls - strategyAfterBoot).toBeLessThanOrEqual(1)

    await page.clock.fastForward(50_000)
    await expect.poll(() => strategyCalls, { timeout: 5_000 }).toBeGreaterThan(strategyAfterBoot)
  })

  test('visibility resume is session-first then quotes then strategy', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    const order: string[] = []
    await page.route('**/api/v3/market/session**', async (route) => {
      order.push('session')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sessionPayload('MORNING')) })
    })
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await page.route('**/api/v3/portfolios/*/dashboard/today**', async (route) => {
      order.push('strategy')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(dashboardPayload({ decision: null })) })
    })
    await page.route('**/api/v3/market/instruments/quotes', async (route) => {
      order.push('quote')
      const body = route.request().postDataJSON() as { codes?: string[] } | null
      const codes = body?.codes || []
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: codes.map((code) => quoteItem(code, 100, 0.1)),
          as_of: `${TRADE_DATE}T14:31:05+08:00`,
        }),
      })
    })
    await mockBars(page)

    await openHoldings(page)
    await expect(page.getByTestId('v3-holdings-table')).toBeVisible()
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await page.waitForTimeout(100)
    order.length = 0

    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await expect.poll(() => order.length, { timeout: 10_000 }).toBeGreaterThanOrEqual(3)
    expect(order[0]).toBe('session')
    expect(order.indexOf('quote')).toBeGreaterThan(order.indexOf('session'))
    expect(order.indexOf('strategy')).toBeGreaterThan(order.indexOf('session'))
  })

  test('update drawer upload poll + confirm snapshot invalidates old decision', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: null, 2: null })
    await mockSession(page)
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockBars(page)

    let parseStatus = 'uploaded'
    let parsePolls = 0
    await page.route('**/api/v2/portfolios/1/uploads', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 77,
          portfolio_id: 1,
          original_filename: 'a.png',
          mime_type: 'image/png',
          parsing_status: 'uploaded',
          parsed: null,
          identity_issues: [],
          validation_errors: [],
          screenshot_url: '/api/v2/uploads/77/image',
          created_at: `${TRADE_DATE}T09:00:00+08:00`,
        }),
      }),
    )
    await page.route('**/api/v2/uploads/77', (route) => {
      parsePolls += 1
      if (parsePolls >= 2 && parseStatus !== 'confirmed') parseStatus = 'waiting_confirmation'
      if (parseStatus === 'waiting_confirmation') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 77,
            portfolio_id: 1,
            original_filename: 'a.png',
            mime_type: 'image/png',
            parsing_status: 'waiting_confirmation',
            parsed: {
              holdings: [{
                code: '600519.SH',
                canonical_code: '600519.SH',
                name: '贵州茅台',
                display_name: '贵州茅台',
                security_id: 1,
                resolution_status: 'RESOLVED',
                qty: 100,
                available_qty: 100,
                cost: 1500,
                price: 1680,
                market_value: 168000,
                pnl: 0.1,
                pnl_amount: 1000,
                weight: 0.3,
                extra: {},
              }],
              total_assets: 500000,
              total_market_value: 168000,
              broker_available_cash: 332000,
              excluded_items: [],
              notes: [],
            },
            identity_issues: [],
            validation_errors: [],
            screenshot_url: '/api/v2/uploads/77/image',
            created_at: `${TRADE_DATE}T09:00:00+08:00`,
          }),
        })
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 77,
          portfolio_id: 1,
          original_filename: 'a.png',
          mime_type: 'image/png',
          parsing_status: parseStatus,
          parsed: null,
          identity_issues: [],
          validation_errors: [],
          screenshot_url: '/api/v2/uploads/77/image',
          created_at: `${TRADE_DATE}T09:00:00+08:00`,
        }),
      })
    })
    await page.route('**/api/v2/uploads/77/parsed-holdings', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 77,
          portfolio_id: 1,
          original_filename: 'a.png',
          mime_type: 'image/png',
          parsing_status: 'waiting_confirmation',
          parsed: {
            holdings: [{
              code: '600519.SH',
              canonical_code: '600519.SH',
              name: '贵州茅台',
              display_name: '贵州茅台',
              security_id: 1,
              resolution_status: 'RESOLVED',
              qty: 100,
              available_qty: 100,
              cost: 1500,
              price: 1680,
              market_value: 168000,
              pnl: 0.1,
              pnl_amount: 1000,
              weight: 0.3,
              extra: {},
            }],
            total_assets: 500000,
            total_market_value: 168000,
            broker_available_cash: 332000,
            excluded_items: [],
            notes: [],
          },
          identity_issues: [],
          validation_errors: [],
          screenshot_url: '/api/v2/uploads/77/image',
          created_at: `${TRADE_DATE}T09:00:00+08:00`,
        }),
      }),
    )
    await page.route('**/api/v2/uploads/77/confirm', (route) => {
      parseStatus = 'confirmed'
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 301,
          portfolio_id: 1,
          upload_id: 77,
          source: 'upload',
          snapshot_time: `${TRADE_DATE}T15:10:00+08:00`,
          status: 'confirmed',
          identity_status: 'RESOLVED',
          identity_issues: [],
          holdings: [{
            code: '600519.SH',
            canonical_code: '600519.SH',
            name: '贵州茅台',
            display_name: '贵州茅台',
            asset_type: 'STOCK',
            security_id: 1,
            resolution_status: 'RESOLVED',
            qty: 100,
            available_qty: 100,
            cost: 1500,
            price: 1680,
            market_value: 168000,
            pnl: 0.1,
            pnl_amount: 1000,
            weight: 0.3,
            extra: {},
          }],
          total_assets: 500000,
          total_market_value: 168000,
          broker_available_cash: 332000,
          excluded_items: [],
          notes: [],
        }),
      })
    })
    await page.route('**/api/v2/snapshots/301', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 301,
          portfolio_id: 1,
          upload_id: 77,
          source: 'upload',
          snapshot_time: `${TRADE_DATE}T15:10:00+08:00`,
          status: 'confirmed',
          identity_status: 'RESOLVED',
          identity_issues: [],
          holdings: [{
            code: '600519.SH',
            canonical_code: '600519.SH',
            name: '贵州茅台',
            display_name: '贵州茅台',
            asset_type: 'STOCK',
            security_id: 1,
            resolution_status: 'RESOLVED',
            qty: 100,
            available_qty: 100,
            cost: 1500,
            price: 1680,
            market_value: 168000,
            pnl: 0.1,
            pnl_amount: 1000,
            weight: 0.3,
            extra: {},
          }],
          total_assets: 500000,
          total_market_value: 168000,
          broker_available_cash: 332000,
          excluded_items: [],
          notes: [],
        }),
      }),
    )
    await page.route('**/api/v2/portfolios**', (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{
          id: 1,
          name: '组合A',
          market: 'CN',
          currency: 'CNY',
          is_default: true,
          latest_snapshot_id: parseStatus === 'confirmed' ? 301 : null,
          latest_snapshot_time: parseStatus === 'confirmed' ? `${TRADE_DATE}T15:10:00+08:00` : null,
          created_at: `${TRADE_DATE}T00:00:00+08:00`,
          updated_at: `${TRADE_DATE}T00:00:00+08:00`,
        }]),
      })
    })
    // Dashboard still returns old decision bound to snapshot 100.
    await mockDashboard(page, dashboardPayload({
      decision: {
        id: 9,
        decision_at: `${TRADE_DATE}T13:05:42+08:00`,
        decision_type: 'DAILY',
        final_rating: 'ACTION',
        portfolio_action: 'REDUCE',
        conclusion: 'REDUCE',
        holding_actions: [{ code: '600519.SH', action: 'REDUCE' }],
        candidate_actions: [],
        quality: 'A',
        confidence: 0.7,
        analysis_run_id: 9,
        portfolio_snapshot_id: 100,
      },
    }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 1688, 0.5)),
      as_of: `${TRADE_DATE}T15:11:00+08:00`,
    }))

    await page.goto('/holdings?action=update')
    await expect(page.getByTestId('v3-holdings-update-drawer')).toBeVisible()
    await page.locator('input[type="file"]').setInputFiles({
      name: 'a.png',
      mimeType: 'image/png',
      buffer: Buffer.from('fake-png'),
    })
    await page.getByTestId('v3-update-submit').click()
    await expect(page.getByTestId('v3-update-status')).toContainText('待人工确认', { timeout: 15_000 })
    await expect(page.locator('[data-testid^="v3-identity-row-"]').first()).toBeVisible()
    await expect(page.getByTestId('v3-update-confirm')).toBeEnabled()
    await page.getByTestId('v3-update-confirm').click()
    await expect(page.getByTestId('v3-holdings-stale-strategy')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('v3-holding-row-600519.SH').getByTestId('v3-holding-judgment')).toHaveAttribute('data-status', 'DATA_INSUFFICIENT')
  })

  test('identity ambiguous opens candidate dialog and selection resolves row', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: null, 2: null })
    await mockSession(page)
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await page.route('**/api/v2/holdings/resolve**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          code: '000001',
          name: '银行',
          resolution_status: 'AMBIGUOUS',
          extra: {
            identity_candidates: [
              { code: '000001.SZ', canonical_code: '000001.SZ', display_name: '平安银行', asset_type: 'STOCK', exchange: 'SZSE', security_id: 11 },
              { code: '600000.SH', canonical_code: '600000.SH', display_name: '浦发银行', asset_type: 'STOCK', exchange: 'SSE', security_id: 12 },
            ],
          },
        }),
      }),
    )

    await page.goto('/holdings?action=update')
    await expect(page.getByTestId('v3-holdings-update-drawer')).toBeVisible()
    await page.getByTestId('v3-update-manual').click()
    await page.locator('[data-testid="v3-identity-code"]').first().fill('000001')
    await page.locator('[data-testid="v3-identity-code"]').first().blur()
    await expect(page.getByTestId('v3-identity-select-candidate')).toBeVisible({ timeout: 10_000 })
    await page.getByTestId('v3-identity-select-candidate').click()
    await expect(page.getByTestId('v3-security-candidate-dialog')).toBeVisible()
    await page.getByTestId('v3-candidate-pick').first().click()
    await expect(page.locator('[data-testid="v3-identity-row-0"]')).toContainText('已匹配')
  })

  test('closing update drawer stops upload polling', async ({ acceptancePage: page }) => {
    await mockAuth(page)
    await mockPortfolios(page, { 1: 100, 2: null })
    await mockSession(page)
    await mockSnapshot(page, 100, snapshotPayload({ id: 100 }))
    await mockDashboard(page, dashboardPayload({ decision: null }))
    await mockQuotes(page, (codes) => ({
      items: codes.map((code) => quoteItem(code, 100, 0.1)),
      as_of: `${TRADE_DATE}T14:31:05+08:00`,
    }))
    await mockBars(page)

    let uploadHits = 0
    await page.route('**/api/v2/portfolios/1/uploads', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 88,
          portfolio_id: 1,
          original_filename: 'a.png',
          mime_type: 'image/png',
          parsing_status: 'vision_parsing',
          parsed: null,
          identity_issues: [],
          validation_errors: [],
          screenshot_url: '/api/v2/uploads/88/image',
          created_at: `${TRADE_DATE}T09:00:00+08:00`,
        }),
      }),
    )
    await page.route('**/api/v2/uploads/88', (route) => {
      uploadHits += 1
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 88,
          portfolio_id: 1,
          original_filename: 'a.png',
          mime_type: 'image/png',
          parsing_status: 'vision_parsing',
          parsed: null,
          identity_issues: [],
          validation_errors: [],
          screenshot_url: '/api/v2/uploads/88/image',
          created_at: `${TRADE_DATE}T09:00:00+08:00`,
        }),
      })
    })

    await page.goto('/holdings?action=update')
    await expect(page.getByTestId('v3-holdings-update-drawer')).toBeVisible()
    await page.locator('input[type="file"]').setInputFiles({
      name: 'a.png',
      mimeType: 'image/png',
      buffer: Buffer.from('fake-png'),
    })
    await expect(page.getByTestId('v3-update-submit')).toBeEnabled()
    await page.getByTestId('v3-update-submit').click()
    await expect.poll(() => uploadHits, { timeout: 10_000 }).toBeGreaterThan(0)
    const hitsBeforeClose = uploadHits
    await page.getByTestId('v3-detail-drawer-close').click()
    await page.waitForTimeout(4000)
    expect(uploadHits).toBeLessThanOrEqual(hitsBeforeClose + 1)
  })
})
