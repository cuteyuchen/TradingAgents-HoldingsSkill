/**
 * V3 Dashboard Decision Workbench acceptance
 * Fully deterministic via page.route() — no live Tencent/Eastmoney/Fuyao.
 */
import { test, expect, type Page } from '@playwright/test'

const TRADE_DATE = '2026-09-11'

type SessionKind =
  | 'PRE_OPEN'
  | 'OPEN_AUCTION'
  | 'MORNING'
  | 'LUNCH_BREAK'
  | 'AFTERNOON'
  | 'CLOSE_AUCTION'
  | 'CLOSED'
  | 'NON_TRADING_DAY'
  | 'DATA_ABNORMAL'

function sessionPayload(kind: SessionKind, dataBasis: 'live' | 'session_close' | 'previous_session_close' = 'live') {
  return {
    market: 'CN',
    timezone: 'Asia/Shanghai',
    session: kind,
    is_trading_day: kind !== 'NON_TRADING_DAY',
    is_market_open: kind === 'MORNING' || kind === 'AFTERNOON' || kind === 'OPEN_AUCTION' || kind === 'CLOSE_AUCTION',
    trading_date: TRADE_DATE,
    latest_valid_trading_date: TRADE_DATE,
    resolved_at: `${TRADE_DATE}T10:00:00+08:00`,
    data_status: kind === 'DATA_ABNORMAL' ? 'ABNORMAL' : 'OK',
    data_basis: dataBasis,
    display_label: kind,
  }
}

function indexRow(code: string, name: string, last: number, changePct: number) {
  return {
    code,
    name,
    last,
    change: Number((last * changePct * 0.01).toFixed(2)),
    change_pct: changePct,
    open: last,
    high: last * 1.01,
    low: last * 0.99,
    prev_close: last / (1 + changePct / 100),
    volume: 1000,
    turnover: 1e9,
    as_of: `${TRADE_DATE}T10:00:00+08:00`,
    source: 'acceptance',
    quality: 'A',
    status: 'available',
    fallback: false,
    missing_fields: [],
  }
}

function majorIndicesPayload() {
  return [
    indexRow('000001.SH', '上证指数', 3200.5, 0.52),
    indexRow('399001.SZ', '深证成指', 10000.2, -0.31),
    indexRow('399006.SZ', '创业板指', 2000.1, 0.0),
    indexRow('000300.SH', '沪深300', 3800.8, 0.41),
    indexRow('000852.SH', '中证1000', 5500.3, -1.2),
    {
      code: '000688.SH',
      name: '科创50',
      last: null,
      change: null,
      change_pct: null,
      quality: 'MISSING',
      status: 'unavailable',
      fallback: false,
      missing_fields: ['last'],
    },
  ]
}

function systemicRiskPayload() {
  return {
    risk_level: 'MEDIUM',
    risk_score: 55.5,
    median_return: {
      status: 'available',
      current_value: 0.12,
      daily_median_return: 0.12,
      trend_20d: 0.08,
      percentile_250d: 62.5,
      eligible_count: 5000,
      universe_total: 5300,
      excluded_count: 200,
      suspended_count: 100,
      as_of: `${TRADE_DATE}T10:00:00+08:00`,
      trading_date: TRADE_DATE,
      quality_grade: 'A',
      quality_flags: [],
      missing_fields: [],
      source_status: 'OK',
      universe_version: 'v1',
      calculation_version: 'v1',
    },
    turnover_concentration: {
      status: 'available',
      ratio: 0.28,
      avg_20d: 0.24,
      delta_vs_20d: 0.04,
      trend: 'rising',
      percentile_250d: 70,
      top_n: 5,
      eligible_count: 5000,
      total_turnover: 1.2e12,
      as_of: `${TRADE_DATE}T10:00:00+08:00`,
      trading_date: TRADE_DATE,
      quality_grade: 'A',
      quality_flags: [],
      missing_fields: [],
      source_status: 'OK',
      calculation_version: 'v1',
    },
    breadth: {
      status: 'available',
      advancers: 2800,
      decliners: 2100,
      unchanged: 300,
      suspended: 80,
      limit_up: 42,
      limit_down: 8,
      total: 5200,
      eligible_count: 5120,
      advance_decline_ratio: 1.33,
      as_of: `${TRADE_DATE}T10:00:00+08:00`,
      trading_date: TRADE_DATE,
      quality_grade: 'A',
      quality_flags: [],
      missing_fields: [],
      source_status: 'OK',
      calculation_version: 'v1',
    },
    total_turnover: {
      status: 'available',
      value: 9.8e11,
      avg_20d: 9.2e11,
      delta_vs_20d: 6e10,
      unit: 'CNY',
      as_of: `${TRADE_DATE}T10:00:00+08:00`,
      trading_date: TRADE_DATE,
      quality_grade: 'A',
      quality_flags: [],
      missing_fields: [],
      source_status: 'OK',
      calculation_version: 'v1',
    },
    major_indices: majorIndicesPayload(),
    risk_factors: ['BREADTH_NARROW', 'CONCENTRATION_ELEVATED'],
    data_quality: 'A',
    quality_flags: [],
    missing_fields: [],
    source_status: 'OK',
    as_of: `${TRADE_DATE}T10:00:00+08:00`,
    trading_date: TRADE_DATE,
    calculation_version: 'v1',
  }
}

