/** 统一数值/时间/状态格式化：null/NaN/Infinity → —，0 保留真实 0 */

export const DASH = '—'

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function formatPrice(value: number | null | undefined, digits = 2): string {
  if (!isFiniteNumber(value)) return DASH
  return value.toFixed(digits)
}

export function formatSignedNumber(value: number | null | undefined, digits = 2): string {
  if (!isFiniteNumber(value)) return DASH
  const formatted = value.toFixed(digits)
  return value > 0 ? `+${formatted}` : formatted
}

export function formatPercentPoint(value: number | null | undefined, digits = 2): string {
  if (!isFiniteNumber(value)) return DASH
  const formatted = value.toFixed(digits)
  return `${value > 0 ? '+' : ''}${formatted}%`
}

/** Volume contract is shares. UI may present 万股 / 亿股. */
export function formatVolumeShares(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return DASH
  if (value === 0) return '0'
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)}亿股`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)}万股`
  return `${formatInteger(value)}股`
}

/** Order book level volume: keep shares as integer with unit. */
export function formatShares(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return DASH
  if (value === 0) return '0'
  return `${formatInteger(value)}`
}

export function formatMoneyCNY(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return DASH
  if (value === 0) return '0'
  const abs = Math.abs(value)
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  const unsigned = abs >= 1e8
    ? `${(abs / 1e8).toFixed(2)}亿`
    : abs >= 1e4
      ? `${(abs / 1e4).toFixed(2)}万`
      : abs.toFixed(2)
  return `${sign}${unsigned}`
}

export function formatMoneyPlainCNY(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return DASH
  if (value === 0) return '0'
  return formatMoneyCNY(value).replace(/^[+-]/, '')
}

export function formatInteger(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return DASH
  return String(Math.trunc(value))
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  if (typeof value === 'number') {
    const d = new Date(value)
    return Number.isNaN(d.getTime()) ? null : d
  }
  const text = String(value)
  // Daily bar dates are YYYY-MM-DD; keep as calendar date without timezone shift.
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [y, m, d] = text.split('-').map(Number)
    return new Date(y, m - 1, d)
  }
  const parsed = new Date(text)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function formatDate(value: string | number | Date | null | undefined): string {
  const d = toDate(value)
  if (!d) return DASH
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

export function formatDateTime(value: string | number | Date | null | undefined): string {
  const d = toDate(value)
  if (!d) return DASH
  return `${formatDate(d)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

export function formatTimeOnly(value: string | number | Date | null | undefined): string {
  const d = toDate(value)
  if (!d) return DASH
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

export function formatDataBasis(value: string | null | undefined): string {
  switch (value) {
    case 'live':
      return '盘中'
    case 'session_close':
      return '当日收盘'
    case 'previous_session_close':
      return '上一交易日收盘'
    default:
      return DASH
  }
}

export function formatQuality(value: string | null | undefined): string {
  if (!value) return DASH
  return value.toUpperCase()
}

export function formatMarketStatus(status: string | null | undefined): string {
  switch (status) {
    case 'available':
      return '可用'
    case 'degraded':
      return '降级'
    case 'stale':
      return '可能过期'
    case 'unavailable':
      return '不可用'
    case 'unsupported':
      return '不支持'
    case 'empty':
      return '空'
    default:
      return DASH
  }
}

/** A-share direction: >0 up (red), <0 down (green), 0/invalid flat. */
export function marketTrend(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (!isFiniteNumber(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

export function trendClass(value: number | null | undefined): string {
  const trend = marketTrend(value)
  if (trend === 'up') return 'v3-market-up'
  if (trend === 'down') return 'v3-market-down'
  return 'v3-market-flat'
}

export function formatExchangeLabel(exchange: string | null | undefined): string {
  switch (exchange) {
    case 'SSE':
      return '上交所'
    case 'SZSE':
      return '深交所'
    case 'BSE':
      return '北交所'
    default:
      return exchange || DASH
  }
}

export function formatInstrumentType(type: string | null | undefined): string {
  switch (type) {
    case 'STOCK':
      return '股票'
    case 'ETF':
      return 'ETF'
    case 'INDEX':
      return '指数'
    default:
      return type || DASH
  }
}

export function formatBarInterval(interval: string | null | undefined): string {
  switch (interval) {
    case '1d':
      return '日K'
    case '1w':
      return '周K'
    case '1M':
      return '月K'
    default:
      return interval || DASH
  }
}

export function formatAdjustment(adjustment: string | null | undefined): string {
  switch (adjustment) {
    case 'forward':
      return '前复权'
    case 'none':
      return '不复权'
    case 'backward':
      return '后复权'
    default:
      return adjustment || DASH
  }
}

export function formatSessionKind(session: string | null | undefined): string {
  switch (session) {
    case 'PRE_OPEN':
      return '开盘前'
    case 'OPEN_AUCTION':
      return '开盘集合竞价'
    case 'MORNING':
      return '上午连续竞价'
    case 'LUNCH_BREAK':
      return '午间休市'
    case 'AFTERNOON':
      return '下午连续竞价'
    case 'CLOSE_AUCTION':
      return '收盘集合竞价'
    case 'CLOSED':
      return '已收盘'
    case 'NON_TRADING_DAY':
      return '非交易日'
    case 'DATA_ABNORMAL':
      return '数据异常'
    default:
      return DASH
  }
}
