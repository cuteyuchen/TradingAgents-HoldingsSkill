const assert = require('node:assert/strict')
const { beforeEach, test } = require('node:test')
const { loadWithMocks, loadVueWithMocks } = require('./register-typescript.cjs')

// 先以 Node/SSR 环境载入现有 UI 依赖，测试只提供事件和存储，不伪造浏览器 DOM。
require('naive-ui')
const storage = new Map()
global.localStorage = {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: key => storage.delete(key),
}
global.window = new EventTarget()
window.localStorage = localStorage
// 不等待生产环境的 30 秒截止时间，仍让真实 AbortController 和响应读取执行。
window.setTimeout = (callback, delay) => setTimeout(callback, delay >= 30_000 ? 25 : delay)
window.clearTimeout = clearTimeout
Object.defineProperty(global, 'navigator', { configurable: true, value: { userAgent: 'offline-review' } })

const client = require('../src/api/index.ts')
const portfolio = require('../src/composables/portfolio.ts')
const holdings = require('../src/v3/holdings/holdings-formatters.ts')
const { observationTimeToIso } = require('../src/utils/observation-time.ts')
const vue = require('vue')
const mocks = {
  vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
  'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ replace: async () => {} }) },
}
const dashboardController = loadWithMocks('../src/v3/dashboard/useV3Dashboard.ts', mocks)
const holdingsController = loadWithMocks('../src/v3/holdings/useV3Holdings.ts', mocks)
const instrumentController = loadWithMocks('../src/v3/instrument-detail/useInstrumentDetail.ts', mocks)
const updateController = loadWithMocks('../src/v3/holdings/useV3HoldingsUpdate.ts', mocks)
const tokens = name => ({ access_token: `${name}-access`, refresh_token: `${name}-refresh`, token_type: 'bearer', expires_in: 60 })
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}
const flush = () => new Promise(resolve => setImmediate(resolve))

beforeEach(() => {
  client.clearSession()
  storage.clear()
  global.fetch = async () => json({})
})

function holding(overrides = {}) {
  return {
    code: '600519', canonical_code: '600519.SH', resolution_status: 'RESOLVED', security_id: 1,
    qty: 100, available_qty: 0, cost: 10, market_value: 1000, pnl_amount: 20, pnl: 0.02,
    ...overrides,
  }
}

function quote(overrides = {}) {
  return holdings.mapQuoteItem({
    code: '600519.SH', last: 12, prev_close: 11, change: 1, change_pct: 9.09,
    quality: 'A', status: 'available', data_basis: 'live', trading_date: '2026-10-13',
    observed_at: '2026-10-13T10:00:00+08:00', ...overrides,
  })
}

function view(overrides = {}) {
  return holdings.buildHoldingsViewModel({
    hasPortfolio: true, portfolioId: 1, portfolioName: '测试组合',
    snapshot: { id: 1, portfolio_id: 1, snapshot_time: '2026-10-13T09:00:00+08:00', holdings: [holding()], total_market_value: 1000 },
    dashboard: {
      as_of: '2026-10-13T10:00:00+08:00', trade_date: '2026-10-13', portfolio: { snapshot_id: 1 },
      decisions: { latest: { portfolio_snapshot_id: 1, conclusion: 'ADD', holding_actions: [{ code: '600519', action: 'ADD' }] } },
    },
    quotes: new Map([['600519.SH', quote()]]), quotesAsOf: '2026-10-13T10:00:00+08:00',
    session: 'MORNING', sessionTradingDate: '2026-10-13', sessionLabel: '上午交易', filter: 'all', sort: 'default',
    ...overrides,
  })
}