function overviewPayload(sessionKind: SessionKind = 'MORNING', dataBasis: 'live' | 'previous_session_close' = 'live') {
  return {
    session: sessionPayload(sessionKind, dataBasis),
    major_indices: majorIndicesPayload(),
    systemic_risk: systemicRiskPayload(),
    breadth: systemicRiskPayload().breadth,
    all_a_median: systemicRiskPayload().median_return,
    turnover_concentration: systemicRiskPayload().turnover_concentration,
    total_turnover: systemicRiskPayload().total_turnover,
    quote_as_of: `${TRADE_DATE}T10:00:00+08:00`,
  }
}

function dashboardToday(options: {
  portfolioId: number
  totalAssets?: number
  decision?: Record<string, unknown>
  analysis?: Record<string, unknown>
  decisionsStatus?: string
  analysisStatus?: string
  analysisInProgress?: boolean
  finalAction?: string
  timeline?: unknown[]
  notifications?: unknown[]
}) {
  const {
    portfolioId,
    totalAssets = 100000,
    decision = null as Record<string, unknown> | null,
    analysis = null as Record<string, unknown> | null,
    decisionsStatus = decision ? 'AVAILABLE' : 'MISSING',
    analysisStatus = analysis ? 'AVAILABLE' : 'MISSING',
    analysisInProgress = false,
    finalAction = 'NO_ACTION',
    timeline = [
      {
        key: 'fast_review',
        time: `${TRADE_DATE}T10:30:00+08:00`,
        label: '快速复核',
        kind: 'FAST_REVIEW',
        scheduled_at: `${TRADE_DATE}T10:30:00+08:00`,
        is_current: true,
      },
    ],
    notifications = [],
  } = options

  return {
    as_of: `${TRADE_DATE}T10:05:00+08:00`,
    trade_date: TRADE_DATE,
    market_open: true,
    market_session: sessionPayload('MORNING'),
    quote_as_of: `${TRADE_DATE}T10:00:00+08:00`,
    strategy_analysis_at: analysis?.finished_at || null,
    workflow_state: 'MORNING_SESSION',
    market: { status: 'AVAILABLE', score: 60, freshness: 'FRESH' },
    portfolio: {
      status: 'AVAILABLE',
      quality_status: 'VALID',
      freshness: 'FRESH',
      snapshot_id: 11,
      snapshot_time: `${TRADE_DATE}T09:00:00+08:00`,
      total_assets: totalAssets,
      market_value: totalAssets * 0.7,
      spendable_cash: totalAssets * 0.3,
      reserve_assets: totalAssets * 0.05,
      cash_ratio: 0.3,
      gross_exposure: 0.7,
      position_count: 6,
      risk_flags: [],
      holdings: [],
      hard_cap_breaches: [],
    },
    candidates: { status: 'AVAILABLE', action: [], ready: [], watchlist: [] },
    triggers: { status: 'AVAILABLE', today_count: 0, items: [] },
    analysis: {
      status: analysisStatus,
      latest: analysis,
      last_analysis: analysis,
      analysis_in_progress: analysisInProgress,
      jobs: [],
      running_jobs: analysisInProgress ? [{ id: 9, mode: 'fast', status: 'RUNNING' }] : [],
    },
    decisions: {
      status: decisionsStatus,
      latest: decision,
      today_count: decision ? 1 : 0,
      final_action: finalAction,
    },
    executions: { status: 'AVAILABLE', items: [] },
    memory: { status: 'MISSING' },
    data_health: { status: 'OK', overall: 'OK', components: [] },
    timeline: {
      as_of: `${TRADE_DATE}T10:05:00+08:00`,
      trade_date: TRADE_DATE,
      workflow_state: 'MORNING_SESSION',
      monitor: {},
      timeline,
    },
    notifications: { items: notifications, count: notifications.length, total_count: notifications.length, unread_count: 0, critical_count: 0 },
    _portfolio_id: portfolioId,
  }
}

