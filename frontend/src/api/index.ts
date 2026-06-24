// Thin API client. Base path is proxied to the backend in dev; in prod both
// are served from the same origin via docker-compose.

import type {
  ArchiveDetail,
  ArchiveSummary,
} from './types'

const TOKEN_KEY = 'advisor_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
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
    if (res.status === 401 || res.status === 403) {
      throw new Error('密码错误或已失效，请重新输入访问密码')
    }
    throw new Error(`${res.status} ${res.statusText} ${text}`)
  }
  if (res.status === 204) return null as T
  return (await res.json()) as T
}

export const api = {
  verifyToken: (): Promise<{ status: string }> => request('/api/v1/auth/verify'),

  // /*********************** 归档接口 *********************/
  listArchives: (): Promise<ArchiveSummary[]> => request('/api/v1/archives'),
  getArchive: (id: number | string): Promise<ArchiveDetail> => request(`/api/v1/archives/${id}`),
  deleteArchive: (id: number | string): Promise<void> => request(`/api/v1/archives/${id}`, { method: 'DELETE' }),
}