test('Shadow: 可卖未知值与已证明下界分别保留，已知零和旧响应不被改写', async () => {
  for (const sellable of [
    { sellable_quantity: 0, sellable_quantity_minimum: 0, sellable_quantity_known: true },
    { sellable_quantity: 100, sellable_quantity_minimum: 100, sellable_quantity_known: true },
    { sellable_quantity: null, sellable_quantity_minimum: 0, sellable_quantity_known: false },
    { sellable_quantity: null, sellable_quantity_minimum: 100, sellable_quantity_known: false },
    { sellable_quantity: 0 },
    { sellable_quantity: 100 },
    { sellable_quantity: null },
  ]) {
    const position = { id: 1, code: '600519.SH', quantity: 200, average_cost: 10, ...sellable }
    const account = { id: 1, positions: [position] }
    global.fetch = async () => json(account)
    const detail = await client.api.getShadowAccount(1)
    assert.deepEqual(detail, account)
    // 技术详情的 JSON 输出也不能用下界替换未知总量，或把 null 转成零。
    assert.deepEqual(JSON.parse(JSON.stringify(detail)).positions[0], position)
    global.fetch = async () => json([account])
    assert.deepEqual(await client.api.listShadowAccounts(1), [account])
  }
})

test('N13: 可卖数量为零只阻断卖出，不能单独阻断加仓', () => {
  assert.equal(view().rows[0].execution.blocking, false)
  for (const action of ['REDUCE', 'EXIT']) {
    const result = view({ dashboard: {
      as_of: '2026-10-13T10:00:00+08:00', portfolio: { snapshot_id: 1 },
      decisions: { latest: { portfolio_snapshot_id: 1, conclusion: action, holding_actions: [{ code: '600519', action }] } },
    } })
    assert.equal(result.rows[0].execution.blocking, true)
    assert.equal(result.rows[0].judgment.status, action)
  }
  const blocked = view({ dashboard: {
    as_of: '2026-10-13T10:00:00+08:00', portfolio: { snapshot_id: 1 },
    decisions: { latest: { portfolio_snapshot_id: 1, quality: 'BLOCKED', conclusion: 'ADD', holding_actions: [{ code: '600519', action: 'ADD' }] } },
  } })
  assert.equal(blocked.rows[0].judgment.status, 'ADD')
  assert.equal(blocked.rows[0].execution.blocking, true)
  assert.equal(holdings.normalizeHoldingStatus('UNKNOWN_ACTION'), 'DATA_INSUFFICIENT')
})

test('N14: 无效、昨日收盘及过期行情不得进入当期估值或当日盈亏', () => {
  for (const payload of [
    { quality: 'INVALID' }, { quality: 'CONFLICT' }, { status: 'CONFLICT' }, { status: 'INVALID' },
    { quality: null }, { status: null }, { quality: 'UNKNOWN' }, { status: 'UNKNOWN' },
    { last: 0 }, { last: -1 },
    ...['QUOTE_INVALID', 'INVALID_OBSERVED_TIME', 'PRICE_CONFLICT', 'QUOTE_CHANGE_CONFLICT',
      'FUTURE_OBSERVED_TIME', 'OBSERVED_DATE_MISMATCH'].map(flag => ({ quality_flags: [flag] })),
  ]) {
    const mapped = quote(payload)
    assert.equal(mapped.status, 'MISSING', JSON.stringify(payload))
    const result = view({ quotes: new Map([['600519.SH', mapped]]) })
    assert.equal(result.rows[0].quoteDisplayable, false)
    assert.equal(result.rows[0].liveMarketValueEstimate, null)
    assert.equal(result.rows[0].dayPnlEstimate, null)
    assert.equal(result.rows[0].markedPnlEstimate, null)
    assert.equal(result.rows[0].quoteExecutionEligible, false)
    assert.equal(result.rows[0].execution.blocking, true)
    assert.equal(result.summary.marketValueSource, 'snapshot')
  }
  for (const payload of [
    { status: 'unavailable', quality: 'F' },
    { status: 'stale', quality: 'C' },
    { quality_flags: ['QUOTE_STALE'] }, { quality_flags: ['STALE_TRADING_DATE'] },
    { quality_flags: ['CLOSE_NOT_CONFIRMED'] },
    { trading_date: '2026-10-12', data_basis: 'previous_session_close' },
    { trading_date: null },
  ]) {
    const result = view({ quotes: new Map([['600519.SH', quote(payload)]]) })
    assert.equal(result.rows[0].liveMarketValueEstimate, null)
    assert.equal(result.rows[0].dayPnlEstimate, null)
    assert.equal(result.rows[0].markedPnlEstimate, null)
    assert.equal(result.rows[0].quoteExecutionEligible, false)
    assert.equal(result.summary.marketValueSource, 'snapshot')
  }
  assert.equal(view({ quotes: new Map([['600519.SH', quote({ status: 'unavailable', quality: 'F' })]]) }).rows[0].quoteDisplayable, false)
  const previous = view({ quotes: new Map([['600519.SH', quote({ trading_date: '2026-10-12', data_basis: 'previous_session_close' })]]) })
  assert.equal(previous.rows[0].quoteDisplayable, true)
  assert.equal(previous.rows[0].quote.tradingDate, '2026-10-12')
  for (const [session, data_basis] of [['LUNCH_BREAK', 'live'], ['CLOSED', 'session_close']]) {
    const result = view({ session, quotes: new Map([['600519.SH', quote({ data_basis })]]) })
    assert.equal(result.rows[0].liveMarketValueEstimate, 1200)
    assert.equal(result.rows[0].dayPnlEstimate, 100)
    assert.equal(result.rows[0].quoteExecutionEligible, false)
  }
})

