/**
 * V3 Holdings controller.
 * - Position / Quote / Strategy independent modules
 * - Ownership + generation race safety
 * - Session-aware independent pollers
 * - Batch quotes only
 * - Lazy bounded SVG sparkline cache
 */
import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../../api'
import type {
  DailyDashboard,
  MarketSessionResponse,
  PortfolioSnapshot,
} from '../../api/types'
import { usePortfolioContext } from '../../composables/portfolio'
import type {
  V3HoldingFilter,
  V3HoldingSort,
  V3HoldingsViewModel,
  V3QuoteVM,
} from './holdings-types'
import {
  buildHoldingsViewModel,
  holdingsPollIntervals,
  mapQuoteItem,
} from './holdings-formatters'

export { buildHoldingsViewModel, holdingsPollIntervals, mapQuoteItem }

export function isAbortError(error: unknown): boolean {
  if (!error) return false
  if (error instanceof DOMException && error.name === 'AbortError') return true
  if (error instanceof ApiError) {
    const cause = error.cause as { name?: string } | undefined
    if (cause && (cause.name === 'AbortError' || (cause as Error).name === 'AbortError')) return true
  }
  return false
}

interface RequestSlot {
  controller: AbortController | null
  seq: number
}

function createSlot(): RequestSlot {
  return { controller: null, seq: 0 }
}

function begin(slot: RequestSlot): { seq: number; signal: AbortSignal } {
  slot.controller?.abort()
  const controller = new AbortController()
  slot.controller = controller
  slot.seq += 1
  return { seq: slot.seq, signal: controller.signal }
}

function isCurrent(slot: RequestSlot, seq: number): boolean {
  return slot.seq === seq
}

function isPageHidden(): boolean {
  return typeof document !== 'undefined' && document.hidden
}

export interface SparklinePoint {
  time: string
  close: number
}

