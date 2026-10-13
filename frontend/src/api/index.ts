import type {
  AnalysisJob,
  AnalysisMode,
  AnalysisRunDetail,
  AnalysisRunSummary,
  BatchQuoteResponse,
  CapitalFlow,
  InstrumentBarsQuery,
  InstrumentBarsResponse,
  InstrumentMarketSnapshot,
  InstrumentMetadataResponse,
  InstrumentQuote,
  OrderBook,
  BacktestRun,
  CalibrationReport,
  DailyDashboard,
  FuyaoContribution,
  FuyaoMarketBrief,
  FuyaoSecurityContext,
  FuyaoStatus,
  FuyaoConfig,
  MajorIndexQuote,
  MarketOverview,
  MarketSessionResponse,
  SystemicRiskSnapshot,
  DashboardDiagnostics,
  DashboardHealth,
  DashboardTimeline,
  HoldingUpload,
  ModelProfile,
  ModelProvider,
  NotificationChannel,
  OperatingNotificationList,
  GovernanceEventListResponse,
  GovernanceHealth,
  GovernanceRegistryResponse,
  HistoryCoverage,
  HistorySyncRun,
  Holding,
  LedgerImportCommitResult,
  LedgerImportPreview,
  ParameterChangeProposal,
  ParameterSetListResponse,
  ParameterSetVersion,
  ParsedHoldings,
  ProposalListResponse,
  Portfolio,
  PortfolioAccountState,
  PortfolioSnapshot,
  RecomputeCapabilityManifest,
  ReplayAvailabilityManifest,
  SystemBackup,
  SystemBackupList,
  SystemDiagnostics,
  SystemHealth,
  LiveValidationReadiness,
  SystemReadiness,
  SystemRecoveryReport,
  SystemRelease,
  ResearchScope,
  ReplayMode,
  Schedule,
  ShadowAccount,
  ShadowDailySnapshot,
  ShadowDecision,
  ShadowDecisionDetail,
  ShadowFill,
  ShadowOrder,
  ShadowPerformance,
  ShadowValidation,
  TokenPair,
  TradeLedgerEntry,
  User,
} from './types'

const ACCESS_KEY = 'advisor_v2_access_token'
const REFRESH_KEY = 'advisor_v2_refresh_token'
const SESSION_KEY = 'advisor_v2_session_id'

export type ApiErrorKind = 'auth' | 'forbidden' | 'not_found' | 'conflict' | 'validation' | 'server' | 'network' | 'timeout' | 'cancelled' | 'unknown'

export class ApiError extends Error {
  readonly status: number | null
  readonly code: string | null
  readonly kind: ApiErrorKind
  readonly requestId: string | null
  readonly fieldErrors: Record<string, string[]>

  constructor(message: string, options: {
    status?: number | null
    code?: string | null
    kind?: ApiErrorKind
    requestId?: string | null
    fieldErrors?: Record<string, string[]>
    cause?: unknown
  } = {}) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause })
    this.name = 'ApiError'
    this.status = options.status ?? null
    this.code = options.code ?? null
    this.kind = options.kind || 'unknown'
    this.requestId = options.requestId ?? null
    this.fieldErrors = options.fieldErrors || {}
  }
}

const statusMessages: Record<number, string> = {
  401: '登录状态已过期，请重新登录。',
  403: '当前账户没有权限执行此操作。',
  404: '请求的资源不存在，可能已被删除或不属于当前组合。',
  409: '当前操作与已有数据冲突，请刷新后再试。',
  422: '提交内容未通过校验，请检查标记的字段。',
  500: '后端发生系统错误，请稍后重试。',
  502: '后端网关暂时不可用，请稍后重试。',
  503: '后端服务暂时不可用，请确认服务状态后重试。',
  504: '后端响应超时，请稍后重试。',
}

function requestIdHeader(res: Response): string | null {
  return res.headers.get('X-Request-ID') || res.headers.get('x-request-id')
}

function requestIdValue(): string {
  const uuid = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2)
  return `web_${uuid}`
}

function statusKind(status: number): ApiErrorKind {
  if (status === 401) return 'auth'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 422) return 'validation'
  if (status >= 500) return 'server'
  return 'unknown'
}

function payloadDetails(payload: any): { message: string; code: string | null; fieldErrors: Record<string, string[]> } {
  const detail = payload?.detail ?? payload?.error ?? payload
  const fieldErrors: Record<string, string[]> = {}
  if (Array.isArray(detail)) {
    for (const item of detail) {
      const path = Array.isArray(item?.loc) ? item.loc.filter((part: unknown) => part !== 'body').join('.') : 'form'
      const message = String(item?.msg || item?.message || item || '')
      if (message) fieldErrors[path] = [...(fieldErrors[path] || []), message]
    }
  }
  const code = typeof detail?.code === 'string' ? detail.code : typeof payload?.code === 'string' ? payload.code : null
  const message = typeof detail === 'string'
    ? detail
    : typeof detail?.message === 'string'
      ? detail.message
      : Object.values(fieldErrors).flat().join('；')
  return { message, code, fieldErrors }
}

