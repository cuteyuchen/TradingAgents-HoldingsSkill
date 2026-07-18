import type {
  AnalysisJob,
  AnalysisMode,
  AnalysisRunDetail,
  AnalysisRunSummary,
  HoldingUpload,
  ModelProfile,
  ModelProvider,
  NotificationChannel,
  ParsedHoldings,
  Portfolio,
  PortfolioSnapshot,
  Schedule,
  TokenPair,
  User,
} from './types'

const ACCESS_KEY = 'advisor_v2_access_token'
const REFRESH_KEY = 'advisor_v2_refresh_token'

export function getAccessToken(): string {
  return localStorage.getItem(ACCESS_KEY) || ''
}

export function getRefreshToken(): string {
  return localStorage.getItem(REFRESH_KEY) || ''
}

export function saveSession(tokens: TokenPair): void {
  localStorage.setItem(ACCESS_KEY, tokens.access_token)
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
}

export function clearSession(): void {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export function hasSession(): boolean {
  return Boolean(getAccessToken() || getRefreshToken())
}

interface RequestOptions {
  method?: string
  body?: unknown
  public?: boolean
  headers?: Record<string, string>
  retryAuth?: boolean
}

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
  } catch {
    return `${res.status} ${res.statusText}`
  }
}

let refreshPromise: Promise<boolean> | null = null

