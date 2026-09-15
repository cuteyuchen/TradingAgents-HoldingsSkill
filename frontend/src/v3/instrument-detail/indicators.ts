/**
 * Deterministic pure indicator functions for InstrumentDetail.
 * Do not mutate input bars. Empty/insufficient input is safe. No NaN/Infinity.
 */

export interface OhlcBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
  turnover: number | null
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** SMA over close prices. Incomplete windows return null. */
export function calculateSMA(values: number[], period: number): (number | null)[] {
  const result: (number | null)[] = new Array(values.length).fill(null)
  if (period <= 0) return result
  let sum = 0
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i]
    if (!isFiniteNumber(v)) {
      sum = 0
      continue
    }
    sum += v
    if (i >= period) {
      const prev = values[i - period]
      if (isFiniteNumber(prev)) sum -= prev
    }
    if (i >= period - 1) {
      result[i] = sum / period
    }
  }
  return result
}

/** EMA seed = SMA of first `period` values; subsequent values use EMA formula. */
export function calculateEMA(values: number[], period: number): (number | null)[] {
  const result: (number | null)[] = new Array(values.length).fill(null)
  if (period <= 0 || values.length < period) return result
  const k = 2 / (period + 1)
  let sum = 0
  let seeded = false
  let ema = 0
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i]
    if (!isFiniteNumber(v)) continue
    if (!seeded) {
      sum += v
      if (i === period - 1) {
        ema = sum / period
        result[i] = ema
        seeded = true
      }
      continue
    }
    ema = v * k + ema * (1 - k)
    if (isFiniteNumber(ema)) result[i] = ema
    else result[i] = null
  }
  return result
}

export interface MacdResult {
  dif: (number | null)[]
  dea: (number | null)[]
  hist: (number | null)[]
  ema12: (number | null)[]
  ema26: (number | null)[]
}

/** MACD(12,26,9): DIF=EMA12-EMA26, DEA=EMA9(DIF), HIST=2*(DIF-DEA). */
export function calculateMACD(closes: number[]): MacdResult {
  const ema12 = calculateEMA(closes, 12)
  const ema26 = calculateEMA(closes, 26)
  const dif: (number | null)[] = new Array(closes.length).fill(null)
  for (let i = 0; i < closes.length; i += 1) {
    const a = ema12[i]
    const b = ema26[i]
    if (isFiniteNumber(a) && isFiniteNumber(b)) {
      dif[i] = a - b
    }
  }
  const dea: (number | null)[] = new Array(closes.length).fill(null)
  const validDif: number[] = []
  const difIndex: number[] = []
  for (let i = 0; i < dif.length; i += 1) {
    if (isFiniteNumber(dif[i])) {
      validDif.push(dif[i] as number)
      difIndex.push(i)
    }
  }
  const deaOnValid = calculateEMA(validDif, 9)
  for (let k = 0; k < difIndex.length; k += 1) {
    dea[difIndex[k]] = deaOnValid[k]
  }
  const hist: (number | null)[] = new Array(closes.length).fill(null)
  for (let i = 0; i < closes.length; i += 1) {
    const d = dif[i]
    const e = dea[i]
    if (isFiniteNumber(d) && isFiniteNumber(e)) {
      hist[i] = 2 * (d - e)
    }
  }
  return { dif, dea, hist, ema12, ema26 }
}

/** RSI with Wilder smoothing. First RSI appears after `period` bars. */
export function calculateRSI(closes: number[], period = 14): (number | null)[] {
  const result: (number | null)[] = new Array(closes.length).fill(null)
  if (closes.length <= period) return result
  let avgGain = 0
  let avgLoss = 0
  for (let i = 1; i <= period; i += 1) {
    const change = closes[i] - closes[i - 1]
    if (!isFiniteNumber(change)) return result
    if (change >= 0) avgGain += change
    else avgLoss += Math.abs(change)
  }
  avgGain /= period
  avgLoss /= period
  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  for (let i = period + 1; i < closes.length; i += 1) {
    const change = closes[i] - closes[i - 1]
    if (!isFiniteNumber(change)) {
      result[i] = null
      continue
    }
    const gain = change > 0 ? change : 0
    const loss = change < 0 ? Math.abs(change) : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    if (avgLoss === 0) result[i] = 100
    else {
      const rsi = 100 - 100 / (1 + avgGain / avgLoss)
      result[i] = isFiniteNumber(rsi) ? rsi : null
    }
  }
  return result
}

export function closesOf(bars: OhlcBar[]): number[] {
  return bars.map((b) => (isFiniteNumber(b.close) ? b.close : Number.NaN))
}

export function volumesOf(bars: OhlcBar[]): (number | null)[] {
  return bars.map((b) => (isFiniteNumber(b.volume) ? b.volume : null))
}