test('N15: 混合持仓以全部有效账户行作为分母，缺数量与 ledger-only 持仓不能被遗漏', () => {
  const snapshot = {
    id: 1, portfolio_id: 1, holdings: [
      holding(),
      holding({ code: 'unknown', canonical_code: null, resolution_status: 'UNRESOLVED', qty: 900, market_value: 9000, pnl_amount: null }),
      holding({ code: '000001', canonical_code: '000001.SZ', qty: null }),
      holding({ code: '000002', canonical_code: '000002.SZ', qty: 0 }),
    ], total_market_value: 10_000,
  }
  const accountState = {
    portfolio_id: 1, snapshot_id: 1, account_derivation: { entry_count: 1 },
    positions: [
      { code: '600519', canonical_code: '600519.SH', qty: 100, available_qty: 0, position_changed: false, current_cost: 10, cost_status: 'KNOWN' },
      { code: '159915', canonical_code: '159915.SZ', qty: 100, position_changed: true, current_cost: 2, cost_status: 'KNOWN' },
    ],
  }
  const result = view({ snapshot, accountState })
  assert.equal(result.summary.positionCount, 4)
  assert.equal(result.summary.identityCoverage, 3 / 4)
  assert.equal(result.summary.quoteCoverage, 1 / 4)
  assert.equal(result.summary.valuationCoverage, 1 / 4)
  assert.equal(result.summary.marketValue, 10_000)
  assert.equal(result.summary.marketValueSource, 'snapshot')
  assert.equal(result.summary.dayPnlAvailable, false)
  assert.equal(result.summary.floatingPnlAvailable, false)
})

test('N09: 净数量不变的往返交易使用账户成本，未知成本不沿用旧快照', () => {
  for (const cost_status of ['KNOWN', 'UNKNOWN']) {
    const result = view({ accountState: {
      portfolio_id: 1, snapshot_id: 1, account_derivation: { entry_count: 2 },
      positions: [{ code: '600519', qty: 100, available_qty: 50, qty_delta: 0, current_cost: 12, cost_status, position_changed: true, position_version: 'round-trip' }],
    } })
    assert.equal(result.rows[0].cost, cost_status === 'KNOWN' ? 12 : null)
    assert.equal(result.rows[0].snapshotPnlAmount, null)
    assert.equal(result.rows[0].dayPnlEstimate, null)
    assert.equal(result.rows[0].positionVersion, 'round-trip')
  }
  const legacy = view({ accountState: {
    account_derivation: { entry_count: 2 }, positions: [{ code: '600519', qty: 100, qty_delta: 0 }],
  } })
  assert.equal(legacy.rows[0].cost, null)
})

test('N16: 请求和响应体超时保持 timeout，主动取消才可被控制器静默忽略', async () => {
  for (const makeFetch of [
    () => new Promise(() => {}),
    () => Promise.resolve({ ok: true, status: 200, headers: new Headers(), json: () => new Promise(() => {}) }),
  ]) {
    global.fetch = makeFetch
    await assert.rejects(client.api.me(), error => {
      assert.equal(error.kind, 'timeout')
      for (const controller of [dashboardController, holdingsController, instrumentController]) assert.equal(controller.isAbortError(error), false)
      return true
    })
  }
  const controller = new AbortController()
  global.fetch = () => new Promise(() => {})
  const pending = client.api.getMarketSession(controller.signal)
  controller.abort()
  await assert.rejects(pending, error => error.kind === 'cancelled' && dashboardController.isAbortError(error))
})

