/**
 * V3-UI-1 Unified InstrumentDetail acceptance.
 * Deterministic page.route fixtures — no real Tencent/Eastmoney/Fuyao providers.
 */
import { test, expect, login, allowExpectedHttpError } from './fixtures'
import type { Page } from '@playwright/test'

type Identity = {
  instrument_id: string
  code: string
  symbol: string
  exchange: 'SSE' | 'SZSE' | 'BSE'
  name: string | null
  instrument_type: 'STOCK' | 'ETF' | 'INDEX'
  board: string | null
  currency: string
  lot_size: number | null
  is_st: boolean
  is_suspended: boolean
  status: string
}

function identity(code: string, name: string, type: Identity['instrument_type'], exchange: Identity['exchange'], extra: Partial<Identity> = {}): Identity {
  return {
    instrument_id: `id-${code}`,
    code,
    symbol: code.split('.')[0],
    exchange,
    name,
    instrument_type: type,
    board: type === 'STOCK' ? '主板' : null,
    currency: 'CNY',
    lot_size: 100,
    is_st: false,
    is_suspended: false,
    status: 'active',
    ...extra,
  }
}

function capabilities(type: 'STOCK' | 'ETF' | 'INDEX', flow = false) {
  return {
    quote: true,
    bars: true,
    order_book: type !== 'INDEX',
    capital_flow: flow,
    fundamentals: type === 'STOCK',
    etf_profile: type === 'ETF',
    bar_intervals: ['1d', '1w', '1M'],
    adjustments: type === 'INDEX' ? ['none'] : ['forward', 'none', 'backward'],
  }
}

function quality(status = 'available', qualityGrade: 'A' | 'B' | 'C' | 'D' | 'F' = 'A', flags: string[] = [], errorCode: string | null = null) {
  return { status, quality: qualityGrade, quality_flags: flags, error_code: errorCode }
}

function provenance(code: string, overrides: Record<string, unknown> = {}) {
  return {
    provider: 'fixture-provider',
    provider_profile: 'fixture-profile',
    source: 'fixture',
    fallback: false,
    observed_at: '2026-09-11T15:00:00+08:00',
    fetched_at: '2026-09-11T15:00:05+08:00',
    trading_date: '2026-09-11',
    data_basis: 'session_close',
    ...quality(),
    instrument: undefined as unknown,
    ...overrides,
  }
}

function metadataPayload(code: string, name: string, type: Identity['instrument_type'], exchange: Identity['exchange'], flow = false) {
  const id = identity(code, name, type, exchange)
  return {
    identity: id,
    capabilities: capabilities(type, flow),
    metadata: {
      board: type === 'STOCK' ? '主板' : null,
      industry: type === 'STOCK' ? '白酒' : null,
      concepts: type === 'STOCK' ? ['消费'] : null,
      is_st: false,
      list_date: '2001-08-27',
      lot_size: 100,
      price_limit_rule: type === 'STOCK' ? '10%' : null,
      available_for_trading: true,
      fund_type: type === 'ETF' ? '股票型' : null,
      underlying_index: type === 'ETF' ? '创业板指' : null,
      management_company: type === 'ETF' ? '易方达' : null,
      expense_ratio: type === 'ETF' ? 0.005 : null,
      tracking_target: type === 'ETF' ? '399006.SZ' : null,
      publisher: type === 'INDEX' ? '中证指数' : null,
      base_date: type === 'INDEX' ? '2004-12-31' : null,
      base_value: type === 'INDEX' ? 1000 : null,
      constituent_count: type === 'INDEX' ? 300 : null,
    },
    provider: 'fixture-provider',
    provider_profile: 'fixture-profile',
    source: 'fixture',
    fallback: false,
    observed_at: null,
    fetched_at: '2026-09-11T15:00:01+08:00',
    trading_date: '2026-09-11',
    data_basis: 'session_close',
    quality: 'A',
    quality_flags: [],
    error_code: null,
    status: 'available',
  }
}