export function getAccessToken(): string { return localStorage.getItem(ACCESS_KEY) || '' }
export function getRefreshToken(): string { return localStorage.getItem(REFRESH_KEY) || '' }
export function getSessionId(): string | null { return localStorage.getItem(SESSION_KEY) }
let sessionGeneration = 0
let observedSessionId = typeof window === 'undefined' ? null : localStorage.getItem(SESSION_KEY)
let refreshPromise: Promise<boolean> | null = null
let refreshController: AbortController | null = null

export function getSessionGeneration(): number { return sessionGeneration }

function invalidateSessionRequests(): void {
  sessionGeneration += 1
  refreshController?.abort()
  refreshController = null
  refreshPromise = null
}

function notifySessionChanged(): void {
  window.dispatchEvent(new CustomEvent('advisor-session-changed'))
}

export function saveSession(tokens: TokenPair): void {
  invalidateSessionRequests()
  localStorage.setItem(ACCESS_KEY, tokens.access_token)
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
  localStorage.setItem(SESSION_KEY, requestIdValue())
  observedSessionId = localStorage.getItem(SESSION_KEY)
  notifySessionChanged()
}
export function clearSession(): void {
  invalidateSessionRequests()
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(SESSION_KEY)
  observedSessionId = null
  notifySessionChanged()
}
export function hasSession(): boolean { return Boolean(getAccessToken() || getRefreshToken()) }

interface RequestOptions { method?: string; body?: unknown; public?: boolean; headers?: Record<string, string>; retryAuth?: boolean; timeoutMs?: number; signal?: AbortSignal }

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    // 同账户令牌轮换不清组合选择；换号或退出才使本页私有请求与缓存失效。
    if (event.storageArea && event.storageArea !== localStorage) return
    if (event.key !== null && ![ACCESS_KEY, REFRESH_KEY, SESSION_KEY].includes(event.key)) return
    const currentSessionId = localStorage.getItem(SESSION_KEY)
    if (currentSessionId && currentSessionId === observedSessionId && getAccessToken() && getRefreshToken()) return
    observedSessionId = currentSessionId
    invalidateSessionRequests()
    notifySessionChanged()
  })
}

export function isCancelledRequest(error: unknown): boolean {
  if (error instanceof ApiError) return error.kind === 'cancelled'
  return error instanceof DOMException && error.name === 'AbortError'
}

