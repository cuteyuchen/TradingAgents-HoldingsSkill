// Thin API client. Base path is proxied to the backend in dev; in prod both
// are served from the same origin via docker-compose.

import type {
  BenchmarkPrice,
  Candidate,
  HealthStatus,
  HoldingTimeline,
  RunDetail,
  RunSummary,
  WatchlistItem,
} from './types'

const TOKEN_KEY = 'advisor_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t)
}

interface RequestOptions {
  method?: string
  headers?: Record<string, string>
  body?: string
  public?: boolean
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers || {}) }
  if (options.body) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (token && !options.public) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(path, {
    method: options.method || 'GET',
    headers,
    body: options.body,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText} ${text}`)
  }
  if (res.status === 204) return null as T
  return (await res.json()) as T
}

export const api = {
  // Runs
  listRuns: (params = ''): Promise<RunSummary[]> => request(`/api/v1/runs${params}`),
  getRun: (id: number | string): Promise<RunDetail> => request(`/api/v1/runs/${id}`),

  // Portfolio / holdings
  currentPortfolio: (): Promise<{ run_id: number | null; timestamp: string | null; holdings: import('./types').Holding[] }> =>
    request('/api/v1/portfolio/current'),
  holdingTimeline: (code: string, limit = 5): Promise<HoldingTimeline> =>
    request(`/api/v1/holdings/${code}/timeline?limit=${limit}`),

  // Candidates
  listCandidates: (status = ''): Promise<Candidate[]> =>
    request(`/api/v1/candidates${status ? `?status=${encodeURIComponent(status)}` : ''}`),

  // Benchmark
  benchmark: (from = '', to = ''): Promise<BenchmarkPrice[]> =>
    request(`/api/v1/benchmark/hs300?from=${from}&to=${to}`),

  // Watchlist
  listWatchlist: (): Promise<WatchlistItem[]> => request('/api/v1/watchlist'),
  addWatchlist: (item: WatchlistItem): Promise<WatchlistItem> =>
    request('/api/v1/watchlist', { method: 'POST', body: JSON.stringify(item) }),
  removeWatchlist: (code: string): Promise<void> =>
    request(`/api/v1/watchlist/${code}`, { method: 'DELETE' }),

  // Health
  health: (): Promise<HealthStatus[]> => request('/api/v1/health'),
}