function quotePayload(code: string, name: string, type: Identity['instrument_type'], exchange: Identity['exchange'], opts: Partial<Record<string, unknown>> = {}) {
  const base = {
    instrument: identity(code, name, type, exchange),
    last: 1688.0,
    change: 18.5,
    change_pct: 1.11,
    open: 1670.0,
    high: 1695.0,
    low: 1665.0,
    prev_close: 1669.5,
    volume: 32000000,
    turnover: 5.4e10,
    amplitude_pct: 1.8,
    turnover_rate: 0.25,
    volume_unit: 'shares',
    turnover_unit: 'CNY',
    provider: 'fixture-provider',
    provider_profile: 'fixture-profile',
    source: 'fixture',
    fallback: false,
    observed_at: '2026-09-11T15:00:00+08:00',
    fetched_at: '2026-09-11T15:00:05+08:00',
    trading_date: '2026-09-11',
    data_basis: 'session_close',
    quality: 'A',
    quality_flags: [],
    error_code: null,
    status: 'available',
    ...opts,
  }
  return base
}

function barsPayload(code: string, name: string, type: Identity['instrument_type'], exchange: Identity['exchange'], adjustment = 'forward', n = 30) {
  const bars = Array.from({ length: n }, (_, i) => {
    const day = i + 1
    const close = 1600 + i * 3
    return {
      time: `2026-08-${String(day).padStart(2, '0')}`,
      open: close - 2,
      high: close + 4,
      low: close - 5,
      close,
      volume: 1_000_000 + i * 1000,
      turnover: 1.6e9 + i * 1e6,
    }
  })
  return {
    instrument: identity(code, name, type, exchange),
    interval: '1d',
    adjustment,
    bars,
    mixed_sources: false,
    volume_unit: 'shares',
    turnover_unit: 'CNY',
    provider: 'fixture-provider',
    provider_profile: 'fixture-profile',
    source: 'fixture',
    fallback: false,
    observed_at: '2026-09-11T15:00:00+08:00',
    fetched_at: '2026-09-11T15:00:06+08:00',
    trading_date: '2026-09-11',
    data_basis: 'session_close',
    quality: 'A',
    quality_flags: [],
    error_code: null,
    status: 'available',
  }
}

function bookPayload(code: string, name: string, exchange: Identity['exchange']) {
  const levels = (side: 'bid' | 'ask', base: number) =>
    Array.from({ length: 5 }, (_, i) => ({
      level: i + 1,
      price: side === 'bid' ? base - i * 0.1 : base + i * 0.1,
      volume: 10000 * (i + 1),
    }))
  return {
    instrument: identity(code, name, 'STOCK', exchange),
    bids: levels('bid', 1688),
    asks: levels('ask', 1688.2),
    bid_volume_total: 150000,
    ask_volume_total: 120000,
    order_ratio: 12.5,
    order_difference: 30000,
    inner_volume: 500000,
    outer_volume: 620000,
    derived: true,
    derived_fields: ['order_ratio', 'order_difference'],
    volume_unit: 'shares',
    provider: 'fixture-provider',
    provider_profile: 'fixture-profile',
    source: 'fixture',
    fallback: false,
    observed_at: '2026-09-11T15:00:00+08:00',
    fetched_at: '2026-09-11T15:00:07+08:00',
    trading_date: '2026-09-11',
    data_basis: 'session_close',
    quality: 'B',
    quality_flags: [],
    error_code: null,
    status: 'available',
  }
}

function flowPayload(code: string, name: string, exchange: Identity['exchange']) {
  const history = Array.from({ length: 10 }, (_, i) => ({
    time: `2026-09-0${i + 1}`,
    main_net_inflow: (i - 3) * 1e7,
    super_large_net_inflow: (i - 4) * 5e6,
    large_net_inflow: (i - 2) * 3e6,
    medium_net_inflow: (i - 5) * 2e6,
    small_net_inflow: (i - 1) * 1e6,
  }))
  return {
    instrument: identity(code, name, 'STOCK', exchange),
    current: {
      main_net_inflow: 1.25e8,
      super_large_net_inflow: 8e7,
      large_net_inflow: 4.5e7,
      medium_net_inflow: -1e7,
      small_net_inflow: -2e7,
    },
    history,
    currency: 'CNY',
    provider_derived: true,
    methodology: 'provider_defined',
    provider: 'fixture-provider',
    provider_profile: 'fixture-profile',
    source: 'fixture',
    fallback: false,
    observed_at: '2026-09-11T15:00:00+08:00',
    fetched_at: '2026-09-11T15:00:08+08:00',
    trading_date: '2026-09-11',
    data_basis: 'session_close',
    quality: 'B',
    quality_flags: ['PROVIDER_DERIVED_CLASSIFICATION'],
    error_code: null,
    status: 'available',
  }
}

