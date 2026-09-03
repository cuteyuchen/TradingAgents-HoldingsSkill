import type {
  AnalysisJob,
  AnalysisMode,
  AnalysisRunDetail,
  AnalysisRunSummary,
  BacktestRun,
  CalibrationReport,
  DailyDashboard,
  FuyaoContribution,
  FuyaoMarketBrief,
  FuyaoSecurityContext,
  FuyaoStatus,
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
  ParameterChangeProposal,
  ParameterSetListResponse,
  ParameterSetVersion,
  ParsedHoldings,
  ProposalListResponse,
  Portfolio,
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
  User,
} from './types'

const ACCESS_KEY = 'advisor_v2_access_token'
const REFRESH_KEY = 'advisor_v2_refresh_token'

export type ApiErrorKind = 'auth' | 'forbidden' | 'not_found' | 'conflict' | 'validation' | 'server' | 'network' | 'timeout' | 'unknown'

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
export function saveSession(tokens: TokenPair): void {
  localStorage.setItem(ACCESS_KEY, tokens.access_token)
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
}
export function clearSession(): void { localStorage.removeItem(ACCESS_KEY); localStorage.removeItem(REFRESH_KEY) }
export function hasSession(): boolean { return Boolean(getAccessToken() || getRefreshToken()) }

interface RequestOptions { method?: string; body?: unknown; public?: boolean; headers?: Record<string, string>; retryAuth?: boolean; timeoutMs?: number }

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

let refreshPromise: Promise<boolean> | null = null
async function refreshSession(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false
  if (!refreshPromise) {
    refreshPromise = fetch('/api/v2/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': requestIdValue() },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).then(async (res) => {
      if (!res.ok) return false
      saveSession((await res.json()) as TokenPair)
      return true
    }).catch(() => false).finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
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
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs || 30_000)
  let res: Response
  try {
    res = await fetch(path, { method: options.method || 'GET', headers, body, signal: controller.signal })
  } catch (cause) {
    const timedOut = cause instanceof DOMException && cause.name === 'AbortError'
    throw new ApiError(
      timedOut ? '后端响应超时，请稍后重试。' : '无法连接后端，请确认服务已启动。',
      { kind: timedOut ? 'timeout' : 'network', cause },
    )
  } finally {
    window.clearTimeout(timeout)
  }
  if (res.status === 401 && !options.public && options.retryAuth !== false) {
    const refreshed = await refreshSession()
    if (refreshed) return request<T>(path, { ...options, retryAuth: false })
    clearSession()
    window.dispatchEvent(new CustomEvent('advisor-auth-expired'))
  }
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  try {
    return (await res.json()) as T
  } catch (cause) {
    throw new ApiError('后端返回了无法解析的数据。', { kind: 'server', requestId: requestIdHeader(res), cause })
  }
}

async function registerAndLogin(payload: { email: string; username?: string; password: string }): Promise<TokenPair> {
  await request<User>('/api/v2/auth/register', { method: 'POST', body: payload, public: true })
  return request<TokenPair>('/api/v2/auth/login', {
    method: 'POST', body: { email: payload.email, password: payload.password, device_info: navigator.userAgent }, public: true,
  })
}

export const api = {
  register: registerAndLogin,
  login: (payload: { email: string; password: string; device_info?: string }) => request<TokenPair>('/api/v2/auth/login', { method: 'POST', body: payload, public: true }),
  logout: (refreshToken = getRefreshToken()) => request<void>('/api/v2/auth/logout', { method: 'POST', body: { refresh_token: refreshToken } }),
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
  resolveHolding: (holding: Holding) => request<Holding>('/api/v2/holdings/resolve', { method: 'POST', body: holding }),
  retryUploadParse: (id: number) => request<HoldingUpload>(`/api/v2/uploads/${id}/parse`, { method: 'POST' }),
  updateParsedHoldings: (id: number, parsed: ParsedHoldings) => request<HoldingUpload>(`/api/v2/uploads/${id}/parsed-holdings`, { method: 'PATCH', body: { parsed } }),
  confirmUpload: (id: number) => request<PortfolioSnapshot>(`/api/v2/uploads/${id}/confirm`, { method: 'POST' }),
  getUploadImage: (id: number) => requestBlob(`/api/v2/uploads/${id}/image`),
  listSnapshots: (portfolioId: number) => request<PortfolioSnapshot[]>(`/api/v2/portfolios/${portfolioId}/snapshots`),
  getSnapshot: (id: number) => request<PortfolioSnapshot>(`/api/v2/snapshots/${id}`),

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

  getDashboardToday: (portfolioId: number) => request<DailyDashboard>(`/api/v3/portfolios/${portfolioId}/dashboard/today`),
  getDashboardTimeline: (portfolioId: number) => request<DashboardTimeline>(`/api/v3/portfolios/${portfolioId}/dashboard/timeline`),
  getDashboardHealth: (portfolioId: number) => request<DashboardHealth>(`/api/v3/portfolios/${portfolioId}/dashboard/health`),
  getDashboardDiagnostics: (portfolioId: number) => request<DashboardDiagnostics>(`/api/v3/portfolios/${portfolioId}/dashboard/diagnostics`),
  getFuyaoStatus: (probe = false) => request<FuyaoStatus>(`/api/v3/fuyao/status${probe ? '?probe=true' : ''}`),
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
  const headers: Record<string, string> = { 'X-Request-ID': requestIdValue() }; const token = getAccessToken(); if (token) headers.Authorization = `Bearer ${token}`
  let res: Response
  try {
    res = await fetch(path, { headers })
  } catch (cause) {
    throw new ApiError('无法连接后端，请确认服务已启动。', { kind: 'network', cause })
  }
  if (res.status === 401 && retryAuth) {
    if (await refreshSession()) return requestBlob(path, false)
    clearSession()
    window.dispatchEvent(new CustomEvent('advisor-auth-expired'))
  }
  if (!res.ok) throw await parseError(res)
  return res.blob()
}
