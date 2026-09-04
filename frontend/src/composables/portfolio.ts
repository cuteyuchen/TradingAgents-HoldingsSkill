import { computed, ref } from 'vue'
import { api, type ApiError } from '../api'
import type { Portfolio } from '../api/types'

const STORAGE_KEY = 'advisor_selected_portfolio_id'
const portfolios = ref<Portfolio[]>([])
const selectedPortfolioId = ref<number | null>(readStoredId())
const loading = ref(false)
const error = ref<ApiError | null>(null)
let inFlight: Promise<Portfolio[]> | null = null
let cacheGeneration = 0

function readStoredId(): number | null {
  if (typeof window === 'undefined') return null
  const raw = Number(window.localStorage.getItem(STORAGE_KEY))
  return Number.isInteger(raw) && raw > 0 ? raw : null
}

function persistSelection(id: number | null) {
  if (typeof window === 'undefined') return
  if (id) window.localStorage.setItem(STORAGE_KEY, String(id))
  else window.localStorage.removeItem(STORAGE_KEY)
  window.dispatchEvent(new CustomEvent('advisor-portfolio-changed', { detail: { portfolioId: id } }))
}

export function setSelectedPortfolio(id: number | null): void {
  const next = id && portfolios.value.some((item) => item.id === id) ? id : null
  selectedPortfolioId.value = next
  persistSelection(next)
}

export function clearPortfolioContext(): void {
  cacheGeneration += 1
  portfolios.value = []
  selectedPortfolioId.value = null
  error.value = null
  loading.value = false
  inFlight = null
  persistSelection(null)
}

export async function loadPortfolios(force = false): Promise<Portfolio[]> {
  if (!force && portfolios.value.length) return portfolios.value
  if (inFlight) {
    if (!force) return inFlight
    // A forced reload must observe facts written after the older request began.
    const pending = inFlight
    await pending.catch(() => undefined)
  }
  loading.value = true
  error.value = null
  const generation = cacheGeneration
  const request = api.listPortfolios()
    .then((rows) => {
      if (generation !== cacheGeneration) return []
      portfolios.value = rows
      const valid = rows.some((item) => item.id === selectedPortfolioId.value)
      if (!valid) {
        const fallback = rows.find((item) => item.is_default)?.id || rows[0]?.id || null
        selectedPortfolioId.value = fallback
        persistSelection(fallback)
      }
      return rows
    })
    .catch((reason: unknown) => {
      error.value = reason as ApiError
      throw reason
    })
  inFlight = request
  try {
    return await request
  } finally {
    if (inFlight === request) {
      loading.value = false
      inFlight = null
    }
  }
}

export function usePortfolioContext() {
  return {
    portfolios,
    selectedPortfolioId,
    selectedPortfolio: computed(() => portfolios.value.find((item) => item.id === selectedPortfolioId.value) || null),
    loading,
    error,
    loadPortfolios,
    setSelectedPortfolio,
  }
}