async function refreshSession(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false
  if (!refreshPromise) {
    refreshPromise = fetch('/api/v2/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
      .then(async (res) => {
        if (!res.ok) return false
        saveSession((await res.json()) as TokenPair)
        return true
      })
      .catch(() => false)
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers || {}) }
  const isForm = options.body instanceof FormData
  let body: BodyInit | undefined
  if (options.body !== undefined) {
    if (isForm) {
      body = options.body as FormData
    } else {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(options.body)
    }
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

export const api = {
  register: (payload: { email: string; username?: string; password: string }) =>
    request<TokenPair>('/api/v2/auth/register', { method: 'POST', body: payload, public: true }),
  login: (payload: { email: string; password: string; device_info?: string }) =>
    request<TokenPair>('/api/v2/auth/login', { method: 'POST', body: payload, public: true }),
  logout: (refreshToken = getRefreshToken()) =>
    request<void>('/api/v2/auth/logout', { method: 'POST', body: { refresh_token: refreshToken } }),
  me: () => request<User>('/api/v2/auth/me'),

  listProviders: () => request<ModelProvider[]>('/api/v2/model-settings/providers'),
  createProvider: (payload: Record<string, unknown>) =>
    request<ModelProvider>('/api/v2/model-settings/providers', { method: 'POST', body: payload }),
  updateProvider: (id: number, payload: Record<string, unknown>) =>
    request<ModelProvider>(`/api/v2/model-settings/providers/${id}`, { method: 'PATCH', body: payload }),
  deleteProvider: (id: number) =>
    request<void>(`/api/v2/model-settings/providers/${id}`, { method: 'DELETE' }),
  listProfiles: () => request<ModelProfile[]>('/api/v2/model-settings/profiles'),
  createProfile: (payload: Record<string, unknown>) =>
    request<ModelProfile>('/api/v2/model-settings/profiles', { method: 'POST', body: payload }),
  updateProfile: (id: number, payload: Record<string, unknown>) =>
    request<ModelProfile>(`/api/v2/model-settings/profiles/${id}`, { method: 'PATCH', body: payload }),
  deleteProfile: (id: number) =>
    request<void>(`/api/v2/model-settings/profiles/${id}`, { method: 'DELETE' }),
  testProfile: (id: number) =>
    request<{ status: string; message: string; latency_ms?: number; raw_excerpt?: string }>(
      `/api/v2/model-settings/profiles/${id}/test`,
      { method: 'POST' },
    ),

  listPortfolios: () => request<Portfolio[]>('/api/v2/portfolios'),
  createPortfolio: (payload: { name: string; market?: string; currency?: string; is_default?: boolean }) =>
    request<Portfolio>('/api/v2/portfolios', { method: 'POST', body: payload }),
  updatePortfolio: (id: number, payload: Record<string, unknown>) =>
    request<Portfolio>(`/api/v2/portfolios/${id}`, { method: 'PATCH', body: payload }),
  deletePortfolio: (id: number) => request<void>(`/api/v2/portfolios/${id}`, { method: 'DELETE' }),
  uploadHoldings: (portfolioId: number, file: File, parsed?: ParsedHoldings) => {
    const form = new FormData()
    form.append('screenshot', file)
    if (parsed) form.append('holdings_json', JSON.stringify(parsed))
    return request<HoldingUpload>(`/api/v2/portfolios/${portfolioId}/uploads`, { method: 'POST', body: form })
  },
  getUpload: (id: number) => request<HoldingUpload>(`/api/v2/uploads/${id}`),
  retryUploadParse: (id: number) => request<HoldingUpload>(`/api/v2/uploads/${id}/parse`, { method: 'POST' }),
  updateParsedHoldings: (id: number, parsed: ParsedHoldings) =>
    request<HoldingUpload>(`/api/v2/uploads/${id}/parsed-holdings`, { method: 'PATCH', body: { parsed } }),
  confirmUpload: (id: number) => request<PortfolioSnapshot>(`/api/v2/uploads/${id}/confirm`, { method: 'POST' }),
  getUploadImage: (id: number) => requestBlob(`/api/v2/uploads/${id}/image`),
  listSnapshots: (portfolioId: number) => request<PortfolioSnapshot[]>(`/api/v2/portfolios/${portfolioId}/snapshots`),
  getSnapshot: (id: number) => request<PortfolioSnapshot>(`/api/v2/snapshots/${id}`),

  createAnalysisJob: (snapshotId: number, mode: AnalysisMode, checkpoint?: string, notify = true) =>
    request<AnalysisJob>('/api/v2/analysis/jobs', {
      method: 'POST',
      body: { snapshot_id: snapshotId, mode, checkpoint, notify },
    }),
  getAnalysisJob: (id: number) => request<AnalysisJob>(`/api/v2/analysis/jobs/${id}`),
  cancelAnalysisJob: (id: number) => request<AnalysisJob>(`/api/v2/analysis/jobs/${id}/cancel`, { method: 'POST' }),
  retryAnalysisJob: (id: number) => request<AnalysisJob>(`/api/v2/analysis/jobs/${id}/retry`, { method: 'POST' }),
  listRuns: (portfolioId?: number) =>
    request<AnalysisRunSummary[]>(`/api/v2/analysis/runs${portfolioId ? `?portfolio_id=${portfolioId}` : ''}`),
  getRun: (id: number) => request<AnalysisRunDetail>(`/api/v2/analysis/runs/${id}`),
  compareRun: (id: number) => request<Record<string, unknown>>(`/api/v2/analysis/runs/${id}/comparison`),

  listSchedules: () => request<Schedule[]>('/api/v2/schedules'),
  createSchedule: (payload: Record<string, unknown>) => request<Schedule>('/api/v2/schedules', { method: 'POST', body: payload }),
  updateSchedule: (id: number, payload: Record<string, unknown>) =>
    request<Schedule>(`/api/v2/schedules/${id}`, { method: 'PATCH', body: payload }),
  deleteSchedule: (id: number) => request<void>(`/api/v2/schedules/${id}`, { method: 'DELETE' }),
  runScheduleNow: (id: number) => request<AnalysisJob>(`/api/v2/schedules/${id}/run-now`, { method: 'POST' }),

  listNotifications: () => request<NotificationChannel[]>('/api/v2/notifications'),
  createNotification: (payload: Record<string, unknown>) =>
    request<NotificationChannel>('/api/v2/notifications', { method: 'POST', body: payload }),
  updateNotification: (id: number, payload: Record<string, unknown>) =>
    request<NotificationChannel>(`/api/v2/notifications/${id}`, { method: 'PATCH', body: payload }),
  deleteNotification: (id: number) => request<void>(`/api/v2/notifications/${id}`, { method: 'DELETE' }),
  testNotification: (id: number) =>
    request<{ status: string; message: string }>(`/api/v2/notifications/${id}/test`, { method: 'POST' }),
}

async function requestBlob(path: string, retryAuth = true): Promise<Blob> {
  const headers: Record<string, string> = {}
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(path, { headers })
  if (res.status === 401 && retryAuth && (await refreshSession())) return requestBlob(path, false)
  if (!res.ok) throw new Error(await parseError(res))
  return res.blob()
}
