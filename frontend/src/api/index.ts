import type {
  AnalysisJob,
  AnalysisMode,
  AnalysisRunDetail,
  AnalysisRunSummary,
  BacktestRun,
  CalibrationReport,
  DailyDashboard,
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
  ParameterChangeProposal,
  ParameterSetListResponse,
  ParameterSetVersion,
  ParsedHoldings,
  ProposalListResponse,
  Portfolio,
  PortfolioSnapshot,
  ReplayAvailabilityManifest,
  ResearchScope,
  ReplayMode,
  Schedule,
  TokenPair,
  User,
} from './types'

const ACCESS_KEY = 'advisor_v2_access_token'
const REFRESH_KEY = 'advisor_v2_refresh_token'

export function getAccessToken(): string { return localStorage.getItem(ACCESS_KEY) || '' }
export function getRefreshToken(): string { return localStorage.getItem(REFRESH_KEY) || '' }
export function saveSession(tokens: TokenPair): void {
  localStorage.setItem(ACCESS_KEY, tokens.access_token)
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
}
export function clearSession(): void { localStorage.removeItem(ACCESS_KEY); localStorage.removeItem(REFRESH_KEY) }
export function hasSession(): boolean { return Boolean(getAccessToken() || getRefreshToken()) }

interface RequestOptions { method?: string; body?: unknown; public?: boolean; headers?: Record<string, string>; retryAuth?: boolean }

async function parseError(res: Response): Promise<string> {
  try {
    const payload = await res.json()
    const detail = payload?.detail
    if (typeof detail === 'string') return detail
    if (detail?.message) {
      const errors = Array.isArray(detail.errors) ? `：${detail.errors.join('；')}` : ''
      return `${detail.message}${errors}`
    }
    return JSON.stringify(payload)
  } catch { return `${res.status} ${res.statusText}` }
}

let refreshPromise: Promise<boolean> | null = null
async function refreshSession(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false
  if (!refreshPromise) {
    refreshPromise = fetch('/api/v2/auth/refresh', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: refreshToken }),
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
  const isForm = options.body instanceof FormData
  let body: BodyInit | undefined
  if (options.body !== undefined) {
    if (isForm) body = options.body as FormData
    else { headers['Content-Type'] = 'application/json'; body = JSON.stringify(options.body) }
  }
  const token = getAccessToken()
  if (token && !options.public) headers.Authorization = `Bearer ${token}`
  const res = await fetch(path, { method: options.method || 'GET', headers, body })
  if (res.status === 401 && !options.public && options.retryAuth !== false) {
    const refreshed = await refreshSession()
    if (refreshed) return request<T>(path, { ...options, retryAuth: false })
    clearSession()
    window.dispatchEvent(new CustomEvent('advisor-auth-expired'))
  }
  if (!res.ok) throw new Error(await parseError(res))
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
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
  getReplayAvailability: (params: { start_date?: string; end_date?: string; portfolio_id?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.start_date) query.set('start_date', params.start_date)
    if (params.end_date) query.set('end_date', params.end_date)
    if (params.portfolio_id) query.set('portfolio_id', String(params.portfolio_id))
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<ReplayAvailabilityManifest>(`/api/v3/research/replay-availability${suffix}`)
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
}

async function requestBlob(path: string, retryAuth = true): Promise<Blob> {
  const headers: Record<string, string> = {}; const token = getAccessToken(); if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(path, { headers })
  if (res.status === 401 && retryAuth && (await refreshSession())) return requestBlob(path, false)
  if (!res.ok) throw new Error(await parseError(res))
  return res.blob()
}
