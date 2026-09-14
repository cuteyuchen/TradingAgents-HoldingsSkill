/**
 * Shared InstrumentDetail data controller.
 * Used by both full page and drawer — no duplicated request logic.
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

function pollingIntervalMs(session: MarketSessionResponse | null, module: 'quote' | 'book' | 'flow' | 'bars'): number | null {
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
  const bookRequestedRef = ref(false)
  const flowRequestedRef = ref(false)

  let abortController: AbortController | null = null
  let requestSeq = 0
  let activeCode = ''
  let quoteTimer: number | null = null
  let bookTimer: number | null = null
  let flowTimer: number | null = null
  let barsTimer: number | null = null
  let sessionTimer: number | null = null
  let visibilityHandler: (() => void) | null = null

  const identity = computed(() => metadata.value?.identity ?? null)
  const capabilities = computed(() => metadata.value?.capabilities ?? null)
  const instrumentMeta = computed(() => metadata.value?.metadata ?? null)
  const canonicalCode = computed(() => identity.value?.code || code.value)

  const supportsBook = computed(() => capabilities.value?.order_book === true)
  const supportsFlow = computed(() => capabilities.value?.capital_flow === true)
  const supportsBars = computed(() => capabilities.value?.bars !== false)
  const supportsQuote = computed(() => capabilities.value?.quote !== false)

  function clearTimers(): void {
    for (const t of [quoteTimer, bookTimer, flowTimer, barsTimer, sessionTimer]) {
      if (t !== null) window.clearTimeout(t)
    }
    quoteTimer = bookTimer = flowTimer = barsTimer = sessionTimer = null
  }

  function abortInFlight(): void {
    abortController?.abort()
    abortController = null
  }

  function beginRequest(): { signal: AbortSignal; seq: number; code: string } {
    abortInFlight()
    abortController = new AbortController()
    requestSeq += 1
    activeCode = code.value
    return { signal: abortController.signal, seq: requestSeq, code: activeCode }
  }

  function isCurrent(seq: number, requestCode: string): boolean {
    return seq === requestSeq && requestCode === activeCode && requestCode === code.value
  }

  async function loadSession(signal?: AbortSignal): Promise<void> {
    sessionLoading.value = true
    try {
      const data = await api.getMarketSession(signal)
      if (signal?.aborted) return
      session.value = data
    } catch {
      // Session is advisory for polling cadence; do not fail the page.
    } finally {
      if (!signal?.aborted) sessionLoading.value = false
    }
  }

  async function loadMetadata(force = false): Promise<boolean> {
    const { signal, seq, code: reqCode } = beginRequest()
    pageLoading.value = !metadata.value || force
    pageError.value = null
    pageNotFound.value = false
    try {
      const data = await api.getInstrument(reqCode, signal)
      if (!isCurrent(seq, reqCode)) return false
      metadata.value = data
      pageLoading.value = false
      const preferred = defaultAdjustment(data.capabilities)
      if (preferred && !data.capabilities.adjustments.includes(adjustment.value as BarAdjustment)) {
        adjustment.value = preferred
      } else if (!adjustment.value) {
        adjustment.value = preferred
      }
      return true
    } catch (error) {
      if (isAbortError(error) || !isCurrent(seq, reqCode)) return false
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
    const reqCode = canonicalCode.value
    if (!reqCode) return
    quoteLoading.value = true
    quoteError.value = null
    const prevAbort = abortController
    // Quote is independent of page identity load; use dedicated abort via local signal
    const local = new AbortController()
    const signal = local.signal
    // If page-level abort fires, also abort quote
    const onAbort = () => local.abort()
    prevAbort?.signal.addEventListener('abort', onAbort, { once: true })
    try {
      const data = await api.getInstrumentQuote(reqCode, signal)
      if (signal.aborted || reqCode !== code.value) return
      quote.value = data
    } catch (error) {
      if (isAbortError(error) || signal.aborted) return
      quoteError.value = mapApiError(error)
    } finally {
      prevAbort?.signal.removeEventListener('abort', onAbort)
      if (!signal.aborted) quoteLoading.value = false
    }
  }

  async function loadBars(): Promise<void> {
    if (!supportsBars.value) return
    const reqCode = canonicalCode.value
    if (!reqCode) return
    barsLoading.value = true
    barsError.value = null
    const reqInterval = interval.value
    const reqAdjustment = adjustment.value
    const reqLimit = barLimit.value
    const local = new AbortController()
    const signal = local.signal
    const onAbort = () => local.abort()
    abortController?.signal.addEventListener('abort', onAbort, { once: true })
    try {
      const data = await api.getInstrumentBars(
        reqCode,
        {
          interval: reqInterval,
          limit: reqLimit,
          ...(reqAdjustment ? { adjustment: reqAdjustment } : {}),
        },
        signal,
      )
      if (signal.aborted || reqCode !== code.value) return
      if (data.interval !== reqInterval || data.adjustment !== (reqAdjustment ?? data.adjustment)) {
        // Stale after param change
        if (reqInterval !== interval.value || reqAdjustment !== adjustment.value) return
      }
      bars.value = data
    } catch (error) {
      if (isAbortError(error) || signal.aborted) return
      if (reqCode !== code.value || reqInterval !== interval.value) return
      barsError.value = mapApiError(error)
    } finally {
      abortController?.signal.removeEventListener('abort', onAbort)
      if (!signal.aborted) barsLoading.value = false
    }
  }

  async function loadOrderBook(force = false): Promise<void> {
    if (!supportsBook.value) return
    const reqCode = canonicalCode.value
    if (!reqCode) return
    if (!force && bookLoadedFor.value === reqCode && orderBook.value) return
    bookLoading.value = true
    bookError.value = null
    const local = new AbortController()
    const signal = local.signal
    const onAbort = () => local.abort()
    abortController?.signal.addEventListener('abort', onAbort, { once: true })
    try {
      const data = await api.getInstrumentOrderBook(reqCode, signal)
      if (signal.aborted || reqCode !== code.value) return
      orderBook.value = data
      bookLoadedFor.value = reqCode
    } catch (error) {
      if (isAbortError(error) || signal.aborted) return
      if (reqCode !== code.value) return
      bookError.value = mapApiError(error)
    } finally {
      abortController?.signal.removeEventListener('abort', onAbort)
      if (!signal.aborted) bookLoading.value = false
    }
  }

  async function loadCapitalFlow(force = false): Promise<void> {
    if (!supportsFlow.value) return
    const reqCode = canonicalCode.value
    if (!reqCode) return
    if (!force && flowLoadedFor.value === reqCode && capitalFlow.value) return
    flowLoading.value = true
    flowError.value = null
    const local = new AbortController()
    const signal = local.signal
    const onAbort = () => local.abort()
    abortController?.signal.addEventListener('abort', onAbort, { once: true })
    try {
      const data = await api.getInstrumentCapitalFlow(reqCode, signal)
      if (signal.aborted || reqCode !== code.value) return
      capitalFlow.value = data
      flowLoadedFor.value = reqCode
    } catch (error) {
      if (isAbortError(error) || signal.aborted) return
      if (reqCode !== code.value) return
      flowError.value = mapApiError(error)
    } finally {
      abortController?.signal.removeEventListener('abort', onAbort)
      if (!signal.aborted) flowLoading.value = false
    }
  }

  async function initialLoad(): Promise<void> {
    abortInFlight()
    clearTimers()
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

    // Parallel secondary loads after identity is known
    await Promise.all([
      loadSession(abortController?.signal),
      loadQuote(),
      loadBars(),
    ])
    schedulePolling()
    if (activeTab.value === 'book') void loadOrderBook()
    if (activeTab.value === 'flow') void loadCapitalFlow()
  }

  async function refreshAll(): Promise<void> {
    if (!metadata.value) {
      await initialLoad()
      return
    }
    await Promise.all([loadQuote(), loadBars()])
    if (activeTab.value === 'book') await loadOrderBook(true)
    if (activeTab.value === 'flow') await loadCapitalFlow(true)
    schedulePolling()
  }

  function schedulePolling(): void {
    clearTimers()
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    const s = session.value
    const quoteMs = pollingIntervalMs(s, 'quote')
    if (quoteMs !== null) {
      quoteTimer = window.setTimeout(async () => {
        await loadQuote()
        schedulePolling()
      }, quoteMs)
    }
    if (activeTab.value === 'book' && supportsBook.value) {
      const ms = pollingIntervalMs(s, 'book')
      if (ms !== null) {
        bookTimer = window.setTimeout(async () => {
          await loadOrderBook(true)
          schedulePolling()
        }, ms)
      }
    }
    if (activeTab.value === 'flow' && supportsFlow.value) {
      const ms = pollingIntervalMs(s, 'flow')
      if (ms !== null) {
        flowTimer = window.setTimeout(async () => {
          await loadCapitalFlow(true)
          schedulePolling()
        }, ms)
      }
    }
    if (supportsBars.value) {
      const ms = pollingIntervalMs(s, 'bars')
      if (ms !== null) {
        barsTimer = window.setTimeout(async () => {
          await loadBars()
          schedulePolling()
        }, ms)
      }
    }
    // Session refresh hourly-ish when open
    if (s && (s.session === 'MORNING' || s.session === 'AFTERNOON' || s.session === 'LUNCH_BREAK')) {
      sessionTimer = window.setTimeout(async () => {
        await loadSession()
        schedulePolling()
      }, 60_000)
    }
  }

  function onVisibilityChange(): void {
    if (document.visibilityState === 'hidden') {
      clearTimers()
    } else {
      schedulePolling()
    }
  }

  function setTab(tab: InstrumentTab): void {
    activeTab.value = tab
    if (tab === 'book') void loadOrderBook()
    if (tab === 'flow') void loadCapitalFlow()
    schedulePolling()
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
    abortInFlight()
    clearTimers()
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