test('N16: 后台刷新超时保留旧值、呈现错误且 loading 收敛', async () => {
  client.saveSession(tokens('A'))
  portfolio.usePortfolioContext().portfolios.value = [{ id: 1, name: 'A', latest_snapshot_id: 1 }]
  portfolio.setSelectedPortfolio(1)
  const controller = dashboardController.useV3Dashboard()
  global.fetch = async () => json({ portfolio: { snapshot_id: 1 }, decisions: {} })
  await controller.loadPortfolioDashboard()
  const previous = controller.currentPortfolioDashboard.value
  global.fetch = () => new Promise(() => {})
  await controller.loadPortfolioDashboard(true)
  assert.strictEqual(controller.currentPortfolioDashboard.value, previous)
  assert.equal(controller.currentPortfolioError.value.kind, 'timeout')
  assert.equal(controller.portfolioLoading.value, false)
})

test('N16: 401 等待共享刷新时主动取消立即返回，其他请求仍可完成刷新', async () => {
  client.saveSession(tokens('A'))
  const refresh = deferred()
  let refreshSignal
  global.fetch = async (path, init) => {
    if (path.endsWith('/refresh')) { refreshSignal = init.signal; return refresh.promise }
    return init.headers.Authorization === 'Bearer A-new-access' ? json({ id: 1 }) : json({}, 401)
  }
  const controller = new AbortController()
  const cancelled = client.api.getMarketSession(controller.signal)
  const checked = assert.rejects(cancelled, error => error.kind === 'cancelled')
  const shared = client.api.me()
  while (!refreshSignal) await flush()
  controller.abort()
  await checked
  assert.equal(refreshSignal.aborted, false)
  refresh.resolve(json(tokens('A-new')))
  assert.deepEqual(await shared, { id: 1 })
  assert.equal(client.getAccessToken(), 'A-new-access')
})

test('N17: 迟到登录及注册两阶段响应不能穿过换号或退出', async () => {
  for (const stage of ['login', 'register', 'register-login']) {
    for (const replacement of [null, tokens('B')]) {
      const response = deferred()
      let delayed = false
      let loginCount = 0
      global.fetch = async path => {
        if (path.endsWith('/login')) loginCount += 1
        if (stage === 'register-login' && path.endsWith('/register')) return json({ id: 1 })
        delayed = true
        return response.promise
      }
      const request = stage === 'login'
        ? client.api.login({ email: 'a@example.test', password: 'secret' })
        : client.api.register({ email: 'a@example.test', password: 'secret' })
      const checked = assert.rejects(request, error => error.kind === 'cancelled')
      while (!delayed) await flush()
      if (replacement) client.saveSession(replacement)
      else client.clearSession()
      response.resolve(json(stage === 'register' ? { id: 1 } : tokens('A-late')))
      await checked
      assert.equal(client.getAccessToken(), replacement?.access_token || '')
      assert.equal(loginCount, stage === 'register' ? 0 : 1)
    }
  }
})

test('N17: 登录页面卸载或会话代际改变后不能提交迟到凭证', async () => {
  for (const mode of ['login', 'register']) {
    for (const reason of ['unmount', 'replace', 'logout']) {
      client.clearSession()
      const response = deferred()
      let unmount
      let successCount = 0
      let redirectCount = 0
      const component = loadVueWithMocks('../src/views/LoginView.vue', {
        vue: { ...vue, onBeforeUnmount: callback => { unmount = callback } },
        'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ replace: async () => { redirectCount += 1 } }) },
        'naive-ui': { useMessage: () => ({ success: () => { successCount += 1 } }) },
        '../api': { ...client, api: { login: () => response.promise, register: () => response.promise } },
        '../components/AppVersion.vue': {},
      })
      const form = component.setup({}, { expose() {} })
      form.mode.value = mode
      form.email.value = 'a@example.test'
      form.password.value = form.confirmPassword.value = 'secret'
      const pending = form.submit()
      if (reason === 'unmount') unmount()
      else if (reason === 'replace') client.saveSession(tokens('B'))
      else client.clearSession()
      response.resolve(tokens('A-late'))
      await pending
      assert.equal(client.getAccessToken(), reason === 'replace' ? 'B-access' : '')
      assert.equal(successCount, 0)
      assert.equal(redirectCount, 0)
    }
  }
})