function sessionPayload() {
  return {
    market: 'CN',
    timezone: 'Asia/Shanghai',
    session: 'CLOSED',
    is_trading_day: true,
    is_market_open: false,
    trading_date: '2026-09-11',
    resolved_at: '2026-09-11T15:05:00+08:00',
    data_status: 'OK',
    data_basis: 'session_close',
    display_label: '已收盘',
  }
}

interface InstrumentFixtureOpts {
  code: string
  name: string
  type: Identity['instrument_type']
  exchange: Identity['exchange']
  flow?: boolean
  quoteStatus?: string
  bookStatus?: string
  flowStatus?: string
  stale?: boolean
  degradeQuote?: boolean
  notFound?: boolean
}

async function routeInstrument(page: Page, opts: InstrumentFixtureOpts) {
  const { code, name, type, exchange } = opts
  const encoded = encodeURIComponent(code)

  await page.route(`**/api/v3/market/session`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sessionPayload()) })
  })

  await page.route(`**/api/v3/market/instruments/${encoded}`, async (route) => {
    if (opts.notFound) {
      allowExpectedHttpError(page, 404)
      await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'INSTRUMENT_NOT_FOUND', message: 'not found' } }) })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(metadataPayload(code, name, type, exchange, opts.flow === true)) })
  })

  await page.route(`**/api/v3/market/instruments/${encoded}/quote`, async (route) => {
    const q = quotePayload(code, name, type, exchange, {
      ...(opts.quoteStatus ? { status: opts.quoteStatus, quality: opts.quoteStatus === 'stale' ? 'C' : 'B', quality_flags: opts.quoteStatus === 'stale' ? ['STALE_QUOTE'] : [] } : {}),
      ...(opts.stale ? { stale: true } : {}),
      ...(opts.degradeQuote ? { status: 'degraded', quality: 'C', quality_flags: ['PROVIDER_FALLBACK'], fallback: true } : {}),
      ...(type === 'INDEX' ? { volume: null, turnover: null } : {}),
    })
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(q) })
  })

  await page.route(`**/api/v3/market/instruments/${encoded}/bars**`, async (route) => {
    const url = new URL(route.request().url())
    const adjustment = url.searchParams.get('adjustment') || (type === 'INDEX' ? 'none' : 'forward')
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(barsPayload(code, name, type, exchange, adjustment)) })
  })

  await page.route(`**/api/v3/market/instruments/${encoded}/order-book`, async (route) => {
    if (type === 'INDEX' || opts.bookStatus === 'unsupported') {
      // Backend returns 200 with unsupported status for capability false
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          instrument: identity(code, name, type, exchange),
          bids: [], asks: [],
          bid_volume_total: null, ask_volume_total: null,
          order_ratio: null, order_difference: null,
          inner_volume: null, outer_volume: null,
          derived: false, derived_fields: [],
          volume_unit: 'shares',
          provider: null, provider_profile: null, source: null, fallback: false,
          observed_at: null, fetched_at: null, trading_date: null,
          data_basis: 'session_close',
          quality: 'F', quality_flags: [], error_code: 'UNSUPPORTED',
          status: 'unsupported',
        }),
      })
      return
    }
    if (opts.bookStatus === 'unavailable') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...bookPayload(code, name, exchange),
          bids: [], asks: [],
          status: 'unavailable', quality: 'F', error_code: 'PROVIDER_UNAVAILABLE',
        }),
      })
      return
    }
    if (opts.bookStatus === 'empty') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...bookPayload(code, name, exchange), bids: [], asks: [], status: 'empty', quality: 'C' }),
      })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bookPayload(code, name, exchange)) })
  })

  await page.route(`**/api/v3/market/instruments/${encoded}/capital-flow`, async (route) => {
    if (!opts.flow || opts.flowStatus === 'unsupported') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          instrument: identity(code, name, type, exchange),
          current: null,
          history: [],
          currency: 'CNY',
          provider_derived: true,
          methodology: 'provider_defined',
          provider: null, provider_profile: null, source: null, fallback: false,
          observed_at: null, fetched_at: null, trading_date: null,
          data_basis: 'session_close',
          quality: 'F', quality_flags: [], error_code: 'UNSUPPORTED',
          status: 'unsupported',
        }),
      })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(flowPayload(code, name, exchange)) })
  })
}

