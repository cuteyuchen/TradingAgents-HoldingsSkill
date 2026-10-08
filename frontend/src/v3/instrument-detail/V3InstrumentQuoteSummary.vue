<script setup lang="ts">
import { computed } from 'vue'
import type { InstrumentQuote } from '@/api/types'
import {
  DASH,
  formatMoneyCNY,
  formatPercentPoint,
  formatPrice,
  formatVolumeShares,
  marketTrend,
} from './formatters'

const props = defineProps<{
  quote: InstrumentQuote | null
  loading?: boolean
}>()

const last = computed(() => props.quote?.last ?? null)
const change = computed(() => props.quote?.change ?? null)
const changePct = computed(() => props.quote?.change_pct ?? null)
const trend = computed(() => marketTrend(changePct.value))

const fields = computed(() => {
  const q = props.quote
  return [
    { key: 'open', label: '今开', value: formatPrice(q?.open), trend: q?.open != null && q?.prev_close != null ? marketTrend(q.open - q.prev_close) : null },
    { key: 'high', label: '最高', value: formatPrice(q?.high), trend: q?.high != null && q?.prev_close != null ? marketTrend(q.high - q.prev_close) : null },
    { key: 'low', label: '最低', value: formatPrice(q?.low), trend: q?.low != null && q?.prev_close != null ? marketTrend(q.low - q.prev_close) : null },
    { key: 'prev_close', label: '昨收', value: formatPrice(q?.prev_close), trend: null },
    { key: 'volume', label: '成交量', value: formatVolumeShares(q?.volume), trend: null },
    { key: 'turnover', label: '成交额', value: formatMoneyCNY(q?.turnover), trend: null },
    { key: 'amplitude', label: '振幅', value: formatPercentPoint(q?.amplitude_pct), trend: null },
    { key: 'turnover_rate', label: '换手率', value: formatPercentPoint(q?.turnover_rate), trend: null },
  ]
})

function trendClass(t: 'up' | 'down' | 'flat' | null): string {
  if (t === 'up') return 'trend-up'
  if (t === 'down') return 'trend-down'
  return 'trend-flat'
}
</script>

<template>
  <div class="quote-summary" data-testid="instrument-quote-summary">
    <div v-if="loading && !quote" class="quote-summary__skeleton">
      <span class="skeleton-line w-32" />
      <span class="skeleton-line w-20" />
    </div>
    <template v-else>
      <div class="quote-summary__primary">
        <div class="quote-summary__price">
          <span
            class="quote-summary__last v3-number"
            :class="trendClass(trend)"
            data-testid="quote-last"
          >{{ formatPrice(last) }}</span>
          <span
            class="quote-summary__change v3-number"
            :class="trendClass(trend)"
            data-testid="quote-change"
          >{{ change === null ? DASH : (change > 0 ? '+' : '') + change.toFixed(2) }}</span>
          <span
            class="quote-summary__pct v3-number"
            :class="trendClass(trend)"
            data-testid="quote-change-pct"
          >{{ formatPercentPoint(changePct) }}</span>
        </div>
        <p class="quote-summary__hint">红涨绿跌 · 单位：价格 CNY / 量 股 · 金额 CNY</p>
      </div>
      <dl class="quote-summary__grid">
        <div v-for="field in fields" :key="field.key" class="quote-summary__cell">
          <dt>{{ field.label }}</dt>
          <dd
            class="v3-number"
            :class="field.trend ? trendClass(field.trend) : ''"
            :data-testid="`quote-${field.key}`"
          >{{ field.value }}</dd>
        </div>
      </dl>
    </template>
  </div>
</template>

<style scoped>
.quote-summary {
  display: flex;
  flex-direction: column;
  gap: var(--v3-space-4);
}
.quote-summary__primary {
  display: flex;
  flex-direction: column;
  gap: var(--v3-space-1);
}
.quote-summary__price {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--v3-space-3);
}
.quote-summary__last {
  font-size: var(--v3-font-size-3xl);
  font-weight: 700;
  line-height: 1.1;
}
.quote-summary__change,
.quote-summary__pct {
  font-size: var(--v3-font-size-xl);
  font-weight: 600;
}
.quote-summary__hint {
  margin: 0;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.quote-summary__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: var(--v3-space-3);
  margin: 0;
}
.quote-summary__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.quote-summary__cell dt {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.quote-summary__cell dd {
  margin: 0;
  color: var(--v3-text);
  font-size: var(--v3-font-size-lg);
  font-weight: 600;
}
.trend-up { color: var(--v3-market-up); }
.trend-down { color: var(--v3-market-down); }
.trend-flat { color: var(--v3-market-flat); }
.skeleton-line {
  display: block;
  height: 18px;
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface-muted);
}
.w-32 { width: 128px; height: 32px; }
.w-20 { width: 80px; }
</style>