const NO_ACTION_DECISION = {
  id: 1,
  decision_at: `${TRADE_DATE}T09:40:00+08:00`,
  decision_type: 'DAILY',
  final_rating: 'HOLD',
  portfolio_action: 'NO_ACTION',
  conclusion: 'NO_ACTION',
  holding_actions: [],
  candidate_actions: [],
  quality: 'A',
  confidence: 0.8,
  analysis_run_id: 101,
}

const ACTIONABLE_DECISION = {
  id: 2,
  decision_at: `${TRADE_DATE}T09:45:00+08:00`,
  decision_type: 'DAILY',
  final_rating: 'ACTION',
  portfolio_action: 'ACTION',
  conclusion: 'ACTION',
  holding_actions: [
    { code: '600519.SH', name: '贵州茅台', action: 'REDUCE', reason: '超配回补', stage: 'ACTION' },
  ],
  candidate_actions: [
    { code: '000001.SZ', name: '平安银行', action: 'BUY', reason: '候选确认', stage: 'ACTION' },
  ],
  quality: 'A',
  confidence: 0.77,
  analysis_run_id: 102,
}

const BLOCKED_DECISION = {
  id: 3,
  decision_at: `${TRADE_DATE}T09:50:00+08:00`,
  decision_type: 'DAILY',
  final_rating: 'BLOCKED',
  portfolio_action: 'BLOCKED',
  conclusion: 'BLOCKED',
  holding_actions: [],
  candidate_actions: [],
  quality: 'BLOCKED',
  confidence: null,
  analysis_run_id: 103,
  reasons: ['数据质量不足', '组合快照缺失'],
}

const ANALYSIS_DONE = {
  analysis_job_id: 21,
  analysis_run_id: 101,
  mode: 'fast',
  started_at: `${TRADE_DATE}T09:35:00+08:00`,
  finished_at: `${TRADE_DATE}T09:40:00+08:00`,
  status: 'SUCCESS',
  final_rating: 'HOLD',
  portfolio_action: 'NO_ACTION',
  quality: 'A',
  confidence: 0.8,
  candidate_action_count: 0,
}

async function mockAuthAndShell(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('advisor_v2_access_token', 'acceptance-v3-dashboard')
    localStorage.setItem('advisor_v2_refresh_token', 'acceptance-v3-dashboard-refresh')
  })
  await page.route('**/api/v2/auth/me**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 1, email: 'a@example.com', username: 'a', status: 'active', timezone: 'Asia/Shanghai', created_at: `${TRADE_DATE}T00:00:00+08:00` }),
    }),
  )
  await page.route('**/api/v3/system/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'OK' }) }),
  )
  await page.route('**/api/v2/portfolios**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 1, name: '组合A', market: 'CN', currency: 'CNY', is_default: true, created_at: `${TRADE_DATE}T00:00:00+08:00`, updated_at: `${TRADE_DATE}T00:00:00+08:00` },
        { id: 2, name: '组合B', market: 'CN', currency: 'CNY', is_default: false, created_at: `${TRADE_DATE}T00:00:00+08:00`, updated_at: `${TRADE_DATE}T00:00:00+08:00` },
      ]),
    }),
  )
}