/** 截止时间同时适用于网络读取和共享刷新等待；调用者取消不能伪装成超时。 */
async function withDeadline<T>(
  work: (signal: AbortSignal) => Promise<T>,
  options: { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<T> {
  if (options.signal?.aborted) throw new ApiError('请求已取消。', { kind: 'cancelled' })
  const controller = new AbortController()
  let abortKind: 'timeout' | 'cancelled' | null = null
  let rejectAbort: (error: ApiError) => void = () => undefined
  const interrupted = new Promise<never>((_resolve, reject) => { rejectAbort = reject })
  const abort = (kind: 'timeout' | 'cancelled') => {
    if (abortKind) return
    abortKind = kind
    controller.abort()
    rejectAbort(new ApiError(kind === 'timeout' ? '后端响应超时，请稍后重试。' : '请求已取消。', { kind }))
  }
  const abortExternal = () => abort('cancelled')
  const timeout = window.setTimeout(() => abort('timeout'), options.timeoutMs ?? 30_000)
  options.signal?.addEventListener('abort', abortExternal, { once: true })
  try {
    return await Promise.race([
      work(controller.signal),
      interrupted,
    ])
  } catch (cause) {
    if (abortKind) throw new ApiError(abortKind === 'timeout' ? '后端响应超时，请稍后重试。' : '请求已取消。', { kind: abortKind, cause })
    if (cause instanceof ApiError) throw cause
    throw new ApiError('无法连接后端，请确认服务已启动。', { kind: 'network', cause })
  } finally {
    window.clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abortExternal)
  }
}

function fetchWithDeadline<T>(
  path: string,
  init: RequestInit,
  read: (response: Response) => Promise<T>,
  options: { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<T> {
  return withDeadline(signal => fetch(path, { ...init, signal }).then(read), options)
}

async function parseError(res: Response): Promise<ApiError> {
  let payload: any = null
  try {
    payload = await res.json()
  } catch { /* Empty or non-JSON responses still get a stable user message. */ }
  const parsed = payloadDetails(payload)
  const generic = statusMessages[res.status] || `${res.status} ${res.statusText || '请求失败'}`
  const safeDetail = res.status >= 500 ? '' : parsed.message
  const message = safeDetail ? `${generic} ${safeDetail}` : generic
  return new ApiError(message, {
    status: res.status,
    code: parsed.code,
    kind: statusKind(res.status),
    requestId: requestIdHeader(res),
    fieldErrors: parsed.fieldErrors,
  })
}

export function errorMessage(error: unknown, fallback = '请求失败，请稍后重试。'): string {
  if (typeof error === 'string' && error) return error
  return error instanceof ApiError || error instanceof Error ? error.message : fallback
}

export function requestIdOf(error: unknown): string | null {
  return error instanceof ApiError ? error.requestId : null
}

async function refreshSession(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false
  if (!refreshPromise) {
    const generation = sessionGeneration
    const sessionId = localStorage.getItem(SESSION_KEY)
    const controller = new AbortController()
    refreshController = controller
    const pending = fetchWithDeadline('/api/v2/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': requestIdValue() },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }, async (res) => {
      if (res.status === 401 || res.status === 403) return null
      if (!res.ok) throw await parseError(res)
      const tokens = (await res.json()) as TokenPair
      if (!tokens?.access_token || !tokens.refresh_token || typeof tokens.access_token !== 'string' || typeof tokens.refresh_token !== 'string') {
        throw new ApiError('后端返回了无效的登录凭证。', { kind: 'server' })
      }
      return tokens
    }, { signal: controller.signal })
    .then((tokens) => {
      if (controller.signal.aborted || generation !== sessionGeneration || sessionId !== localStorage.getItem(SESSION_KEY)) return false
      // 另一标签页可能已完成同一会话的轮换，不能用旧 refresh 的拒绝结果清掉新令牌。
      if (refreshToken !== getRefreshToken()) return hasSession()
      if (!tokens) return false
      // 同一会话内的轮换不触发换号清缓存；迟到响应必须先核对会话和旧令牌。
      localStorage.setItem(ACCESS_KEY, tokens.access_token)
      localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
      return true
    }).finally(() => {
      if (refreshPromise === pending) refreshPromise = null
      if (refreshController === controller) refreshController = null
    })
    refreshPromise = pending
  }
  return refreshPromise
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const generation = sessionGeneration
  const sessionId = localStorage.getItem(SESSION_KEY)
  const sessionCurrent = () => generation === sessionGeneration && sessionId === localStorage.getItem(SESSION_KEY)
  const headers: Record<string, string> = { ...(options.headers || {}) }
  headers['X-Request-ID'] ||= requestIdValue()
  const isForm = options.body instanceof FormData
  let body: BodyInit | undefined
  if (options.body !== undefined) {
    if (isForm) body = options.body as FormData
    else { headers['Content-Type'] = 'application/json'; body = JSON.stringify(options.body) }
  }
  const token = getAccessToken()
  if (token && !options.public) headers.Authorization = `Bearer ${token}`
  const result = await fetchWithDeadline(path, { method: options.method || 'GET', headers, body }, async (res) => {
    if (!res.ok) return { response: res, error: await parseError(res), data: undefined }
    if (res.status === 204) return { response: res, error: null, data: undefined }
    try {
      return { response: res, error: null, data: (await res.json()) as T }
    } catch (cause) {
      throw new ApiError('后端返回了无法解析的数据。', { kind: 'server', requestId: requestIdHeader(res), cause })
    }
  }, options)
  if (!sessionCurrent()) throw new ApiError('会话已切换，请求已取消。', { kind: 'cancelled' })
  const res = result.response
  if (res.status === 401 && !options.public && options.retryAuth !== false) {
    // 只取消本请求的等待，不能中止其他调用者共用的 refresh。
    const refreshed = await withDeadline(() => refreshSession(), options)
    if (!sessionCurrent() || options.signal?.aborted) throw new ApiError('请求已取消。', { kind: 'cancelled' })
    if (refreshed) return request<T>(path, { ...options, retryAuth: false })
    clearSession()
    window.dispatchEvent(new CustomEvent('advisor-auth-expired'))
  }
  if (result.error) throw result.error
  return result.data as T
}

async function registerAndLogin(payload: { email: string; username?: string; password: string }): Promise<TokenPair> {
  const generation = sessionGeneration
  const sessionId = localStorage.getItem(SESSION_KEY)
  await request<User>('/api/v2/auth/register', { method: 'POST', body: payload, public: true })
  if (generation !== sessionGeneration || sessionId !== localStorage.getItem(SESSION_KEY)) {
    throw new ApiError('会话已切换，请求已取消。', { kind: 'cancelled' })
  }
  return request<TokenPair>('/api/v2/auth/login', {
    method: 'POST', body: { email: payload.email, password: payload.password, device_info: navigator.userAgent }, public: true,
  })
}

export const api = {
  register: registerAndLogin,
  login: (payload: { email: string; password: string; device_info?: string }) => request<TokenPair>('/api/v2/auth/login', { method: 'POST', body: payload, public: true }),
  logout: (refreshToken = getRefreshToken()) => request<void>('/api/v2/auth/logout', { method: 'POST', body: { refresh_token: refreshToken }, retryAuth: false }),
  me: () => request<User>('/api/v2/auth/me'),

  listProviders: () => request<ModelProvider[]>('/api/v2/model-settings/providers'),
  createProvider: (payload: Record<string, unknown>) => request<ModelProvider>('/api/v2/model-settings/providers', { method: 'POST', body: payload }),
  updateProvider: (id: number, payload: Record<string, unknown>) => request<ModelProvider>(`/api/v2/model-settings/providers/${id}`, { method: 'PATCH', body: payload }),
  deleteProvider: (id: number) => request<void>(`/api/v2/model-settings/providers/${id}`, { method: 'DELETE' }),
  listProfiles: () => request<ModelProfile[]>('/api/v2/model-settings/profiles'),
  createProfile: (payload: Record<string, unknown>) => request<ModelProfile>('/api/v2/model-settings/profiles', { method: 'POST', body: payload }),
  updateProfile: (id: number, payload: Record<string, unknown>) => request<ModelProfile>(`/api/v2/model-settings/profiles/${id}`, { method: 'PATCH', body: payload }),
  deleteProfile: (id: number) => request<void>(`/api/v2/model-settings/profiles/${id}`, { method: 'DELETE' }),
  testProfile: (id: number) => request<{ status: string; message: string; latency_ms?: number; raw_excerpt?: string }>(`/api/v2/model-settings/profiles/${id}/test`, { method: 'POST' }),

  listPortfolios: () => request<Portfolio[]>('/api/v2/portfolios'),
  createPortfolio: (payload: { name: string; market?: string; currency?: string; is_default?: boolean }) => request<Portfolio>('/api/v2/portfolios', { method: 'POST', body: payload }),
  updatePortfolio: (id: number, payload: Record<string, unknown>) => request<Portfolio>(`/api/v2/portfolios/${id}`, { method: 'PATCH', body: payload }),
  deletePortfolio: (id: number) => request<void>(`/api/v2/portfolios/${id}`, { method: 'DELETE' }),
  uploadHoldings: (portfolioId: number, file: File, parsed?: ParsedHoldings) => {
    const form = new FormData(); form.append('screenshot', file); if (parsed) form.append('holdings_json', JSON.stringify(parsed))
    return request<HoldingUpload>(`/api/v2/portfolios/${portfolioId}/uploads`, { method: 'POST', body: form })
  },
  getUpload: (id: number) => request<HoldingUpload>(`/api/v2/uploads/${id}`),
  resolveHolding: (holding: Holding, portfolioId?: number | null, signal?: AbortSignal) => request<Holding>(
    `/api/v2/holdings/resolve${portfolioId ? `?portfolio_id=${portfolioId}` : ''}`,
    { method: 'POST', body: holding, signal },
  ),
  retryUploadParse: (id: number) => request<HoldingUpload>(`/api/v2/uploads/${id}/parse`, { method: 'POST' }),
  updateParsedHoldings: (id: number, parsed: ParsedHoldings) => request<HoldingUpload>(`/api/v2/uploads/${id}/parsed-holdings`, { method: 'PATCH', body: { parsed } }),
  confirmUpload: (id: number) => request<PortfolioSnapshot>(`/api/v2/uploads/${id}/confirm`, { method: 'POST' }),
  getUploadImage: (id: number) => requestBlob(`/api/v2/uploads/${id}/image`),
  listSnapshots: (portfolioId: number) => request<PortfolioSnapshot[]>(`/api/v2/portfolios/${portfolioId}/snapshots`),
  getSnapshot: (id: number, signal?: AbortSignal) => request<PortfolioSnapshot>(`/api/v2/snapshots/${id}`, { signal }),

  getPortfolioState: (portfolioId: number, signal?: AbortSignal) => request<PortfolioAccountState>(`/api/v3/portfolios/${portfolioId}/state`, { signal }),
  listLedgerEntries: (portfolioId: number) => request<TradeLedgerEntry[]>(`/api/v3/portfolios/${portfolioId}/ledger`),
  createLedgerEntry: (portfolioId: number, payload: Record<string, unknown>) =>
    request<TradeLedgerEntry>(`/api/v3/portfolios/${portfolioId}/ledger`, { method: 'POST', body: payload }),
  reviseLedgerEntry: (portfolioId: number, entryId: number, payload: { changes: Record<string, unknown>; reason: string }) =>
    request<TradeLedgerEntry>(`/api/v3/portfolios/${portfolioId}/ledger/${entryId}/revise`, { method: 'POST', body: payload }),
  voidLedgerEntry: (portfolioId: number, entryId: number, reason: string) =>
    request<TradeLedgerEntry>(`/api/v3/portfolios/${portfolioId}/ledger/${entryId}/void`, { method: 'POST', body: { reason } }),
  previewLedgerImport: (portfolioId: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<LedgerImportPreview>(`/api/v3/portfolios/${portfolioId}/ledger/import/preview`, { method: 'POST', body: form })
  },
  commitLedgerImport: (portfolioId: number, payload: { source_ref: string; rows: Record<string, unknown>[]; label?: string }) =>
    request<LedgerImportCommitResult>(`/api/v3/portfolios/${portfolioId}/ledger/import`, { method: 'POST', body: payload }),

  createAnalysisJob: (snapshotId: number, mode: AnalysisMode, checkpoint?: string, notify = true) => request<AnalysisJob>('/api/v2/analysis/jobs', { method: 'POST', body: { snapshot_id: snapshotId, mode, checkpoint, notify } }),
  getAnalysisJob: (id: number) => request<AnalysisJob>(`/api/v2/analysis/jobs/${id}`),
  cancelAnalysisJob: (id: number) => request<AnalysisJob>(`/api/v2/analysis/jobs/${id}/cancel`, { method: 'POST' }),
  retryAnalysisJob: (id: number) => request<AnalysisJob>(`/api/v2/analysis/jobs/${id}/retry`, { method: 'POST' }),
  listRuns: (portfolioId?: number) => request<AnalysisRunSummary[]>(`/api/v2/analysis/runs${portfolioId ? `?portfolio_id=${portfolioId}` : ''}`),
  getRun: (id: number) => request<AnalysisRunDetail>(`/api/v2/analysis/runs/${id}`),
  compareRun: (id: number) => request<Record<string, unknown>>(`/api/v2/analysis/runs/${id}/comparison`),

  listShadowAccounts: (portfolioId?: number) => request<ShadowAccount[]>(
    `/api/v3/shadow/accounts${portfolioId ? `?portfolio_id=${portfolioId}` : ''}`,
  ),
  createShadowAccount: (payload: { portfolio_id: number; snapshot_id?: number | null; name?: string }) =>
    request<ShadowAccount>('/api/v3/shadow/accounts', { method: 'POST', body: payload }),
  getShadowAccount: (accountId: number) => request<ShadowAccount>(`/api/v3/shadow/accounts/${accountId}`),
  pauseShadowAccount: (accountId: number) => request<ShadowAccount>(`/api/v3/shadow/accounts/${accountId}/pause`, { method: 'POST' }),
  resumeShadowAccount: (accountId: number) => request<ShadowAccount>(`/api/v3/shadow/accounts/${accountId}/resume`, { method: 'POST' }),
  rebaseShadowAccount: (accountId: number, snapshotId?: number | null) => request<ShadowAccount>(
    `/api/v3/shadow/accounts/${accountId}/rebase`,
    { method: 'POST', body: { snapshot_id: snapshotId ?? null, acknowledge: true } },
  ),
  listShadowDecisions: (params: { portfolio_id?: number; account_id?: number; final_action?: string; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.portfolio_id) query.set('portfolio_id', String(params.portfolio_id))
    if (params.account_id) query.set('account_id', String(params.account_id))
    if (params.final_action) query.set('final_action', params.final_action)
    if (params.limit) query.set('limit', String(params.limit))
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<ShadowDecision[]>(`/api/v3/shadow/decisions${suffix}`)
  },
  getShadowDecision: (observationId: number) => request<ShadowDecisionDetail>(`/api/v3/shadow/decisions/${observationId}`),
  alignShadowDecision: (observationId: number) => request<{ items: ShadowDecisionDetail['actual_alignment'] }>(
    `/api/v3/shadow/decisions/${observationId}/actual-alignment`,
    { method: 'POST' },
  ),
  listShadowOrders: (params: { account_id?: number; portfolio_id?: number; generation?: number; status?: string; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.account_id) query.set('account_id', String(params.account_id))
    if (params.portfolio_id) query.set('portfolio_id', String(params.portfolio_id))
    if (params.generation) query.set('generation', String(params.generation))
    if (params.status) query.set('status', params.status)
    if (params.limit) query.set('limit', String(params.limit))
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<ShadowOrder[]>(`/api/v3/shadow/orders${suffix}`)
  },
  listShadowFills: (params: { account_id?: number; portfolio_id?: number; generation?: number; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.account_id) query.set('account_id', String(params.account_id))
    if (params.portfolio_id) query.set('portfolio_id', String(params.portfolio_id))
    if (params.generation) query.set('generation', String(params.generation))
    if (params.limit) query.set('limit', String(params.limit))
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<ShadowFill[]>(`/api/v3/shadow/fills${suffix}`)
  },
  getShadowPerformance: (accountId: number, generation?: number) => request<ShadowPerformance>(
    `/api/v3/shadow/performance?account_id=${accountId}${generation ? `&generation=${generation}` : ''}`,
  ),
  getShadowValidation: (portfolioId?: number) => request<ShadowValidation>(
    `/api/v3/shadow/validation${portfolioId ? `?portfolio_id=${portfolioId}` : ''}`,
  ),
  listShadowDailySnapshots: (params: { account_id?: number; portfolio_id?: number; generation?: number; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.account_id) query.set('account_id', String(params.account_id))
    if (params.portfolio_id) query.set('portfolio_id', String(params.portfolio_id))
    if (params.generation) query.set('generation', String(params.generation))
    if (params.limit) query.set('limit', String(params.limit))
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<ShadowDailySnapshot[]>(`/api/v3/shadow/daily${suffix}`)
  },

  listSchedules: () => request<Schedule[]>('/api/v2/schedules'),
  createSchedule: (payload: Record<string, unknown>) => request<Schedule>('/api/v2/schedules', { method: 'POST', body: payload }),
  updateSchedule: (id: number, payload: Record<string, unknown>) => request<Schedule>(`/api/v2/schedules/${id}`, { method: 'PATCH', body: payload }),
  deleteSchedule: (id: number) => request<void>(`/api/v2/schedules/${id}`, { method: 'DELETE' }),
  runScheduleNow: (id: number) => request<AnalysisJob>(`/api/v2/schedules/${id}/run-now`, { method: 'POST' }),

  listNotifications: () => request<NotificationChannel[]>('/api/v2/notifications'),
  createNotification: (payload: Record<string, unknown>) => request<NotificationChannel>('/api/v2/notifications', { method: 'POST', body: payload }),
  updateNotification: (id: number, payload: Record<string, unknown>) => request<NotificationChannel>(`/api/v2/notifications/${id}`, { method: 'PATCH', body: payload }),
  deleteNotification: (id: number) => request<void>(`/api/v2/notifications/${id}`, { method: 'DELETE' }),
  testNotification: (id: number) => request<{ status: string; message: string }>(`/api/v2/notifications/${id}/test`, { method: 'POST' }),

  getDashboardToday: (portfolioId: number, signal?: AbortSignal) => request<DailyDashboard>(`/api/v3/portfolios/${portfolioId}/dashboard/today`, { signal }),
  getDailyPlan: (portfolioId: number, signal?: AbortSignal) => request<Record<string, any>>(`/api/v3/triggers/daily-plan?portfolio_id=${portfolioId}`, { signal }),
  refreshDailyPlan: (portfolioId: number) => request<Record<string, any>>('/api/v3/triggers/daily-plan/refresh', { method: 'POST', body: { portfolio_id: portfolioId } }),
  recheckDailyAction: (planId: number) => request<Record<string, any>>(`/api/v3/triggers/plans/${planId}/recheck`, { method: 'POST' }),
  linkDailyActionFill: (planId: number, ledgerEntryId: number) => request<Record<string, any>>(`/api/v3/triggers/plans/${planId}/fills`, { method: 'POST', body: { ledger_entry_id: ledgerEntryId } }),
  getAccountReview: (portfolioId: number, signal?: AbortSignal) => request<Record<string, any>>(`/api/v3/portfolios/${portfolioId}/memory/performance`, { signal }),
  refreshLearning: (portfolioId: number) => request<Record<string, any>>(`/api/v3/portfolios/${portfolioId}/memory/learning/refresh`, { method: 'POST', body: {} }),
  getDashboardTimeline: (portfolioId: number) => request<DashboardTimeline>(`/api/v3/portfolios/${portfolioId}/dashboard/timeline`),
  getDashboardHealth: (portfolioId: number) => request<DashboardHealth>(`/api/v3/portfolios/${portfolioId}/dashboard/health`),
  getDashboardDiagnostics: (portfolioId: number) => request<DashboardDiagnostics>(`/api/v3/portfolios/${portfolioId}/dashboard/diagnostics`),
  getMarketSession: (signal?: AbortSignal) => request<MarketSessionResponse>('/api/v3/market/session', { signal }),
  getInstrument: (code: string, signal?: AbortSignal) => request<InstrumentMetadataResponse>(`/api/v3/market/instruments/${encodeURIComponent(code)}`, { signal }),
  getInstrumentQuote: (code: string, signal?: AbortSignal) => request<InstrumentQuote>(`/api/v3/market/instruments/${encodeURIComponent(code)}/quote`, { signal }),
  getInstrumentQuotes: (codes: string[], signal?: AbortSignal) => request<BatchQuoteResponse>('/api/v3/market/instruments/quotes', { method: 'POST', body: { codes }, signal }),
  getInstrumentBars: (code: string, query: InstrumentBarsQuery = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) params.set(key, String(value))
    }
    return request<InstrumentBarsResponse>(`/api/v3/market/instruments/${encodeURIComponent(code)}/bars?${params}`, { signal })
  },
  getInstrumentOrderBook: (code: string, signal?: AbortSignal) => request<OrderBook>(`/api/v3/market/instruments/${encodeURIComponent(code)}/order-book`, { signal }),
  getInstrumentCapitalFlow: (code: string, signal?: AbortSignal) => request<CapitalFlow>(`/api/v3/market/instruments/${encodeURIComponent(code)}/capital-flow`, { signal }),
  getInstrumentSnapshot: (code: string, signal?: AbortSignal) => request<InstrumentMarketSnapshot>(`/api/v3/market/instruments/${encodeURIComponent(code)}/snapshot`, { signal }),
  getMajorIndices: (signal?: AbortSignal) => request<MajorIndexQuote[]>('/api/v3/market/major-indices', { signal }),
  getSystemicRisk: (signal?: AbortSignal) => request<SystemicRiskSnapshot>('/api/v3/market/systemic-risk', { signal }),
  getMarketOverview: (signal?: AbortSignal) => request<MarketOverview>('/api/v3/market/overview', { signal }),
  getFuyaoStatus: (probe = false) => request<FuyaoStatus>(`/api/v3/fuyao/status${probe ? '?probe=true' : ''}`),
  getFuyaoConfig: () => request<FuyaoConfig>('/api/v3/fuyao/config'),
  saveFuyaoConfig: (apiKey: string) => request<FuyaoConfig>('/api/v3/fuyao/config', { method: 'PUT', body: { api_key: apiKey } }),
  resetFuyaoConfig: () => request<FuyaoConfig>('/api/v3/fuyao/config', { method: 'DELETE' }),
  getFuyaoMarketBrief: (refresh = false) => request<{ brief: FuyaoMarketBrief; score: Record<string, any>; production_score_changed: boolean; all_a_median_definition: string; top5_definition: string }>(`/api/v3/fuyao/market-brief${refresh ? '?refresh=true' : ''}`),
  getFuyaoSecurityContext: (code: string) => request<FuyaoSecurityContext>(`/api/v3/fuyao/securities/${encodeURIComponent(code)}`),
  getPortfolioContribution: (portfolioId: number) => request<FuyaoContribution>(`/api/v3/fuyao/portfolios/${portfolioId}/contribution`),
  getReplayAvailability: (params: { start_date?: string; end_date?: string; portfolio_id?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.start_date) query.set('start_date', params.start_date)
    if (params.end_date) query.set('end_date', params.end_date)
    if (params.portfolio_id) query.set('portfolio_id', String(params.portfolio_id))
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<ReplayAvailabilityManifest>(`/api/v3/research/replay-availability${suffix}`)
  },
  getRecomputeCapability: (params: { scope: ResearchScope; start_date: string; end_date: string; checkpoint?: string; portfolio_id?: number }) => {
    const query = new URLSearchParams({
      scope: params.scope,
      start_date: params.start_date,
      end_date: params.end_date,
      checkpoint: params.checkpoint || 'EOD',
    })
    if (params.portfolio_id) query.set('portfolio_id', String(params.portfolio_id))
    return request<RecomputeCapabilityManifest>(`/api/v3/research/recompute-capability?${query.toString()}`)
  },
  listBacktests: (portfolioId?: number) => request<BacktestRun[]>(`/api/v3/research/backtests${portfolioId ? `?portfolio_id=${portfolioId}` : ''}`),
  getBacktest: (id: number) => request<BacktestRun>(`/api/v3/research/backtests/${id}`),
  createBacktest: (payload: {
    scope: ResearchScope
    replay_mode: ReplayMode
    start_date: string
    end_date: string
    portfolio_id?: number | null
    horizons?: number[]
    experiment?: Record<string, unknown> | null
    random_seed?: number
    bootstrap_iterations?: number
  }) => request<BacktestRun>('/api/v3/research/backtests', { method: 'POST', body: payload }),
  cancelBacktest: (id: number) => request<BacktestRun>(`/api/v3/research/backtests/${id}/cancel`, { method: 'POST' }),
  listCalibrations: (portfolioId?: number) => request<CalibrationReport[]>(`/api/v3/research/calibrations${portfolioId ? `?portfolio_id=${portfolioId}` : ''}`),
  getCalibration: (id: number) => request<CalibrationReport>(`/api/v3/research/calibrations/${id}`),
  createCalibration: (payload: {
    backtest_run_id: number
    target_parameter: string
    parameter_grid?: Array<number | string>
    random_seed?: number
    bootstrap_iterations?: number
  }) => request<CalibrationReport>('/api/v3/research/calibrations', { method: 'POST', body: payload }),
  reconcileToday: (portfolioId: number) => request<Record<string, unknown>>(`/api/v3/portfolios/${portfolioId}/operations/reconcile-today`, { method: 'POST' }),
  listOperatingNotifications: (portfolioId?: number, unreadOnly = false) => request<OperatingNotificationList>(`/api/v3/notifications${portfolioId || unreadOnly ? `?${[
    portfolioId ? `portfolio_id=${portfolioId}` : '',
    unreadOnly ? 'unread_only=true' : '',
  ].filter(Boolean).join('&')}` : ''}`),
  markOperatingNotificationRead: (notificationId: string, portfolioId?: number) => request<Record<string, unknown>>(`/api/v3/notifications/${encodeURIComponent(notificationId)}/read${portfolioId ? `?portfolio_id=${portfolioId}` : ''}`, { method: 'POST' }),

  getGovernanceParameters: () => request<GovernanceRegistryResponse>('/api/v3/governance/parameters'),
  listParameterSets: () => request<ParameterSetListResponse>('/api/v3/governance/parameter-sets'),
  getActiveParameterSet: () => request<ParameterSetVersion>('/api/v3/governance/parameter-sets/active'),
  getParameterSet: (id: number) => request<ParameterSetVersion>(`/api/v3/governance/parameter-sets/${id}`),
  listGovernanceProposals: () => request<ProposalListResponse>('/api/v3/governance/proposals'),
  getGovernanceProposal: (id: number) => request<ParameterChangeProposal>(`/api/v3/governance/proposals/${id}`),
  createProposalFromCalibration: (payload: { calibration_report_id: number; proposed_value: unknown; reason?: string | null }) => request<ParameterChangeProposal>('/api/v3/governance/proposals/from-calibration', { method: 'POST', body: payload }),
  createManualProposal: (payload: { target_parameter_key: string; proposed_value: unknown; reason: string; risk_acknowledged?: boolean; risk_summary?: Record<string, unknown> | null }) => request<ParameterChangeProposal>('/api/v3/governance/proposals/manual', { method: 'POST', body: payload }),
  submitGovernanceProposal: (id: number) => request<ParameterChangeProposal>(`/api/v3/governance/proposals/${id}/submit`, { method: 'POST' }),
  approveGovernanceProposal: (id: number, review_comment?: string | null) => request<{ proposal: ParameterChangeProposal; version: ParameterSetVersion }>(`/api/v3/governance/proposals/${id}/approve`, { method: 'POST', body: { review_comment: review_comment ?? null } }),
  rejectGovernanceProposal: (id: number, review_comment?: string | null) => request<ParameterChangeProposal>(`/api/v3/governance/proposals/${id}/reject`, { method: 'POST', body: { review_comment: review_comment ?? null } }),
  validateParameterSet: (id: number) => request<ParameterSetVersion>(`/api/v3/governance/parameter-sets/${id}/validate`, { method: 'POST' }),
  activateParameterSet: (id: number, payload: { emergency_override?: boolean; reason?: string | null; expected_active_version_id?: number | null }) => request<ParameterSetVersion>(`/api/v3/governance/parameter-sets/${id}/activate`, { method: 'POST', body: payload }),
  createRollbackProposal: (id: number, reason: string) => request<ParameterChangeProposal>(`/api/v3/governance/parameter-sets/${id}/rollback-proposal`, { method: 'POST', body: { reason } }),
  listGovernanceEvents: () => request<GovernanceEventListResponse>('/api/v3/governance/events'),
  getGovernanceHealth: () => request<GovernanceHealth>('/api/v3/governance/health'),
  getSystemRelease: () => request<SystemRelease>('/api/v3/system/release'),
  getSystemHealth: () => request<SystemHealth>('/api/v3/system/health'),
  getSystemReadiness: () => request<SystemReadiness>('/api/v3/system/readiness'),
  getLiveValidationReadiness: () => request<LiveValidationReadiness>('/api/v3/system/live-validation-readiness'),
  getSystemRecovery: () => request<SystemRecoveryReport>('/api/v3/system/recovery'),
  listSystemBackups: () => request<SystemBackupList>('/api/v3/system/backups'),
  createSystemBackup: (reason: string = 'MANUAL') => request<SystemBackup>('/api/v3/system/backups', { method: 'POST', body: { reason } }),
  verifySystemBackup: (backupId: string) => request<Record<string, unknown>>(`/api/v3/system/backups/${encodeURIComponent(backupId)}/verify`, { method: 'POST' }),
  restoreDrill: (backupId: string) => request<Record<string, unknown>>(`/api/v3/system/backups/${encodeURIComponent(backupId)}/restore-drill`, { method: 'POST' }),
  createDiagnostics: () => request<SystemDiagnostics>('/api/v3/system/diagnostics', { method: 'POST' }),
  downloadDiagnostics: (bundleId: string) => requestBlob(`/api/v3/system/diagnostics/${encodeURIComponent(bundleId)}/download`),
  getHistoryCoverage: (params: { start_date?: string; end_date?: string; data_type?: string } = {}) => {
    const query = new URLSearchParams()
    if (params.start_date) query.set('start_date', params.start_date)
    if (params.end_date) query.set('end_date', params.end_date)
    if (params.data_type) query.set('data_type', params.data_type)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<HistoryCoverage>(`/api/v3/history/coverage${suffix}`)
  },
  getHistoryAvailability: () => request<{ items: Record<string, any> }>('/api/v3/history/availability'),
  listHistorySyncRuns: (dataType?: string) => request<{ runs: HistorySyncRun[] }>(
    `/api/v3/history/sync-runs${dataType ? `?data_type=${encodeURIComponent(dataType)}` : ''}`,
  ),
  runHistorySync: (payload: { data_type: string; start_date?: string; end_date?: string; market?: string; provider?: string }) =>
    request<HistorySyncRun>('/api/v3/history/sync', { method: 'POST', body: payload }),
}

async function requestBlob(path: string, retryAuth = true): Promise<Blob> {
  const generation = sessionGeneration
  const sessionId = localStorage.getItem(SESSION_KEY)
  const sessionCurrent = () => generation === sessionGeneration && sessionId === localStorage.getItem(SESSION_KEY)
  const headers: Record<string, string> = { 'X-Request-ID': requestIdValue() }; const token = getAccessToken(); if (token) headers.Authorization = `Bearer ${token}`
  const result = await fetchWithDeadline(path, { headers }, async (res) => res.ok
    ? { response: res, error: null, data: await res.blob() }
    : { response: res, error: await parseError(res), data: null })
  if (!sessionCurrent()) throw new ApiError('会话已切换，请求已取消。', { kind: 'cancelled' })
  const res = result.response
  if (res.status === 401 && retryAuth) {
    const refreshed = await withDeadline(() => refreshSession())
    if (!sessionCurrent()) throw new ApiError('请求已取消。', { kind: 'cancelled' })
    if (refreshed) return requestBlob(path, false)
    clearSession()
    window.dispatchEvent(new CustomEvent('advisor-auth-expired'))
  }
  if (result.error) throw result.error
  return result.data as Blob
}