test('N17: API 完成后存储事件尚未送达时，登录及注册提交仍须核对持久会话归属', async () => {
  for (const mode of ['login', 'register']) {
    client.clearSession()
    const generation = client.getSessionGeneration()
    let successCount = 0
    let redirectCount = 0
    global.fetch = async path => json(path.endsWith('/register') ? { id: 1 } : tokens('A'))
    const submitAuth = payload => client.api[mode](payload).then(result => {
      // 真实请求已通过其末尾检查，但另一标签页在 LoginView await 续跑前写入新会话。
      localStorage.setItem('advisor_v2_access_token', 'B-access')
      localStorage.setItem('advisor_v2_refresh_token', 'B-refresh')
      localStorage.setItem('advisor_v2_session_id', 'B-session')
      return result
    })
    const component = loadVueWithMocks('../src/views/LoginView.vue', {
      vue: { ...vue, onBeforeUnmount() {} },
      'vue-router': { useRoute: () => ({ query: {} }), useRouter: () => ({ replace: async () => { redirectCount += 1 } }) },
      'naive-ui': { useMessage: () => ({ success: () => { successCount += 1 } }) },
      '../api': { ...client, api: { ...client.api, login: submitAuth, register: submitAuth } },
      '../components/AppVersion.vue': {},
    })
    const form = component.setup({}, { expose() {} })
    form.mode.value = mode
    form.email.value = 'a@example.test'
    form.password.value = form.confirmPassword.value = 'secret'
    await form.submit()
    assert.equal(client.getSessionGeneration(), generation)
    assert.equal(client.getSessionId(), 'B-session')
    assert.equal(client.getAccessToken(), 'B-access')
    assert.equal(client.getRefreshToken(), 'B-refresh')
    assert.equal(successCount, 0)
    assert.equal(redirectCount, 0)
  }
})

test('N17: 刷新期间退出或登录 B，迟到 A 响应永远不能恢复或覆盖会话', async () => {
  for (const replacement of [null, tokens('B')]) {
    client.saveSession(tokens('A'))
    const refresh = deferred()
    let started = false
    global.fetch = async path => {
      if (path.endsWith('/refresh')) { started = true; return refresh.promise }
      return json({ detail: 'expired' }, 401)
    }
    const pending = client.api.me()
    const checked = assert.rejects(pending, error => error.kind === 'cancelled')
    while (!started) await flush()
    if (replacement) client.saveSession(replacement)
    else client.clearSession()
    refresh.resolve(json(tokens('A-late')))
    await checked
    await flush()
    assert.equal(client.getAccessToken(), replacement?.access_token || '')
    assert.equal(client.getRefreshToken(), replacement?.refresh_token || '')
  }
})

test('N17: 并行 401 去重刷新；刷新拒绝清空会话，刷新超时不误登出', async () => {
  client.saveSession(tokens('A'))
  let refreshCount = 0
  const refresh = deferred()
  global.fetch = async (path, init) => {
    if (path.endsWith('/refresh')) { refreshCount += 1; return refresh.promise }
    return init.headers.Authorization === 'Bearer A-new-access' ? json({ id: 1 }) : json({}, 401)
  }
  const results = Promise.all([client.api.me(), client.api.me()])
  while (!refreshCount) await flush()
  refresh.resolve(json(tokens('A-new')))
  assert.deepEqual(await results, [{ id: 1 }, { id: 1 }])
  assert.equal(refreshCount, 1)
  global.fetch = async () => json({}, 401)
  await assert.rejects(client.api.me(), error => error.kind === 'auth')
  assert.equal(client.hasSession(), false)
  client.saveSession(tokens('A'))
  global.fetch = async path => path.endsWith('/refresh') ? new Promise(() => {}) : json({}, 401)
  await assert.rejects(client.api.me(), error => error.kind === 'timeout')
  assert.equal(client.getAccessToken(), 'A-access')
})

