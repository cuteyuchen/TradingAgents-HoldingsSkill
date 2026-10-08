/**
 * Shared InstrumentDetail data controller.
 * Used by both full page and drawer — no duplicated request logic.
 *
 * Lifecycle rules:
 * - Each module owns an independent AbortController + sequence (RequestSlot).
 * - Pollers are independent: quote/book/flow/bars/session only reschedule themselves.
 * - code change / unmount / drawer close abort all and clear all timers.
 * - Bars param change aborts only the bars slot.
 * - Secondary loaders use resolved canonical identity, never raw route param equality.
 */
import { computed, onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import { ApiError, api } from '@/api'
import type {
  BarAdjustment,
  BarInterval,
  CapitalFlow,
  InstrumentBarsResponse,
  InstrumentMetadataResponse,
  InstrumentQuote,
  MarketSessionResponse,
  OrderBook,
} from '@/api/types'

export type InstrumentTab = 'quote' | 'book' | 'flow' | 'analysis' | 'news' | 'history'

export interface ModuleError {
  title: string
  description: string
  canRetry: boolean
}

export function isAbortError(error: unknown): boolean {
  if (!error) return false
  if (error instanceof DOMException && error.name === 'AbortError') return true
  if (error instanceof ApiError) {
    const cause = error.cause
    if (cause instanceof DOMException && cause.name === 'AbortError') return true
    if (cause instanceof Error && cause.name === 'AbortError') return true
  }
  return false
}

function mapApiError(error: unknown): ModuleError {
  if (error instanceof ApiError) {
    if (error.status === 404) {
      return { title: '标的不存在或主数据未收录', description: '请确认证券代码是否正确，或联系管理员补充 SecurityMaster。', canRetry: false }
    }
    if (error.status === 429) {
      return { title: '行情请求过于频繁', description: '请稍后重试。', canRetry: true }
    }
    if (error.status !== null && error.status >= 500) {
      return { title: '行情服务暂不可用', description: '服务端异常，请稍后重试。', canRetry: true }
    }
    if (error.kind === 'network' || error.kind === 'timeout') {
      return { title: '网络异常', description: '无法连接行情服务，请检查网络后重试。', canRetry: true }
    }
    return { title: '数据加载失败', description: error.message || '请稍后重试。', canRetry: true }
  }
  return { title: '数据加载失败', description: '请稍后重试。', canRetry: true }
}

export function mapInstrumentError(error: unknown): ModuleError {
  return mapApiError(error)
}

function defaultAdjustment(caps: InstrumentMetadataResponse['capabilities'] | null): BarAdjustment | undefined {
  if (!caps) return undefined
  const order: BarAdjustment[] = ['forward', 'none', 'backward']
  for (const a of order) {
    if (caps.adjustments.includes(a)) return a
  }
  return caps.adjustments[0]
}

/** Exported for deterministic unit/acceptance timing tests. */
export function pollingIntervalMs(session: MarketSessionResponse | null, module: 'quote' | 'book' | 'flow' | 'bars'): number | null {
  const kind = session?.session
  if (kind === 'CLOSED' || kind === 'NON_TRADING_DAY' || kind === 'DATA_ABNORMAL') {
    return null
  }
  const trading = kind === 'MORNING' || kind === 'AFTERNOON' || kind === 'OPEN_AUCTION' || kind === 'CLOSE_AUCTION'
  switch (module) {
    case 'quote':
      if (trading || kind === 'PRE_OPEN') return 5000
      if (kind === 'LUNCH_BREAK') return 30_000
      return null
    case 'book':
      return trading ? 4000 : null
    case 'flow':
      return trading ? 50_000 : null
    case 'bars':
      return trading ? 30_000 : null
    default:
      return null
  }
}

/**
 * Session heartbeat cadence. NEVER returns null — only unmount/hidden may stop it.
 * MarketSessionService remains the only session authority (no browser-clock guessing).
 */
export function sessionPollIntervalMs(session: MarketSessionResponse | null): number {
  const kind = session?.session
  if (!kind) {
    // Initial failure / unknown: fast self-healing retry.
    return 10_000
  }
  switch (kind) {
    case 'DATA_ABNORMAL':
      return 20_000
    case 'PRE_OPEN':
      return 20_000
    case 'OPEN_AUCTION':
      return 12_000
    case 'MORNING':
    case 'LUNCH_BREAK':
    case 'AFTERNOON':
      return 60_000
    case 'CLOSE_AUCTION':
      return 12_000
    case 'CLOSED':
      return 120_000
    case 'NON_TRADING_DAY':
      return 300_000
    default:
      return 30_000
  }
}

type RequestSlotName = 'identity' | 'quote' | 'bars' | 'book' | 'flow' | 'session'

interface RequestSlot {
  controller: AbortController | null
  seq: number
}

export interface UseInstrumentDetailOptions {
  code: Ref<string>
  initialTab?: InstrumentTab
}

export function useInstrumentDetail(options: UseInstrumentDetailOptions) {
  const code = options.code
  const activeTab = ref<InstrumentTab>(options.initialTab ?? 'quote')
  const interval = ref<BarInterval>('1d')
  const adjustment = ref<BarAdjustment | undefined>(undefined)
  const barLimit = ref(250)

  const metadata = shallowRef<InstrumentMetadataResponse | null>(null)
  const quote = shallowRef<InstrumentQuote | null>(null)
  const bars = shallowRef<InstrumentBarsResponse | null>(null)
  const orderBook = shallowRef<OrderBook | null>(null)
  const capitalFlow = shallowRef<CapitalFlow | null>(null)
  const session = shallowRef<MarketSessionResponse | null>(null)

  const pageLoading = ref(false)
  const pageError = ref<ModuleError | null>(null)
  const pageNotFound = ref(false)

  const quoteLoading = ref(false)
  const barsLoading = ref(false)
  const bookLoading = ref(false)
  const flowLoading = ref(false)
  const sessionLoading = ref(false)

  const quoteError = ref<ModuleError | null>(null)
  const barsError = ref<ModuleError | null>(null)
  const bookError = ref<ModuleError | null>(null)
  const flowError = ref<ModuleError | null>(null)

  const bookLoadedFor = ref<string | null>(null)
  const flowLoadedFor = ref<string | null>(null)

  /** Incremented on every code change / initialLoad. Secondary responses must match. */
  let requestGeneration = 0
  /** Raw route/user input code for the current generation. */
  let requestedInputCode = ''
  /** Canonical identity.code after successful metadata (may alias 600519 -> 600519.SH). */
  let resolvedCanonicalCode = ''

  const slots: Record<RequestSlotName, RequestSlot> = {
    identity: { controller: null, seq: 0 },
    quote: { controller: null, seq: 0 },
    bars: { controller: null, seq: 0 },
    book: { controller: null, seq: 0 },
    flow: { controller: null, seq: 0 },
    session: { controller: null, seq: 0 },
  }

  const pollTimers: Record<'quote' | 'book' | 'flow' | 'bars' | 'session', number | null> = {
    quote: null,
    book: null,
    flow: null,
    bars: null,
    session: null,
  }

  let visibilityHandler: (() => void) | null = null

  const identity = computed(() => metadata.value?.identity ?? null)
  const capabilities = computed(() => metadata.value?.capabilities ?? null)
  const instrumentMeta = computed(() => metadata.value?.metadata ?? null)
  const canonicalCode = computed(() => identity.value?.code || code.value)

  const supportsBook = computed(() => capabilities.value?.order_book === true)
  const supportsFlow = computed(() => capabilities.value?.capital_flow === true)
  const supportsBars = computed(() => capabilities.value?.bars !== false)
  const supportsQuote = computed(() => capabilities.value?.quote !== false)

  function beginModuleRequest(name: RequestSlotName): { signal: AbortSignal; seq: number; generation: number; inputCode: string; canonical: string } {
    abortModule(name)
    const slot = slots[name]
    const controller = new AbortController()
    slot.controller = controller
    slot.seq += 1
    return {
      signal: controller.signal,
      seq: slot.seq,
      generation: requestGeneration,
      inputCode: requestedInputCode,
      canonical: resolvedCanonicalCode || code.value,
    }
  }

  function abortModule(name: RequestSlotName): void {
    const slot = slots[name]
    slot.controller?.abort()
    slot.controller = null
  }

  function abortAllModules(): void {
    (Object.keys(slots) as RequestSlotName[]).forEach(abortModule)
  }

  function isCurrentModuleRequest(name: RequestSlotName, ctx: { seq: number; generation: number; signal: AbortSignal }): boolean {
    if (ctx.signal.aborted) return false
    if (ctx.generation !== requestGeneration) return false
    if (ctx.seq !== slots[name].seq) return false
    return true
  }

  /** Secondary response may apply only if identity generation and canonical still match. */
  function isCurrentSecondary(ctx: { seq: number; generation: number; signal: AbortSignal; name: RequestSlotName; canonical: string }): boolean {
    if (!isCurrentModuleRequest(ctx.name, ctx)) return false
    if (!resolvedCanonicalCode || ctx.canonical !== resolvedCanonicalCode) return false
    return true
  }

  function clearPollTimer(name: 'quote' | 'book' | 'flow' | 'bars' | 'session'): void {
    const t = pollTimers[name]
    if (t !== null) window.clearTimeout(t)
    pollTimers[name] = null
  }

  function clearAllPollTimers(): void {
    (Object.keys(pollTimers) as (keyof typeof pollTimers)[]).forEach(clearPollTimer)
  }

  function isPageHidden(): boolean {
    return typeof document !== 'undefined' && document.visibilityState === 'hidden'
  }

  function scheduleQuotePoll(): void {
    clearPollTimer('quote')
    if (isPageHidden()) return
    const ms = pollingIntervalMs(session.value, 'quote')
    if (ms === null) return
    pollTimers.quote = window.setTimeout(async () => {
      await loadQuote()
      // Only reschedule quote — never touch other module timers.
      scheduleQuotePoll()
    }, ms)
  }

  function scheduleBookPoll(): void {
    clearPollTimer('book')
    if (isPageHidden()) return
    if (activeTab.value !== 'book' || !supportsBook.value) return
    const ms = pollingIntervalMs(session.value, 'book')
    if (ms === null) return
    pollTimers.book = window.setTimeout(async () => {
      await loadOrderBook(true)
      scheduleBookPoll()
    }, ms)
  }

  function scheduleFlowPoll(): void {
    clearPollTimer('flow')
    if (isPageHidden()) return
    if (activeTab.value !== 'flow' || !supportsFlow.value) return
    const ms = pollingIntervalMs(session.value, 'flow')
    if (ms === null) return
    pollTimers.flow = window.setTimeout(async () => {
      await loadCapitalFlow(true)
      scheduleFlowPoll()
    }, ms)
  }

  function scheduleBarsPoll(): void {
    clearPollTimer('bars')
    if (isPageHidden()) return
    if (!supportsBars.value) return
    const ms = pollingIntervalMs(session.value, 'bars')
    if (ms === null) return
    pollTimers.bars = window.setTimeout(async () => {
      await loadBars()
      scheduleBarsPoll()
    }, ms)
  }

  function scheduleSessionPoll(): void {
    clearPollTimer('session')
    if (isPageHidden()) return
    // Always schedule: any session kind (and null) must keep a heartbeat path.
    const ms = sessionPollIntervalMs(session.value)
    pollTimers.session = window.setTimeout(() => {
      void runSessionHeartbeat()
    }, ms)
  }

  /**
   * Session tick: refresh only the session slot.
   * Reschedule all module pollers only when the authoritative kind changes.
   * Never aborts quote/bars/book/flow.
   */
  async function runSessionHeartbeat(): Promise<void> {
    if (isPageHidden()) return
    const oldKind = session.value?.session ?? null
    await loadSession()
    if (isPageHidden()) return
    const newKind = session.value?.session ?? null
    if (oldKind !== newKind) {
      rescheduleAllPollers()
    } else {
      scheduleSessionPoll()
    }
  }

  /** Full reschedule: initial load, code change, visibility resume, session state change. */
  function rescheduleAllPollers(): void {
    clearAllPollTimers()
    if (isPageHidden()) return
    scheduleQuotePoll()
    scheduleBookPoll()
    scheduleFlowPoll()
    scheduleBarsPoll()
    scheduleSessionPoll()
  }

  /**
   * Visibility resume: load authoritative session first, then refresh live modules,
   * then reschedule all pollers from the fresh session. No browser-clock session guessing.
   */
  async function refreshOnVisibilityResume(): Promise<void> {
    clearAllPollTimers()
    await loadSession()
    if (isPageHidden()) return
    if (!resolvedCanonicalCode) return
    await Promise.all([loadQuote(), loadBars()])
    if (activeTab.value === 'book' && supportsBook.value) await loadOrderBook(true)
    if (activeTab.value === 'flow' && supportsFlow.value) await loadCapitalFlow(true)
    rescheduleAllPollers()
  }

  function onVisibilityChange(): void {
    if (isPageHidden()) {
      clearAllPollTimers()
    } else {
      void refreshOnVisibilityResume()
    }
  }

  async function loadSession(externalSignal?: AbortSignal): Promise<void> {
    const ctx = beginModuleRequest('session')
    sessionLoading.value = true
    try {
      const data = await api.getMarketSession(ctx.signal)
      if (!isCurrentModuleRequest('session', ctx)) return
      session.value = data
    } catch {
      // Session is advisory for polling cadence; do not fail the page.
    } finally {
      if (isCurrentModuleRequest('session', ctx)) sessionLoading.value = false
    }
    void externalSignal
  }

  async function loadMetadata(force = false): Promise<boolean> {
    const ctx = beginModuleRequest('identity')
    const reqInput = ctx.inputCode
    pageLoading.value = !metadata.value || force
    pageError.value = null
    pageNotFound.value = false
    try {
      const data = await api.getInstrument(reqInput, ctx.signal)
      if (!isCurrentModuleRequest('identity', ctx)) return false
      metadata.value = data
      resolvedCanonicalCode = data.identity.code
      pageLoading.value = false
      const preferred = defaultAdjustment(data.capabilities)
      if (preferred && !data.capabilities.adjustments.includes(adjustment.value as BarAdjustment)) {
        adjustment.value = preferred
      } else if (!adjustment.value) {
        adjustment.value = preferred
      }
      return true
    } catch (error) {
      if (isAbortError(error) || !isCurrentModuleRequest('identity', ctx)) return false
      pageLoading.value = false
      if (error instanceof ApiError && error.status === 404) {
        pageNotFound.value = true
        pageError.value = mapApiError(error)
        return false
      }
      pageError.value = mapApiError(error)
      return false
    }
  }

  async function loadQuote(): Promise<void> {
    if (!supportsQuote.value) return
    const reqCanonical = resolvedCanonicalCode || canonicalCode.value
    if (!reqCanonical) return
    const ctx = beginModuleRequest('quote')
    quoteLoading.value = true
    quoteError.value = null
    try {
      const data = await api.getInstrumentQuote(reqCanonical, ctx.signal)
      // Compare canonical identity, never raw route param.
      if (!isCurrentSecondary({ ...ctx, name: 'quote', canonical: reqCanonical })) return
      quote.value = data
    } catch (error) {
      if (isAbortError(error) || !isCurrentSecondary({ ...ctx, name: 'quote', canonical: reqCanonical })) return
      quoteError.value = mapApiError(error)
    } finally {
      if (isCurrentSecondary({ ...ctx, name: 'quote', canonical: reqCanonical })) quoteLoading.value = false
    }
  }

  async function loadBars(): Promise<void> {
    if (!supportsBars.value) return
    const reqCanonical = resolvedCanonicalCode || canonicalCode.value
    if (!reqCanonical) return
    const reqInterval = interval.value
    const reqAdjustment = adjustment.value
    const reqLimit = barLimit.value
    const ctx = beginModuleRequest('bars')
    barsLoading.value = true
    barsError.value = null
    try {
      const data = await api.getInstrumentBars(
        reqCanonical,
        {
          interval: reqInterval,
          limit: reqLimit,
          ...(reqAdjustment ? { adjustment: reqAdjustment } : {}),
        },
        ctx.signal,
      )
      const stillParams =
        interval.value === reqInterval &&
        adjustment.value === reqAdjustment &&
        barLimit.value === reqLimit
      if (!isCurrentSecondary({ ...ctx, name: 'bars', canonical: reqCanonical })) return
      if (!stillParams) return
      bars.value = data
    } catch (error) {
      if (isAbortError(error) || !isCurrentSecondary({ ...ctx, name: 'bars', canonical: reqCanonical })) return
      if (interval.value !== reqInterval || adjustment.value !== reqAdjustment || barLimit.value !== reqLimit) return
      barsError.value = mapApiError(error)
    } finally {
      if (isCurrentSecondary({ ...ctx, name: 'bars', canonical: reqCanonical })) barsLoading.value = false
    }
  }

  async function loadOrderBook(force = false): Promise<void> {
    if (!supportsBook.value) return
    const reqCanonical = resolvedCanonicalCode || canonicalCode.value
    if (!reqCanonical) return
    if (!force && bookLoadedFor.value === reqCanonical && orderBook.value) return
    const ctx = beginModuleRequest('book')
    bookLoading.value = true
    bookError.value = null
    try {
      const data = await api.getInstrumentOrderBook(reqCanonical, ctx.signal)
      if (!isCurrentSecondary({ ...ctx, name: 'book', canonical: reqCanonical })) return
      orderBook.value = data
      bookLoadedFor.value = reqCanonical
    } catch (error) {
      if (isAbortError(error) || !isCurrentSecondary({ ...ctx, name: 'book', canonical: reqCanonical })) return
      bookError.value = mapApiError(error)
    } finally {
      if (isCurrentSecondary({ ...ctx, name: 'book', canonical: reqCanonical })) bookLoading.value = false
    }
  }

  async function loadCapitalFlow(force = false): Promise<void> {
    if (!supportsFlow.value) return
    const reqCanonical = resolvedCanonicalCode || canonicalCode.value
    if (!reqCanonical) return
    if (!force && flowLoadedFor.value === reqCanonical && capitalFlow.value) return
    const ctx = beginModuleRequest('flow')
    flowLoading.value = true
    flowError.value = null
    try {
      const data = await api.getInstrumentCapitalFlow(reqCanonical, ctx.signal)
      if (!isCurrentSecondary({ ...ctx, name: 'flow', canonical: reqCanonical })) return
      capitalFlow.value = data
      flowLoadedFor.value = reqCanonical
    } catch (error) {
      if (isAbortError(error) || !isCurrentSecondary({ ...ctx, name: 'flow', canonical: reqCanonical })) return
      flowError.value = mapApiError(error)
    } finally {
      if (isCurrentSecondary({ ...ctx, name: 'flow', canonical: reqCanonical })) flowLoading.value = false
    }
  }

  async function initialLoad(): Promise<void> {
    requestGeneration += 1
    requestedInputCode = code.value
    resolvedCanonicalCode = ''
    abortAllModules()
    clearAllPollTimers()
    bookLoadedFor.value = null
    flowLoadedFor.value = null
    quote.value = null
    bars.value = null
    orderBook.value = null
    capitalFlow.value = null
    quoteError.value = null
    barsError.value = null
    bookError.value = null
    flowError.value = null
    pageError.value = null
    pageNotFound.value = false

    const ok = await loadMetadata(true)
    if (!ok) return

    await Promise.all([
      loadSession(),
      loadQuote(),
      loadBars(),
    ])
    rescheduleAllPollers()
    if (activeTab.value === 'book') void loadOrderBook()
    if (activeTab.value === 'flow') void loadCapitalFlow()
  }

  async function refreshAll(): Promise<void> {
    if (!metadata.value || !resolvedCanonicalCode) {
      await initialLoad()
      return
    }
    await Promise.all([loadQuote(), loadBars()])
    if (activeTab.value === 'book') await loadOrderBook(true)
    if (activeTab.value === 'flow') await loadCapitalFlow(true)
    rescheduleAllPollers()
  }

  function setTab(tab: InstrumentTab): void {
    activeTab.value = tab
    if (tab === 'book') void loadOrderBook()
    if (tab === 'flow') void loadCapitalFlow()
    // Tab change only affects book/flow pollers; quote/bars/session keep cadence.
    scheduleBookPoll()
    scheduleFlowPoll()
  }

  function setInterval(next: BarInterval): void {
    if (interval.value === next) return
    interval.value = next
    void loadBars()
  }

  function setAdjustment(next: BarAdjustment): void {
    if (adjustment.value === next) return
    adjustment.value = next
    void loadBars()
  }

  function setBarLimit(next: number): void {
    if (barLimit.value === next) return
    barLimit.value = next
    void loadBars()
  }

  watch(
    code,
    (next, prev) => {
      if (!next || next === prev) return
      void initialLoad()
    },
    { immediate: true },
  )

  onBeforeUnmount(() => {
    abortAllModules()
    clearAllPollTimers()
    if (visibilityHandler) {
      document.removeEventListener('visibilitychange', visibilityHandler)
      visibilityHandler = null
    }
  })

  if (typeof document !== 'undefined') {
    visibilityHandler = onVisibilityChange
    document.addEventListener('visibilitychange', visibilityHandler)
  }

  return {
    activeTab,
    interval,
    adjustment,
    barLimit,
    metadata,
    quote,
    bars,
    orderBook,
    capitalFlow,
    session,
    identity,
    capabilities,
    instrumentMeta,
    canonicalCode,
    pageLoading,
    pageError,
    pageNotFound,
    quoteLoading,
    barsLoading,
    bookLoading,
    flowLoading,
    quoteError,
    barsError,
    bookError,
    flowError,
    supportsBook,
    supportsFlow,
    supportsBars,
    supportsQuote,
    setTab,
    setInterval,
    setAdjustment,
    setBarLimit,
    refreshAll,
    loadOrderBook,
    loadCapitalFlow,
    loadQuote,
    loadBars,
  }
}

export type InstrumentDetailController = ReturnType<typeof useInstrumentDetail>