export function useV3Holdings() {
  const route = useRoute()
  const router = useRouter()
  const {
    portfolios,
    selectedPortfolioId,
    selectedPortfolio,
    loadPortfolios,
    setSelectedPortfolio,
  } = usePortfolioContext()

  const session = ref<MarketSessionResponse | null>(null)
  const snapshot = ref<PortfolioSnapshot | null>(null)
  const snapshotOwnerId = ref<number | null>(null)
  const snapshotError = ref<unknown>(null)
  const snapshotErrorOwnerId = ref<number | null>(null)
  const snapshotLoading = ref(false)
  const snapshotLastSuccess = ref<string | null>(null)

  const dashboard = ref<DailyDashboard | null>(null)
  const dashboardOwnerId = ref<number | null>(null)
  const dashboardError = ref<unknown>(null)
  const dashboardErrorOwnerId = ref<number | null>(null)
  const dashboardLoading = ref(false)
  const dashboardLastSuccess = ref<string | null>(null)

  const quotes = ref<Map<string, V3QuoteVM>>(new Map())
  const quotesOwnerId = ref<number | null>(null)
  const quotesAsOf = ref<string | null>(null)
  const quotesError = ref<unknown>(null)
  const quotesErrorOwnerId = ref<number | null>(null)
  const quotesLoading = ref(false)
  const quotesLastSuccess = ref<string | null>(null)
  let quoteGeneration = 0

  const sparklines = ref<Map<string, SparklinePoint[] | null>>(new Map())
  const sparklineInFlight = new Set<string>()
  const sparklineGeneration = ref(0)

  const filter = ref<V3HoldingFilter>((route.query.filter as V3HoldingFilter) || 'all')
  const sort = ref<V3HoldingSort>((route.query.sort as V3HoldingSort) || 'default')

  const sessionLoading = ref(false)
  const sessionError = ref<unknown>(null)
  const refreshing = ref(false)
  const portfoliosLoading = ref(false)
  const mounted = ref(false)

  const instrumentDrawerOpen = ref(false)
  const selectedInstrumentCode = ref('')
  const updateDrawerOpen = ref(false)

  const slotSession = createSlot()
  const slotSnapshot = createSlot()
  const slotDashboard = createSlot()
  const slotQuotes = createSlot()

  let sessionTimer: number | null = null
  let quotesTimer: number | null = null
  let strategyTimer: number | null = null

  const hasPortfolio = computed(() => portfolios.value.length > 0 && Boolean(selectedPortfolioId.value))

  const quoteCoverage = computed<number | null>(() => {
    const snap = snapshotOwnerId.value === selectedPortfolioId.value ? snapshot.value : null
    if (!snap) return null
    const holdings = snap.holdings || []
    const resolved = holdings.filter((item) => item.resolution_status === 'RESOLVED' && (item.canonical_code || item.code))
    if (!resolved.length) return null
    const map = quotesOwnerId.value === selectedPortfolioId.value ? quotes.value : new Map<string, V3QuoteVM>()
    const covered = resolved.filter((item) => {
      const key = item.canonical_code || item.code || ''
      const quote = map.get(key) || map.get(String(key).split('.')[0])
      return Boolean(quote && quote.last !== null && quote.status !== 'MISSING')
    })
    return covered.length / resolved.length
  })

  const currentSnapshot = computed<PortfolioSnapshot | null>(() => {
    if (snapshotOwnerId.value === null) return null
    if (snapshotOwnerId.value !== selectedPortfolioId.value) return null
    return snapshot.value
  })

  const currentDashboard = computed<DailyDashboard | null>(() => {
    if (dashboardOwnerId.value === null) return null
    if (dashboardOwnerId.value !== selectedPortfolioId.value) return null
    return dashboard.value
  })

  const currentQuotes = computed<Map<string, V3QuoteVM>>(() => {
    if (quotesOwnerId.value !== selectedPortfolioId.value) return new Map()
    return quotes.value
  })

  const currentSnapshotError = computed<unknown>(() => {
    if (snapshotErrorOwnerId.value !== selectedPortfolioId.value) return null
    return snapshotError.value
  })

  const currentDashboardError = computed<unknown>(() => {
    if (dashboardErrorOwnerId.value !== selectedPortfolioId.value) return null
    return dashboardError.value
  })

  const viewModel = computed<V3HoldingsViewModel>(() =>
    buildHoldingsViewModel({
      hasPortfolio: hasPortfolio.value,
      portfolioId: selectedPortfolioId.value,
      portfolioName: selectedPortfolio.value?.name || null,
      snapshot: currentSnapshot.value,
      dashboard: currentDashboard.value,
      quotes: currentQuotes.value,
      quotesAsOf: quotesOwnerId.value === selectedPortfolioId.value ? quotesAsOf.value : null,
      quoteCoverage: quoteCoverage.value,
      session: (session.value?.session ?? null) as V3HoldingsViewModel['timestamps']['session'],
      sessionLabel: session.value?.display_label || session.value?.session || '状态未知',
      filter: filter.value,
      sort: sort.value,
    }),
  )

  function openInstrument(row: { canOpenDetail: boolean; canonicalCode: string | null; code: string }): void {
    if (!row.canOpenDetail) return
    const code = row.canonicalCode || row.code
    if (!code) return
    selectedInstrumentCode.value = code
    instrumentDrawerOpen.value = true
  }

  function setFilter(next: V3HoldingFilter): void {
    filter.value = next
    void router.replace({
      name: 'holdings',
      query: { ...route.query, filter: next === 'all' ? undefined : next },
    })
  }

  function setSort(next: V3HoldingSort): void {
    sort.value = next
    void router.replace({
      name: 'holdings',
      query: { ...route.query, sort: next === 'default' ? undefined : next },
    })
  }

  async function loadSession(silent = false): Promise<void> {
    const ctx = begin(slotSession)
    if (!silent) sessionLoading.value = true
    try {
      const data = await api.getMarketSession(ctx.signal)
      if (!isCurrent(slotSession, ctx.seq)) return
      session.value = data
      sessionError.value = null
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotSession, ctx.seq)) return
      sessionError.value = error
    } finally {
      if (isCurrent(slotSession, ctx.seq) && !silent) sessionLoading.value = false
    }
  }

  async function loadSnapshot(silent = false): Promise<void> {
    const portfolioId = selectedPortfolioId.value
    const ctx = begin(slotSnapshot)
    if (!portfolioId) {
      snapshot.value = null
      snapshotOwnerId.value = null
      if (!silent) snapshotLoading.value = false
      return
    }
    const portfolio = portfolios.value.find((item) => item.id === portfolioId)
    if (!portfolio?.latest_snapshot_id) {
      if (!isCurrent(slotSnapshot, ctx.seq)) return
      if (selectedPortfolioId.value !== portfolioId) return
      snapshot.value = null
      snapshotOwnerId.value = portfolioId
      snapshotError.value = null
      snapshotErrorOwnerId.value = null
      if (!silent) snapshotLoading.value = false
      return
    }
    if (!silent) snapshotLoading.value = true
    try {
      const data = await api.getSnapshot(portfolio.latest_snapshot_id, ctx.signal)
      if (!isCurrent(slotSnapshot, ctx.seq)) return
      if (selectedPortfolioId.value !== portfolioId) return
      snapshot.value = data
      snapshotOwnerId.value = portfolioId
      snapshotError.value = null
      snapshotErrorOwnerId.value = null
      snapshotLastSuccess.value = new Date().toISOString()
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotSnapshot, ctx.seq)) return
      if (selectedPortfolioId.value !== portfolioId) return
      snapshotError.value = error
      snapshotErrorOwnerId.value = portfolioId
    } finally {
      if (isCurrent(slotSnapshot, ctx.seq) && !silent) snapshotLoading.value = false
    }
  }

  async function loadDashboard(silent = false): Promise<void> {
    const portfolioId = selectedPortfolioId.value
    const ctx = begin(slotDashboard)
    if (!portfolioId) {
      dashboard.value = null
      dashboardOwnerId.value = null
      if (!silent) dashboardLoading.value = false
      return
    }
    if (!silent) dashboardLoading.value = true
    try {
      const data = await api.getDashboardToday(portfolioId, ctx.signal)
      if (!isCurrent(slotDashboard, ctx.seq)) return
      if (selectedPortfolioId.value !== portfolioId) return
      dashboard.value = data
      dashboardOwnerId.value = portfolioId
      dashboardError.value = null
      dashboardErrorOwnerId.value = null
      dashboardLastSuccess.value = new Date().toISOString()
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotDashboard, ctx.seq)) return
      if (selectedPortfolioId.value !== portfolioId) return
      dashboardError.value = error
      dashboardErrorOwnerId.value = portfolioId
    } finally {
      if (isCurrent(slotDashboard, ctx.seq) && !silent) dashboardLoading.value = false
    }
  }

  function resolvedQuoteCodes(): string[] {
    const snap = currentSnapshot.value
    if (!snap) return []
    const holdings = snap.holdings || []
    const codes: string[] = []
    const seen = new Set<string>()
    for (const item of holdings) {
      if (item.resolution_status !== 'RESOLVED') continue
      const code = (item.canonical_code || item.code || '').trim()
      if (!code) continue
      if (seen.has(code)) continue
      seen.add(code)
      codes.push(code)
    }
    return codes
  }

  async function loadQuotes(silent = false): Promise<void> {
    const portfolioId = selectedPortfolioId.value
    const ctx = begin(slotQuotes)
    const generation = ++quoteGeneration
    if (!portfolioId) {
      quotes.value = new Map()
      quotesOwnerId.value = null
      quotesAsOf.value = null
      if (!silent) quotesLoading.value = false
      return
    }
    const codes = resolvedQuoteCodes()
    if (!codes.length) {
      if (!isCurrent(slotQuotes, ctx.seq)) return
      quotes.value = new Map()
      quotesOwnerId.value = portfolioId
      quotesAsOf.value = null
      quotesError.value = null
      quotesErrorOwnerId.value = null
      if (!silent) quotesLoading.value = false
      return
    }
    if (!silent) quotesLoading.value = true
    try {
      const response = await api.getInstrumentQuotes(codes, ctx.signal)
      if (!isCurrent(slotQuotes, ctx.seq)) return
      if (generation !== quoteGeneration) return
      if (selectedPortfolioId.value !== portfolioId) return
      const map = new Map<string, V3QuoteVM>()
      for (const item of response.items || []) {
        const vm = mapQuoteItem(item as Parameters<typeof mapQuoteItem>[0])
        if (vm) map.set(vm.code, vm)
      }
      quotes.value = map
      quotesOwnerId.value = portfolioId
      quotesAsOf.value = response.as_of || new Date().toISOString()
      quotesError.value = null
      quotesErrorOwnerId.value = null
      quotesLastSuccess.value = new Date().toISOString()
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotQuotes, ctx.seq)) return
      if (selectedPortfolioId.value !== portfolioId) return
      quotesError.value = error
      quotesErrorOwnerId.value = portfolioId
    } finally {
      if (isCurrent(slotQuotes, ctx.seq) && !silent) quotesLoading.value = false
    }
  }

  /** Lazy bounded SVG sparkline — one-shot, concurrency ≤ 4, never poll. */
  async function loadSparklines(codes: string[]): Promise<void> {
    const generation = sparklineGeneration.value
    const pending = codes.filter((code) => code
      && !sparklines.value.has(code)
      && !sparklineInFlight.has(code))
    if (!pending.length) return
    const queue = [...pending]
    const workers = Array.from({ length: Math.min(4, queue.length) }, async () => {
      while (queue.length) {
        if (generation !== sparklineGeneration.value) return
        const code = queue.shift()
        if (!code) break
        sparklineInFlight.add(code)
        try {
          const response = await api.getInstrumentBars(code, { interval: '1d', limit: 30 })
          if (generation !== sparklineGeneration.value) return
          const points: SparklinePoint[] = (response.bars || [])
            .map((bar) => ({ time: bar.time, close: bar.close }))
            .filter((item) => Number.isFinite(item.close))
          sparklines.value = new Map(sparklines.value).set(code, points.length ? points : null)
        } catch {
          if (generation !== sparklineGeneration.value) return
          sparklines.value = new Map(sparklines.value).set(code, null)
        } finally {
          sparklineInFlight.delete(code)
        }
      }
    })
    await Promise.all(workers)
  }

  function requestVisibleSparklines(codes: string[]): void {
    void loadSparklines(codes.slice(0, 20))
  }

  function clearPollTimers(): void {
    for (const timer of [sessionTimer, quotesTimer, strategyTimer]) {
      if (timer !== null) window.clearTimeout(timer)
    }
    sessionTimer = quotesTimer = strategyTimer = null
  }

  function scheduleSessionPoll(): void {
    if (sessionTimer !== null) {
      window.clearTimeout(sessionTimer)
      sessionTimer = null
    }
    if (isPageHidden() || !mounted.value) return
    const ms = holdingsPollIntervals(session.value?.session ?? null).sessionMs
    sessionTimer = window.setTimeout(async () => {
      const previousKind = session.value?.session ?? null
      await loadSession(true)
      if (isPageHidden() || !mounted.value) return
      const nextKind = session.value?.session ?? null
      scheduleSessionPoll()
      if (previousKind !== nextKind) rescheduleDataPollers()
    }, ms)
  }

  function rescheduleQuotesOnly(): void {
    if (quotesTimer !== null) window.clearTimeout(quotesTimer)
    quotesTimer = null
    if (isPageHidden() || !mounted.value) return
    const ms = holdingsPollIntervals(session.value?.session ?? null).quotesMs
    if (ms === null) return
    quotesTimer = window.setTimeout(async () => {
      await loadQuotes(true)
      if (!isPageHidden()) rescheduleQuotesOnly()
    }, ms)
  }

  function rescheduleStrategyOnly(): void {
    if (strategyTimer !== null) window.clearTimeout(strategyTimer)
    strategyTimer = null
    if (isPageHidden() || !mounted.value) return
    const cadence = holdingsPollIntervals(session.value?.session ?? null)
    const analysisInProgress = Boolean(currentDashboard.value?.analysis?.jobs?.some(
      (job: { status?: string }) => String(job.status || '').toLowerCase() === 'running',
    ))
    const ms = analysisInProgress ? 12_000 : cadence.strategyMs
    if (ms === null) return
    strategyTimer = window.setTimeout(async () => {
      await loadDashboard(true)
      if (!isPageHidden()) rescheduleStrategyOnly()
    }, ms)
  }

  function rescheduleDataPollers(): void {
    if (quotesTimer !== null) window.clearTimeout(quotesTimer)
    if (strategyTimer !== null) window.clearTimeout(strategyTimer)
    quotesTimer = strategyTimer = null
    if (isPageHidden() || !mounted.value) return
    rescheduleQuotesOnly()
    rescheduleStrategyOnly()
  }

  async function refreshOnVisibilityResume(): Promise<void> {
    clearPollTimers()
    if (isPageHidden()) return
    await loadSession(true)
    if (isPageHidden()) return
    await Promise.all([
      loadQuotes(true),
      loadDashboard(true),
    ])
    scheduleSessionPoll()
    rescheduleDataPollers()
  }

  function onVisibilityChange(): void {
    if (isPageHidden()) clearPollTimers()
    else void refreshOnVisibilityResume()
  }

  async function refreshAll(): Promise<void> {
    refreshing.value = true
    try {
      await loadSession()
      await loadPortfolios(true)
      await Promise.all([
        loadSnapshot(),
        loadDashboard(),
      ])
      await loadQuotes()
      if (!isPageHidden()) {
        scheduleSessionPoll()
        rescheduleDataPollers()
      }
    } finally {
      refreshing.value = false
    }
  }

  async function reloadAfterConfirm(snapshotId: number): Promise<void> {
    // New confirmed snapshot: drop stale quote generation and rebind strategy.
    quoteGeneration += 1
    sparklineGeneration.value += 1
    quotes.value = new Map()
    quotesAsOf.value = null
    await loadPortfolios(true)
    await Promise.all([
      loadSnapshot(true),
      loadDashboard(true),
    ])
    await loadQuotes(true)
    if (!isPageHidden()) rescheduleDataPollers()
    void snapshotId
  }

  function openUpdate(): void {
    updateDrawerOpen.value = true
    void router.replace({
      name: 'holdings',
      query: { ...route.query, action: 'update', portfolio: selectedPortfolioId.value || undefined },
    })
  }

  function closeUpdate(): void {
    updateDrawerOpen.value = false
    const query = { ...route.query }
    delete query.action
    void router.replace({ name: 'holdings', query })
  }

  async function bootstrap(): Promise<void> {
    mounted.value = true
    portfoliosLoading.value = true
    const marketBoot = loadSession()
    try {
      await loadPortfolios()
      const requested = Number(route.query.portfolio)
      if (requested && portfolios.value.some((item) => item.id === requested)) {
        setSelectedPortfolio(requested)
      }
    } catch {
      // Portfolio list failure must not block session.
    } finally {
      portfoliosLoading.value = false
    }
    await marketBoot
    if (selectedPortfolioId.value) {
      await Promise.all([
        loadSnapshot(),
        loadDashboard(),
      ])
      // Quotes depend on confirmed snapshot holdings — single batch after snapshot resolves.
      await loadQuotes()
    }
    if (route.query.action === 'update') updateDrawerOpen.value = true
    if (!isPageHidden()) {
      scheduleSessionPoll()
      rescheduleDataPollers()
    }
  }

  watch(selectedPortfolioId, (id, previous) => {
    if (id === previous) return
    // Drop prior portfolio errors so A never surfaces as B.
    snapshotError.value = null
    snapshotErrorOwnerId.value = null
    dashboardError.value = null
    dashboardErrorOwnerId.value = null
    quotesError.value = null
    quotesErrorOwnerId.value = null
    quoteGeneration += 1
    sparklineGeneration.value += 1
    // Immediately hide A payload while B loads (ownership gate is on computed).
    void (async () => {
      await Promise.all([
        loadSnapshot(!mounted.value),
        loadDashboard(!mounted.value),
      ])
      await loadQuotes(!mounted.value)
    })()
    if (!isPageHidden() && mounted.value) rescheduleDataPollers()
  })

  watch(() => route.query.action, (value) => {
    updateDrawerOpen.value = value === 'update'
  })

  watch(() => currentSnapshot.value?.id, (id, previous) => {
    if (!id || id === previous) return
    sparklineGeneration.value += 1
    sparklines.value = new Map()
    sparklineInFlight.clear()
  })

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibilityChange)
    void bootstrap()
  })

  onBeforeUnmount(() => {
    mounted.value = false
    document.removeEventListener('visibilitychange', onVisibilityChange)
    clearPollTimers()
    quoteGeneration += 1
    sparklineGeneration.value += 1
    for (const slot of [slotSession, slotSnapshot, slotDashboard, slotQuotes]) {
      slot.controller?.abort()
      slot.controller = null
      slot.seq += 1
    }
  })

  return {
    portfolios,
    selectedPortfolioId,
    selectedPortfolio,
    setSelectedPortfolio,
    hasPortfolio,
    portfoliosLoading,
    refreshing,
    session,
    sessionLoading,
    sessionError,
    snapshotLoading,
    currentSnapshotError,
    dashboardLoading,
    currentDashboardError,
    quotesLoading,
    quotesError,
    snapshotLastSuccess,
    dashboardLastSuccess,
    quotesLastSuccess,
    viewModel,
    filter,
    sort,
    setFilter,
    setSort,
    instrumentDrawerOpen,
    selectedInstrumentCode,
    openInstrument,
    updateDrawerOpen,
    openUpdate,
    closeUpdate,
    refreshAll,
    loadDashboard,
    loadQuotes,
    loadSnapshot,
    reloadAfterConfirm,
    sparklines,
    requestVisibleSparklines,
    router,
  }
}