async function mockMarket(
  page: Page,
  options: {
    sessionKind?: SessionKind
    dataBasis?: 'live' | 'previous_session_close'
    indicesStatus?: number
    riskStatus?: number
    overviewStatus?: number
  } = {},
): Promise<void> {
  const kind = options.sessionKind || 'MORNING'
  const basis = options.dataBasis || 'live'
  await page.route('**/api/v3/market/session**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(sessionPayload(kind, basis)),
    }),
  )
  await page.route('**/api/v3/market/major-indices**', (route) => {
    if (options.indicesStatus && options.indicesStatus >= 400) {
      return route.fulfill({ status: options.indicesStatus, contentType: 'application/json', body: JSON.stringify({ detail: 'indices_error' }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(majorIndicesPayload()) })
  })
  await page.route('**/api/v3/market/systemic-risk**', (route) => {
    if (options.riskStatus && options.riskStatus >= 400) {
      return route.fulfill({ status: options.riskStatus, contentType: 'application/json', body: JSON.stringify({ detail: 'risk_error' }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(systemicRiskPayload()) })
  })
  await page.route('**/api/v3/market/overview**', (route) => {
    if (options.overviewStatus && options.overviewStatus >= 400) {
      return route.fulfill({ status: options.overviewStatus, contentType: 'application/json', body: JSON.stringify({ detail: 'overview_error' }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(overviewPayload(kind, basis)) })
  })
}

async function mockDashboardToday(page: Page, handler: (portfolioId: number) => Record<string, unknown>, status = 200): Promise<void> {
  await page.route('**/api/v3/portfolios/*/dashboard/today**', (route) => {
    const match = route.request().url().match(/portfolios\/(\d+)\/dashboard/)
    const portfolioId = Number(match?.[1] || 1)
    if (status >= 400) {
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ detail: 'dashboard_error' }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(handler(portfolioId)) })
  })
}

async function openDashboard(page: Page, query = ''): Promise<void> {
  await page.goto(`/dashboard${query}`)
  await expect(page.getByTestId('v3-dashboard')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('v3-dashboard-session-bar')).toBeVisible()
}

test.describe('V3 Dashboard Decision Workbench', () => {
  test('renders V3 shell, session, six indices, risk, portfolio, decision, analysis, events', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page, { sessionKind: 'MORNING' })
    await mockDashboardToday(page, (portfolioId) =>
      dashboardToday({
        portfolioId,
        decision: NO_ACTION_DECISION,
        analysis: ANALYSIS_DONE,
        finalAction: 'NO_ACTION',
      }),
    )

    await openDashboard(page)

    await expect(page.getByTestId('v3-app-shell')).toBeVisible()
    await expect(page.locator('.n-button, .n-select, .n-card')).toHaveCount(0)
    await expect(page.getByTestId('v3-session-label')).toContainText('上午交易')
    await expect(page.getByTestId('v3-quote-ts')).toBeVisible()
    await expect(page.getByTestId('v3-analysis-ts')).toBeVisible()

    for (const code of ['000001.SH', '399001.SZ', '399006.SZ', '000300.SH', '000852.SH', '000688.SH']) {
      await expect(page.getByTestId(`v3-index-${code}`)).toBeVisible()
    }
    await expect(page.getByTestId('v3-index-000001.SH')).toContainText('上证指数')
    // A股红涨绿跌
    await expect(page.getByTestId('v3-index-000001.SH')).toHaveClass(/index-card--up/)
    await expect(page.getByTestId('v3-index-399001.SZ')).toHaveClass(/index-card--down/)
    await expect(page.getByTestId('v3-index-000688.SH')).toHaveClass(/index-card--unavailable/)

    await expect(page.getByTestId('v3-risk-level-text')).toHaveText('MEDIUM')
    await expect(page.getByTestId('v3-typical-stock')).toContainText('典型个股表现')
    await expect(page.getByTestId('v3-all-a-median')).toBeVisible()
    await expect(page.getByTestId('v3-all-a-20d')).toBeVisible()
    await expect(page.getByTestId('v3-all-a-250d')).toContainText('62.5')
    await expect(page.getByTestId('v3-breadth-up')).toContainText('2,800')
    await expect(page.getByTestId('v3-limit-up')).toContainText('42')
    await expect(page.getByTestId('v3-total-turnover')).toContainText('亿')
    await expect(page.getByTestId('v3-top5-current')).toContainText('28.0%')
    await expect(page.getByTestId('v3-top5-avg20')).toContainText('24.0%')
    await expect(page.getByTestId('v3-top5-trend')).toContainText('趋向集中')

    await expect(page.getByTestId('v3-portfolio-total-assets')).toBeVisible()
    await expect(page.getByTestId('v3-portfolio-day-return')).toContainText('—')
    await expect(page.getByTestId('v3-decision-hero')).toBeVisible()
    await expect(page.getByTestId('v3-latest-analysis')).toBeVisible()
    await expect(page.getByTestId('v3-important-events')).toBeVisible()
    await expect(page.getByTestId('v3-next-checkpoint')).toContainText('快速复核')
  })

  test('explicit NO_ACTION is first-class and not green success', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page)
    await mockDashboardToday(page, (portfolioId) =>
      dashboardToday({ portfolioId, decision: NO_ACTION_DECISION, analysis: ANALYSIS_DONE, finalAction: 'NO_ACTION' }),
    )
    await openDashboard(page)
    const hero = page.getByTestId('v3-decision-hero')
    await expect(hero).toHaveAttribute('data-decision-kind', 'NO_ACTION')
    await expect(page.getByTestId('v3-decision-title')).toHaveText('今日无需操作')
    await expect(page.getByTestId('v3-decision-kind')).toContainText('NO_ACTION')
  })

  test('missing decision is NOT NO_ACTION', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page)
    // Backend fallback final_action=NO_ACTION without valid latest decision/analysis
    await mockDashboardToday(page, (portfolioId) =>
      dashboardToday({
        portfolioId,
        decision: null,
        analysis: null,
        decisionsStatus: 'MISSING',
        analysisStatus: 'MISSING',
        finalAction: 'NO_ACTION',
      }),
    )
    await openDashboard(page)
    const hero = page.getByTestId('v3-decision-hero')
    await expect(hero).toHaveAttribute('data-decision-kind', 'MISSING')
    await expect(page.getByTestId('v3-decision-title')).toHaveText('暂无有效策略结论')
  })

  test('BLOCKED decision shows structured block reason', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page)
    await mockDashboardToday(page, (portfolioId) =>
      dashboardToday({
        portfolioId,
        decision: BLOCKED_DECISION,
        analysis: ANALYSIS_DONE,
        finalAction: 'BLOCKED',
      }),
    )
    await openDashboard(page)
    const hero = page.getByTestId('v3-decision-hero')
    await expect(hero).toHaveAttribute('data-decision-kind', 'BLOCKED')
    await expect(page.getByTestId('v3-decision-title')).toHaveText('策略暂不可执行')
    await expect(page.getByTestId('v3-decision-reasons')).toContainText('数据质量不足')
  })

  test('actionable decision lists structured actions and opens drawer from code', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page)
    await mockDashboardToday(page, (portfolioId) =>
      dashboardToday({ portfolioId, decision: ACTIONABLE_DECISION, analysis: ANALYSIS_DONE, finalAction: 'ACTION' }),
    )
    await openDashboard(page)
    const hero = page.getByTestId('v3-decision-hero')
    await expect(hero).toHaveAttribute('data-decision-kind', 'ACTIONABLE')
    await expect(page.getByTestId('v3-decision-title')).toHaveText('今日需要行动')
    await expect(page.getByTestId('v3-decision-action-count')).toContainText('2')
    await expect(page.getByTestId('v3-holding-actions')).toContainText('600519.SH')
    await expect(page.getByTestId('v3-candidate-actions')).toContainText('000001.SZ')

    await page.route('**/api/v3/market/instruments/**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          identity: {
            instrument_id: '1',
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
            status: 'ACTIVE',
          },
          capabilities: { quote: true, bars: true, order_book: false, capital_flow: false, fundamentals: false, etf_profile: false, bar_intervals: ['1d'], adjustments: ['forward'] },
          data_quality: { status: 'available', quality: 'A', quality_flags: [], error_code: null },
        }),
      }),
    )
    await page.getByTestId('v3-action-code-600519.SH').click()
    await expect(page.getByTestId('instrument-detail-drawer')).toBeVisible({ timeout: 15_000 })
  })

  test('index card opens unified InstrumentDetail drawer', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page)
    await mockDashboardToday(page, (portfolioId) => dashboardToday({ portfolioId, decision: NO_ACTION_DECISION, analysis: ANALYSIS_DONE }))
    await openDashboard(page)

    await page.route('**/api/v3/market/instruments/**', (route) => {
      const url = route.request().url()
      const isIndex = url.includes('000300')
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          identity: {
            instrument_id: isIndex ? '10' : '1',
            code: isIndex ? '000300.SH' : '600519.SH',
            symbol: isIndex ? '000300' : '600519',
            exchange: 'SSE',
            name: isIndex ? '沪深300' : '贵州茅台',
            instrument_type: isIndex ? 'INDEX' : 'STOCK',
            board: null,
            currency: 'CNY',
            lot_size: null,
            is_st: false,
            is_suspended: false,
            status: 'ACTIVE',
          },
          capabilities: { quote: true, bars: true, order_book: false, capital_flow: false, fundamentals: false, etf_profile: false, bar_intervals: ['1d'], adjustments: ['none'] },
          last: 3800.8,
          change: 15.5,
          change_pct: 0.41,
          status: 'available',
          quality: 'A',
          data_basis: 'live',
          provider: 'acceptance',
          source: 'acceptance',
          fallback: false,
          observed_at: `${TRADE_DATE}T10:00:00+08:00`,
          fetched_at: `${TRADE_DATE}T10:00:00+08:00`,
          trading_date: TRADE_DATE,
        }),
      })
    })

    await page.getByTestId('v3-index-000300.SH').click()
    await expect(page.getByTestId('instrument-detail-drawer')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('instrument-detail-drawer')).toContainText('000300.SH')
  })

  test('non-trading day shows previous session close label', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page, { sessionKind: 'NON_TRADING_DAY', dataBasis: 'previous_session_close' })
    await mockDashboardToday(page, (portfolioId) => dashboardToday({ portfolioId, decision: NO_ACTION_DECISION, analysis: ANALYSIS_DONE }))
    await openDashboard(page)
    await expect(page.getByTestId('v3-session-label')).toContainText('非交易日')
    await expect(page.getByTestId('v3-previous-close')).toHaveText('上一交易日收盘')
    await expect(page.getByText('今日收盘')).toHaveCount(0)
  })

  test('portfolio race: slow A must not override selected B', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page)

    let releaseA!: () => void
    let markAStarted!: () => void
    const aGate = new Promise<void>((resolve) => { releaseA = resolve })
    const aStarted = new Promise<void>((resolve) => { markAStarted = resolve })

    await page.route('**/api/v3/portfolios/*/dashboard/today**', async (route) => {
      const match = route.request().url().match(/portfolios\/(\d+)\/dashboard/)
      const portfolioId = Number(match?.[1] || 1)
      if (portfolioId === 1) {
        markAStarted()
        await aGate
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(dashboardToday({ portfolioId: 1, totalAssets: 11000, decision: ACTIONABLE_DECISION, analysis: ANALYSIS_DONE, finalAction: 'ACTION' })),
        }).catch(() => undefined)
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(dashboardToday({ portfolioId: 2, totalAssets: 22000, decision: NO_ACTION_DECISION, analysis: ANALYSIS_DONE, finalAction: 'NO_ACTION' })),
      })
    })

    await openDashboard(page, '?portfolio=1')
    await aStarted
    await page.getByTestId('v3-portfolio-select').selectOption({ label: '组合B' })
    await expect(page.getByTestId('v3-portfolio-total-assets')).toContainText('22,000')
    await expect(page.getByTestId('v3-decision-hero')).toHaveAttribute('data-decision-kind', 'NO_ACTION')

    releaseA()
    await page.waitForTimeout(150)
    await expect(page.getByTestId('v3-portfolio-total-assets')).toContainText('22,000')
    await expect(page.getByTestId('v3-decision-hero')).toHaveAttribute('data-decision-kind', 'NO_ACTION')
  })

  test('market failure does not break portfolio decision', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page, { indicesStatus: 500, riskStatus: 500, overviewStatus: 500 })
    await mockDashboardToday(page, (portfolioId) =>
      dashboardToday({ portfolioId, decision: NO_ACTION_DECISION, analysis: ANALYSIS_DONE, finalAction: 'NO_ACTION' }),
    )
    await openDashboard(page)
    await expect(page.getByTestId('v3-decision-title')).toHaveText('今日无需操作')
    await expect(page.getByTestId('v3-portfolio-total-assets')).toBeVisible()
  })

  test('portfolio failure does not break market modules', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page)
    await mockDashboardToday(page, () => ({}), 500)
    await openDashboard(page)
    await expect(page.getByTestId('v3-index-000001.SH')).toBeVisible()
    await expect(page.getByTestId('v3-risk-level-text')).toHaveText('MEDIUM')
  })

  test('visibility resume loads session first', async ({ page }) => {
    await mockAuthAndShell(page)
    let sessionCalls = 0
    let sessionKind: SessionKind = 'PRE_OPEN'
    await page.route('**/api/v3/market/session**', (route) => {
      sessionCalls += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sessionPayload(sessionKind)) })
    })
    await mockMarket(page, { sessionKind: 'PRE_OPEN' })
    // Override session route already registered — remock after mockMarket
    await page.unroute('**/api/v3/market/session**')
    await page.route('**/api/v3/market/session**', (route) => {
      sessionCalls += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sessionPayload(sessionKind)) })
    })
    await mockDashboardToday(page, (portfolioId) => dashboardToday({ portfolioId, decision: NO_ACTION_DECISION, analysis: ANALYSIS_DONE }))
    await openDashboard(page)
    await expect(page.getByTestId('v3-session-label')).toContainText('盘前')

    sessionKind = 'MORNING'
    const before = sessionCalls
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await page.waitForTimeout(50)
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await expect(page.getByTestId('v3-session-label')).toContainText('上午交易')
    expect(sessionCalls).toBeGreaterThan(before)
  })

  test('no portfolio shows market still and no NO_ACTION', async ({ page }) => {
    await mockAuthAndShell(page)
    await mockMarket(page)
    await page.route('**/api/v2/portfolios**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
    )
    await mockDashboardToday(page, () => ({}), 404)
    await openDashboard(page)
    await expect(page.getByTestId('v3-index-000001.SH')).toBeVisible()
    await expect(page.getByTestId('v3-no-portfolio')).toBeVisible()
    const hero = page.getByTestId('v3-decision-hero')
    await expect(hero).toHaveAttribute('data-decision-kind', 'NO_PORTFOLIO')
    await expect(page.getByTestId('v3-decision-title')).toContainText('需先建立组合')
    await expect(page.getByText('今日无需操作')).toHaveCount(0)
  })

  test('mobile 375 has no horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 720 })
    await mockAuthAndShell(page)
    await mockMarket(page)
    await mockDashboardToday(page, (portfolioId) =>
      dashboardToday({ portfolioId, decision: NO_ACTION_DECISION, analysis: ANALYSIS_DONE }),
    )
    await openDashboard(page)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)
    expect(overflow).toBe(false)
    await expect(page.getByTestId('v3-portfolio-select')).toBeVisible()
    await expect(page.getByTestId('v3-index-000001.SH')).toBeVisible()
  })
})