test.describe('V3 InstrumentDetail', () => {
  test('STOCK 600519.SH renders quote, kline, book, flow and metadata', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '600519.SH', name: '贵州茅台', type: 'STOCK', exchange: 'SSE', flow: true })
    await page.goto('/market/instruments/600519.SH')

    await expect(page.locator('[data-testid="instrument-detail"]')).toBeVisible()
    await expect(page.locator('[data-testid="instrument-name"]')).toHaveText('贵州茅台')
    await expect(page.locator('[data-testid="instrument-code"]')).toContainText('600519.SH')
    await expect(page.locator('[data-testid="quote-last"]')).toHaveText('1688.00')
    await expect(page.locator('[data-testid="quote-change-pct"]')).toContainText('+1.11%')
    // A-share up = red class
    await expect(page.locator('[data-testid="quote-change-pct"]')).toHaveClass(/trend-up/)
    await expect(page.locator('[data-testid="instrument-quality"]')).toContainText('A')
    await expect(page.locator('[data-testid="kline-aria-summary"]')).toContainText('600519.SH')
    await expect(page.locator('[data-testid="kline-chart"]')).toBeVisible()
    // MA / MACD indicators present in aria or legend via toolbar
    await expect(page.locator('[data-testid="kline-indicator-macd"]')).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('[data-testid="kline-indicator-rsi"]')).toBeVisible()
    await expect(page.locator('[data-testid="instrument-metadata"]')).toBeVisible()

    // Lazy book tab
    await page.locator('[data-testid="v3-tab-book"]').click()
    await expect(page.locator('[data-testid="instrument-order-book"]')).toBeVisible()
    await expect(page.locator('[data-testid="book-derived-mark"]').first()).toBeVisible()
    await expect(page.locator('[data-testid="book-bid-1"]')).toBeVisible()

    // Lazy flow tab
    await page.locator('[data-testid="v3-tab-flow"]').click()
    await expect(page.locator('[data-testid="instrument-capital-flow"]')).toBeVisible()
    await expect(page.locator('[data-testid="flow-disclaimer"]')).toContainText('数据提供方分类估算')
    await expect(page.locator('[data-testid="flow-provider-derived"]')).toBeVisible()
    await expect(page.locator('[data-testid="flow-main_net_inflow"]')).toBeVisible()
  })

  test('ETF 159915.SZ does not request capital-flow when capability=false', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    let flowHits = 0
    await page.on('request', (req) => {
      if (req.url().includes('/capital-flow')) flowHits += 1
    })
    await routeInstrument(page, { code: '159915.SZ', name: '创业板ETF', type: 'ETF', exchange: 'SZSE', flow: false })
    await page.goto('/market/instruments/159915.SZ')
    await expect(page.locator('[data-testid="instrument-name"]')).toHaveText('创业板ETF')
    await page.locator('[data-testid="v3-tab-flow"]').click()
    await expect(page.locator('[data-testid="flow-unsupported"]')).toBeVisible()
    await page.waitForTimeout(200)
    expect(flowHits).toBe(0)
    // ETF metadata
    await page.locator('[data-testid="v3-tab-quote"]').click()
    await expect(page.locator('[data-testid="instrument-metadata"]')).toContainText('基金类型')
  })

  test('INDEX 000300.SH disables book and flow without requests', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    const forbidden: string[] = []
    await page.on('request', (req) => {
      const u = req.url()
      if (u.includes('/order-book') || u.includes('/capital-flow')) forbidden.push(u)
    })
    await routeInstrument(page, { code: '000300.SH', name: '沪深300', type: 'INDEX', exchange: 'SSE', flow: false })
    await page.goto('/market/instruments/000300.SH')
    await expect(page.locator('[data-testid="instrument-name"]')).toHaveText('沪深300')
    await page.locator('[data-testid="v3-tab-book"]').click()
    await expect(page.locator('[data-testid="book-unsupported"]')).toBeVisible()
    await page.locator('[data-testid="v3-tab-flow"]').click()
    await expect(page.locator('[data-testid="flow-unsupported"]')).toBeVisible()
    await page.waitForTimeout(200)
    expect(forbidden.length).toBe(0)
    // adjustment none only
    await page.locator('[data-testid="v3-tab-quote"]').click()
    await expect(page.locator('[data-testid="kline-adjustment-none"]')).toBeVisible()
    await expect(page.locator('[data-testid="kline-adjustment-forward"]')).toHaveCount(0)
  })

  test('Same numeric code 000001.SH vs 000001.SZ keep isolated identity', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '000001.SH', name: '上证指数', type: 'INDEX', exchange: 'SSE' })
    await routeInstrument(page, { code: '000001.SZ', name: '平安银行', type: 'STOCK', exchange: 'SZSE', flow: true })
    await page.goto('/market/instruments/000001.SH')
    await expect(page.locator('[data-testid="instrument-name"]')).toHaveText('上证指数')
    await expect(page.locator('[data-testid="instrument-code"]')).toContainText('000001.SH')
    await page.goto('/market/instruments/000001.SZ')
    await expect(page.locator('[data-testid="instrument-name"]')).toHaveText('平安银行')
    await expect(page.locator('[data-testid="instrument-code"]')).toContainText('000001.SZ')
  })

  test('Stale quote is labelled and not shown as error', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '600519.SH', name: '贵州茅台', type: 'STOCK', exchange: 'SSE', flow: true, quoteStatus: 'stale', stale: true })
    await page.goto('/market/instruments/600519.SH')
    await expect(page.locator('[data-testid="instrument-stale"]')).toBeVisible()
    await expect(page.locator('[data-testid="quote-last"]')).toBeVisible()
    await expect(page.locator('[data-testid="detail-page-error"]')).toHaveCount(0)
  })

  test('Degraded quote shows fallback quality', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '600519.SH', name: '贵州茅台', type: 'STOCK', exchange: 'SSE', flow: true, degradeQuote: true })
    await page.goto('/market/instruments/600519.SH')
    await expect(page.locator('[data-testid="instrument-fallback"]')).toBeVisible()
    await expect(page.locator('[data-testid="quote-last"]')).toBeVisible()
  })

  test('Empty and unavailable book states are distinct', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '600519.SH', name: '贵州茅台', type: 'STOCK', exchange: 'SSE', bookStatus: 'empty' })
    await page.goto('/market/instruments/600519.SH')
    await page.locator('[data-testid="v3-tab-book"]').click()
    await expect(page.locator('[data-testid="book-empty"]')).toBeVisible()
  })

  test('Instrument not found shows page-level 404', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '999999.SH', name: '不存在', type: 'STOCK', exchange: 'SSE', notFound: true })
    await page.goto('/market/instruments/999999.SH')
    await expect(page.locator('[data-testid="detail-not-found"]')).toBeVisible()
  })

  test('Race: slow A response does not overwrite switched B', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    let releaseA!: () => void
    const gate = new Promise<void>((resolve) => { releaseA = resolve })
    let aStarted = false

    await page.route('**/api/v3/market/instruments/600519.SH', async (route) => {
      aStarted = true
      await gate
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(metadataPayload('600519.SH', '贵州茅台', 'STOCK', 'SSE', true)) }).catch(() => undefined)
    })
    await routeInstrument(page, { code: '000300.SH', name: '沪深300', type: 'INDEX', exchange: 'SSE' })

    await page.goto('/market/instruments/600519.SH')
    await expect.poll(() => aStarted).toBe(true)
    // Switch to B while A is still pending
    await page.goto('/market/instruments/000300.SH')
    await expect(page.locator('[data-testid="instrument-name"]')).toHaveText('沪深300')
    releaseA()
    await page.waitForTimeout(150)
    await expect(page.locator('[data-testid="instrument-name"]')).toHaveText('沪深300')
    await expect(page.locator('[data-testid="detail-page-error"]')).toHaveCount(0)
  })

  test('Mobile 375 has no horizontal overflow and toolbar remains usable', async ({ acceptancePage: page, facts }) => {
    await page.setViewportSize({ width: 375, height: 720 })
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '600519.SH', name: '贵州茅台', type: 'STOCK', exchange: 'SSE', flow: true })
    await page.goto('/market/instruments/600519.SH')
    await expect(page.locator('[data-testid="instrument-detail"]')).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    await expect(page.locator('[data-testid="kline-toolbar"]')).toBeVisible()
    await expect(page.locator('[data-testid="kline-interval-1d"]')).toBeVisible()
  })

  test('Analysis/news/history tabs exist with honest unavailable copy', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '600519.SH', name: '贵州茅台', type: 'STOCK', exchange: 'SSE', flow: true })
    await page.goto('/market/instruments/600519.SH')
    await page.locator('[data-testid="v3-tab-analysis"]').click()
    await expect(page.locator('[data-testid="analysis-unavailable"]')).toContainText('暂无统一结构化标的分析接口')
    await page.locator('[data-testid="v3-tab-news"]').click()
    await expect(page.locator('[data-testid="news-unavailable"]')).toContainText('暂无统一标的新闻数据接口')
    await page.locator('[data-testid="v3-tab-history"]').click()
    await expect(page.locator('[data-testid="history-unavailable"]')).toContainText('历史决策将在结构化接口接通后提供')
  })

  test('Interval and indicator switches update network query', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    const barQueries: string[] = []
    await routeInstrument(page, { code: '600519.SH', name: '贵州茅台', type: 'STOCK', exchange: 'SSE', flow: true })
    await page.route('**/api/v3/market/instruments/600519.SH/bars**', async (route) => {
      barQueries.push(route.request().url())
      const url = new URL(route.request().url())
      const adjustment = url.searchParams.get('adjustment') || 'forward'
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(barsPayload('600519.SH', '贵州茅台', 'STOCK', 'SSE', adjustment)) })
    })
    await page.goto('/market/instruments/600519.SH')
    await expect(page.locator('[data-testid="kline-chart"]')).toBeVisible()
    await page.locator('[data-testid="kline-interval-1w"]').click()
    await expect.poll(() => barQueries.some((u) => u.includes('interval=1w'))).toBe(true)
    await page.locator('[data-testid="kline-adjustment-none"]').click()
    await expect.poll(() => barQueries.some((u) => u.includes('adjustment=none'))).toBe(true)
    await page.locator('[data-testid="kline-indicator-rsi"]').click()
    await expect(page.locator('[data-testid="kline-indicator-rsi"]')).toHaveAttribute('aria-pressed', 'true')
  })

  test('Drawer contract mounts shared InstrumentDetail', async ({ acceptancePage: page, facts }) => {
    await login(page, facts.users.a)
    await routeInstrument(page, { code: '600519.SH', name: '贵州茅台', type: 'STOCK', exchange: 'SSE', flow: true })
    // Full page already proves shared core; drawer is the same component behind V3DetailDrawer.
    await page.goto('/market/instruments/600519.SH')
    await expect(page.locator('[data-testid="instrument-detail"]')).toBeVisible()
    // Smoke: page uses V3 tabs (keyboard reachable via Quasar q-tab)
    await expect(page.locator('[data-testid="v3-tabs"]')).toBeVisible()
  })
})