test('N17: 多标签页换号及迟到错误不能重写组合缓存', async () => {
  client.saveSession(tokens('A'))
  const request = deferred()
  global.fetch = () => request.promise
  const pending = portfolio.loadPortfolios()
  const revision = client.getSessionGeneration()
  localStorage.setItem('advisor_v2_access_token', 'B-access')
  localStorage.setItem('advisor_v2_refresh_token', 'B-refresh')
  localStorage.setItem('advisor_v2_session_id', 'B-session')
  const event = new Event('storage')
  Object.defineProperties(event, { key: { value: 'advisor_v2_session_id' }, storageArea: { value: localStorage } })
  window.dispatchEvent(event)
  assert.ok(client.getSessionGeneration() > revision)
  request.reject(new Error('A-private-error'))
  assert.deepEqual(await pending, [])
  assert.deepEqual(portfolio.usePortfolioContext().portfolios.value, [])
  assert.equal(portfolio.usePortfolioContext().selectedPortfolioId.value, null)
  assert.equal(portfolio.usePortfolioContext().error.value, null)
  assert.equal(portfolio.usePortfolioContext().loading.value, false)
})

test('N17: 同会话标签页轮换保留组合选择，旧 refresh 拒绝不能清掉新令牌', async () => {
  for (const dispatchStorageEvent of [false, true]) {
    client.saveSession(tokens('A'))
    const context = portfolio.usePortfolioContext()
    context.portfolios.value = [{ id: 1, name: 'A' }]
    portfolio.setSelectedPortfolio(1)
    const generation = client.getSessionGeneration()
    const refresh = deferred()
    let started = false
    global.fetch = async (path, init) => {
      if (path.endsWith('/refresh')) { started = true; return refresh.promise }
      return init.headers.Authorization === 'Bearer A-rotated-access' ? json({ id: 1 }) : json({}, 401)
    }
    const pending = client.api.me()
    while (!started) await flush()
    localStorage.setItem('advisor_v2_access_token', 'A-rotated-access')
    localStorage.setItem('advisor_v2_refresh_token', 'A-rotated-refresh')
    if (dispatchStorageEvent) {
      for (const key of ['advisor_v2_access_token', 'advisor_v2_refresh_token']) {
        const event = new Event('storage')
        Object.defineProperties(event, { key: { value: key }, storageArea: { value: localStorage } })
        window.dispatchEvent(event)
      }
    }
    refresh.resolve(json({}, 401))
    assert.deepEqual(await pending, { id: 1 })
    assert.equal(client.getSessionGeneration(), generation)
    assert.equal(client.getRefreshToken(), 'A-rotated-refresh')
    assert.deepEqual(context.portfolios.value, [{ id: 1, name: 'A' }])
    assert.equal(context.selectedPortfolioId.value, 1)
  }
})

test('N07: 截图事实时间按北京时间转换，缺失与无效日期不能确认', async () => {
  assert.equal(observationTimeToIso(''), null)
  assert.equal(observationTimeToIso('2026-02-30T10:00'), null)
  assert.equal(observationTimeToIso('2026-10-13T10:30'), '2026-10-13T02:30:00.000Z')
  const controller = updateController.useV3HoldingsUpdate()
  controller.upload.value = { id: 1, validation_errors: [] }
  controller.parsed.value = { holdings: [holding()], excluded_items: [], notes: [] }
  assert.equal(controller.canConfirm.value, false)
  await controller.confirmHoldings()
  assert.match(controller.error.value, /截图事实时间/)
  controller.observedAtInput.value = '2026-10-13T10:30'
  assert.equal(controller.canConfirm.value, true)
  let submitted
  global.fetch = async (_path, init) => {
    submitted = JSON.parse(init.body).parsed
    return json({ id: 1, parsed: submitted, validation_errors: [] })
  }
  assert.equal(await controller.saveParsed(), true)
  assert.equal(submitted.observed_at, '2026-10-13T02:30:00.000Z')
})
