/**
 * V3 Dashboard controller.
 * - Independent module state (data/loading/error/lastSuccess)
 * - AbortController + request sequence per module
 * - Captured portfolio id race protection
 * - Session-aware polling + visibility session-first resume
 */
import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../../api'
import type {
  DailyDashboard,
  MajorIndexQuote,
  MarketOverview,
  MarketSessionResponse,
  SystemicRiskSnapshot,
} from '../../api/types'
import { usePortfolioContext } from '../../composables/portfolio'
import type { V3DashboardViewModel } from './dashboard-types'
import { buildViewModel, dashboardPollIntervals } from './dashboard-formatters'

export { buildViewModel, dashboardPollIntervals }

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

export function useV3Dashboard() {
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
  const majorIndices = ref<MajorIndexQuote[] | null>(null)
  const systemicRisk = ref<SystemicRiskSnapshot | null>(null)
  const marketOverview = ref<MarketOverview | null>(null)
  const portfolioDashboard = ref<DailyDashboard | null>(null)

  const sessionLoading = ref(false)
  const indicesLoading = ref(false)
  const riskLoading = ref(false)
  const overviewLoading = ref(false)
  const portfolioLoading = ref(false)
  const refreshing = ref(false)
  const portfoliosLoading = ref(false)

  const sessionError = ref<unknown>(null)
  const indicesError = ref<unknown>(null)
  const riskError = ref<unknown>(null)
  const overviewError = ref<unknown>(null)
  const portfolioError = ref<unknown>(null)

  const sessionLastSuccess = ref<string | null>(null)
  const indicesLastSuccess = ref<string | null>(null)
  const riskLastSuccess = ref<string | null>(null)
  const overviewLastSuccess = ref<string | null>(null)
  const portfolioLastSuccess = ref<string | null>(null)

  const slotSession = createSlot()
  const slotIndices = createSlot()
  const slotRisk = createSlot()
  const slotOverview = createSlot()
  const slotPortfolio = createSlot()

  const instrumentDrawerOpen = ref(false)
  const selectedInstrumentCode = ref('')
  const mounted = ref(false)

  let sessionTimer: number | null = null
  let indicesTimer: number | null = null
  let riskTimer: number | null = null
  let overviewTimer: number | null = null
  let portfolioTimer: number | null = null

  const hasPortfolio = computed(() => portfolios.value.length > 0 && Boolean(selectedPortfolioId.value))

  const viewModel = computed<V3DashboardViewModel>(() =>
    buildViewModel({
      session: session.value,
      majorIndices: majorIndices.value,
      systemicRisk: systemicRisk.value,
      overview: marketOverview.value,
      dashboard: portfolioDashboard.value,
      portfolioId: selectedPortfolioId.value,
      portfolioName: selectedPortfolio.value?.name || null,
      hasPortfolio: hasPortfolio.value,
    }),
  )

  function openInstrument(code: string): void {
    if (!code) return
    selectedInstrumentCode.value = code
    instrumentDrawerOpen.value = true
  }

  async function loadSession(silent = false): Promise<void> {
    const ctx = begin(slotSession)
    if (!silent) sessionLoading.value = true
    try {
      const data = await api.getMarketSession(ctx.signal)
      if (!isCurrent(slotSession, ctx.seq)) return
      session.value = data
      sessionError.value = null
      sessionLastSuccess.value = new Date().toISOString()
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotSession, ctx.seq)) return
      sessionError.value = error
    } finally {
      if (isCurrent(slotSession, ctx.seq) && !silent) sessionLoading.value = false
    }
  }

  async function loadMajorIndices(silent = false): Promise<void> {
    const ctx = begin(slotIndices)
    if (!silent) indicesLoading.value = true
    try {
      const data = await api.getMajorIndices(ctx.signal)
      if (!isCurrent(slotIndices, ctx.seq)) return
      majorIndices.value = data
      indicesError.value = null
      indicesLastSuccess.value = new Date().toISOString()
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotIndices, ctx.seq)) return
      indicesError.value = error
    } finally {
      if (isCurrent(slotIndices, ctx.seq) && !silent) indicesLoading.value = false
    }
  }

  async function loadSystemicRisk(silent = false): Promise<void> {
    const ctx = begin(slotRisk)
    if (!silent) riskLoading.value = true
    try {
      const data = await api.getSystemicRisk(ctx.signal)
      if (!isCurrent(slotRisk, ctx.seq)) return
      systemicRisk.value = data
      riskError.value = null
      riskLastSuccess.value = new Date().toISOString()
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotRisk, ctx.seq)) return
      riskError.value = error
    } finally {
      if (isCurrent(slotRisk, ctx.seq) && !silent) riskLoading.value = false
    }
  }

  async function loadMarketOverview(silent = false): Promise<void> {
    const ctx = begin(slotOverview)
    if (!silent) overviewLoading.value = true
    try {
      const data = await api.getMarketOverview(ctx.signal)
      if (!isCurrent(slotOverview, ctx.seq)) return
      marketOverview.value = data
      overviewError.value = null
      overviewLastSuccess.value = new Date().toISOString()
      // Overview is a coherent snapshot — fill gaps without forcing extra calls.
      if (!session.value && data.session) session.value = data.session
      if ((!majorIndices.value || !majorIndices.value.length) && data.major_indices?.length) {
        majorIndices.value = data.major_indices
      }
      if (!systemicRisk.value && data.systemic_risk) systemicRisk.value = data.systemic_risk
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotOverview, ctx.seq)) return
      overviewError.value = error
    } finally {
      if (isCurrent(slotOverview, ctx.seq) && !silent) overviewLoading.value = false
    }
  }

  async function loadPortfolioDashboard(silent = false): Promise<void> {
    const portfolioId = selectedPortfolioId.value
    const ctx = begin(slotPortfolio)
    if (!portfolioId) {
      portfolioDashboard.value = null
      portfolioError.value = null
      if (!silent) portfolioLoading.value = false
      return
    }
    if (!silent) portfolioLoading.value = true
    try {
      const data = await api.getDashboardToday(portfolioId, ctx.signal)
      // Race safety: only apply if still current sequence AND still selected portfolio.
      if (!isCurrent(slotPortfolio, ctx.seq)) return
      if (selectedPortfolioId.value !== portfolioId) return
      portfolioDashboard.value = data
      portfolioError.value = null
      portfolioLastSuccess.value = new Date().toISOString()
    } catch (error) {
      if (isAbortError(error) || !isCurrent(slotPortfolio, ctx.seq)) return
      if (selectedPortfolioId.value !== portfolioId) return
      portfolioError.value = error
    } finally {
      if (isCurrent(slotPortfolio, ctx.seq) && !silent) portfolioLoading.value = false
    }
  }

  function clearPollTimers(): void {
    for (const timer of [sessionTimer, indicesTimer, riskTimer, overviewTimer, portfolioTimer]) {
      if (timer !== null) window.clearTimeout(timer)
    }
    sessionTimer = indicesTimer = riskTimer = overviewTimer = portfolioTimer = null
  }

  function scheduleSessionPoll(): void {
    if (sessionTimer !== null) {
      window.clearTimeout(sessionTimer)
      sessionTimer = null
    }
    if (isPageHidden() || !mounted.value) return
    const ms = dashboardPollIntervals(session.value?.session ?? null).sessionMs
    sessionTimer = window.setTimeout(async () => {
      await loadSession(true)
      scheduleSessionPoll()
      // Reconcile other pollers when session kind changes.
      rescheduleDataPollers()
    }, ms)
  }

  function rescheduleDataPollers(): void {
    if (indicesTimer !== null) window.clearTimeout(indicesTimer)
    if (riskTimer !== null) window.clearTimeout(riskTimer)
    if (overviewTimer !== null) window.clearTimeout(overviewTimer)
    if (portfolioTimer !== null) window.clearTimeout(portfolioTimer)
    indicesTimer = riskTimer = overviewTimer = portfolioTimer = null
    if (isPageHidden() || !mounted.value) return

    const cadence = dashboardPollIntervals(session.value?.session ?? null)
    if (cadence.indicesMs !== null) {
      indicesTimer = window.setTimeout(async () => {
        await loadMajorIndices(true)
        if (!isPageHidden()) rescheduleIndicesOnly()
      }, cadence.indicesMs)
    }
    if (cadence.riskMs !== null) {
      riskTimer = window.setTimeout(async () => {
        await loadSystemicRisk(true)
        if (!isPageHidden()) rescheduleRiskOnly()
      }, cadence.riskMs)
    }
    if (cadence.overviewMs !== null) {
      overviewTimer = window.setTimeout(async () => {
        await loadMarketOverview(true)
        if (!isPageHidden()) rescheduleOverviewOnly()
      }, cadence.overviewMs)
    }
    if (cadence.portfolioMs !== null && selectedPortfolioId.value) {
      portfolioTimer = window.setTimeout(async () => {
        await loadPortfolioDashboard(true)
        if (!isPageHidden()) reschedulePortfolioOnly()
      }, cadence.portfolioMs)
    }
  }

  function rescheduleIndicesOnly(): void {
    if (indicesTimer !== null) window.clearTimeout(indicesTimer)
    indicesTimer = null
    if (isPageHidden() || !mounted.value) return
    const ms = dashboardPollIntervals(session.value?.session ?? null).indicesMs
    if (ms === null) return
    indicesTimer = window.setTimeout(async () => {
      await loadMajorIndices(true)
      if (!isPageHidden()) rescheduleIndicesOnly()
    }, ms)
  }

  function rescheduleRiskOnly(): void {
    if (riskTimer !== null) window.clearTimeout(riskTimer)
    riskTimer = null
    if (isPageHidden() || !mounted.value) return
    const ms = dashboardPollIntervals(session.value?.session ?? null).riskMs
    if (ms === null) return
    riskTimer = window.setTimeout(async () => {
      await loadSystemicRisk(true)
      if (!isPageHidden()) rescheduleRiskOnly()
    }, ms)
  }

  function rescheduleOverviewOnly(): void {
    if (overviewTimer !== null) window.clearTimeout(overviewTimer)
    overviewTimer = null
    if (isPageHidden() || !mounted.value) return
    const ms = dashboardPollIntervals(session.value?.session ?? null).overviewMs
    if (ms === null) return
    overviewTimer = window.setTimeout(async () => {
      await loadMarketOverview(true)
      if (!isPageHidden()) rescheduleOverviewOnly()
    }, ms)
  }

  function reschedulePortfolioOnly(): void {
    if (portfolioTimer !== null) window.clearTimeout(portfolioTimer)
    portfolioTimer = null
    if (isPageHidden() || !mounted.value) return
    const ms = dashboardPollIntervals(session.value?.session ?? null).portfolioMs
    if (ms === null || !selectedPortfolioId.value) return
    portfolioTimer = window.setTimeout(async () => {
      await loadPortfolioDashboard(true)
      if (!isPageHidden()) reschedulePortfolioOnly()
    }, ms)
  }

  async function refreshOnVisibilityResume(): Promise<void> {
    clearPollTimers()
    if (isPageHidden()) return
    // Session-first recovery — never resume from a stale session.
    await loadSession(true)
    if (isPageHidden()) return
    await Promise.all([
      loadMajorIndices(true),
      loadSystemicRisk(true),
    ])
    if (selectedPortfolioId.value) await loadPortfolioDashboard(true)
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
      await Promise.all([
        loadMajorIndices(),
        loadSystemicRisk(),
        loadMarketOverview(),
      ])
      if (selectedPortfolioId.value) await loadPortfolioDashboard()
      if (!isPageHidden()) {
        scheduleSessionPoll()
        rescheduleDataPollers()
      }
    } finally {
      refreshing.value = false
    }
  }

  async function bootstrap(): Promise<void> {
    mounted.value = true
    portfoliosLoading.value = true
    // Portfolio-independent market modules load in parallel first.
    const marketBoot = Promise.all([
      loadSession(),
      loadMajorIndices(),
      loadSystemicRisk(),
      loadMarketOverview(),
    ])
    try {
      await loadPortfolios()
      const requested = Number(route.query.portfolio)
      if (requested && portfolios.value.some((item) => item.id === requested)) {
        setSelectedPortfolio(requested)
      }
    } catch {
      // Portfolio list failure must not block market modules.
    } finally {
      portfoliosLoading.value = false
    }
    await marketBoot
    if (selectedPortfolioId.value) await loadPortfolioDashboard()
    if (!isPageHidden()) {
      scheduleSessionPoll()
      rescheduleDataPollers()
    }
  }

  watch(selectedPortfolioId, (id, previous) => {
    if (id === previous) return
    // Market modules are portfolio-independent — do not reload them.
    void loadPortfolioDashboard(!mounted.value)
    if (!isPageHidden() && mounted.value) reschedulePortfolioOnly()
  })

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibilityChange)
    void bootstrap()
  })

  onBeforeUnmount(() => {
    mounted.value = false
    document.removeEventListener('visibilitychange', onVisibilityChange)
    clearPollTimers()
    for (const slot of [slotSession, slotIndices, slotRisk, slotOverview, slotPortfolio]) {
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
    sessionLoading,
    indicesLoading,
    riskLoading,
    overviewLoading,
    portfolioLoading,
    sessionError,
    indicesError,
    riskError,
    overviewError,
    portfolioError,
    sessionLastSuccess,
    indicesLastSuccess,
    riskLastSuccess,
    overviewLastSuccess,
    portfolioLastSuccess,
    session,
    majorIndices,
    systemicRisk,
    marketOverview,
    portfolioDashboard,
    viewModel,
    instrumentDrawerOpen,
    selectedInstrumentCode,
    openInstrument,
    refreshAll,
    loadPortfolioDashboard,
    router,
  }
}
