<script setup lang="ts">
import { computed } from 'vue'
import type { OrderBook } from '@/api/types'
import { DASH, formatPrice, formatShares, marketTrend } from './formatters'
import V3EmptyState from '../components/V3EmptyState.vue'
import V3ErrorState from '../components/V3ErrorState.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import type { ModuleError } from './useInstrumentDetail'

const props = defineProps<{
  orderBook: OrderBook | null
  loading?: boolean
  error: ModuleError | null
  supported: boolean
  prevClose: number | null
}>()

const emit = defineEmits<{ retry: [] }>()

/** Display order: 卖5..卖1 / 买1..买5. Copy + reverse for asks; do not mutate source. */
const askDisplay = computed(() => {
  const asks = props.orderBook?.asks || []
  return [...asks].reverse()
})

const bidDisplay = computed(() => props.orderBook?.bids || [])

function priceTone(price: number | null): 'up' | 'down' | 'flat' {
  if (price == null || props.prevClose == null) return 'flat'
  return marketTrend(price - props.prevClose)
}

const status = computed(() => props.orderBook?.status ?? null)
const derived = computed(() => props.orderBook?.derived === true)
const derivedFields = computed(() => props.orderBook?.derived_fields || [])

function derivedMark(field: 'order_ratio' | 'order_difference'): boolean {
  return derivedFields.value.includes(field)
}

const summaryRows = computed(() => {
  const b = props.orderBook
  return [
    { label: '委比', value: b?.order_ratio == null ? DASH : `${b.order_ratio.toFixed(2)}%`, derived: derivedMark('order_ratio') },
    { label: '委差', value: b?.order_difference == null ? DASH : formatShares(b.order_difference), derived: derivedMark('order_difference') },
    { label: '内盘', value: formatShares(b?.inner_volume), derived: false },
    { label: '外盘', value: formatShares(b?.outer_volume), derived: false },
    { label: '买盘总量', value: formatShares(b?.bid_volume_total), derived: false },
    { label: '卖盘总量', value: formatShares(b?.ask_volume_total), derived: false },
  ]
})

const unsupported = computed(() => !props.supported || status.value === 'unsupported')
const empty = computed(() => status.value === 'empty')
const unavailable = computed(() => status.value === 'unavailable')
</script>

<template>
  <div class="book" data-testid="instrument-order-book">
    <div class="book__header">
      <h3>盘口</h3>
      <V3StatusBadge v-if="orderBook?.status === 'stale'" label="可能过期" tone="warning" data-testid="book-stale" />
      <V3StatusBadge v-if="orderBook?.fallback" label="备用数据源" tone="info" data-testid="book-fallback" />
    </div>

    <V3EmptyState
      v-if="unsupported"
      title="指数当前不提供五档盘口"
      description="该类型标的或数据源不支持盘口，不会发起盘口请求。"
      data-testid="book-unsupported"
    />
    <V3EmptyState
      v-else-if="empty"
      title="当前没有盘口挂单"
      description="数据源返回了空盘口，可能处于集合竞价前或临时无挂单。"
      data-testid="book-empty"
    />
    <V3EmptyState
      v-else-if="unavailable && !orderBook"
      title="数据源当前无法提供盘口"
      description="请稍后重试，或查看数据质量说明。"
      data-testid="book-unavailable"
    />
    <V3ErrorState
      v-else-if="error"
      :title="error.title"
      :description="error.description"
      data-testid="book-error"
      @retry="emit('retry')"
    />
    <div v-else-if="loading && !orderBook" class="book__skeleton" data-testid="book-loading" aria-busy="true">
      <span v-for="i in 10" :key="i" class="book__sk-row" />
    </div>
    <template v-else-if="orderBook">
      <p class="book__unit">单位：股 · 价格颜色按昨收比较（不是简单买红卖绿）</p>
      <div class="book__layout">
        <table class="book__table" data-testid="book-levels">
          <thead>
            <tr>
              <th scope="col">档位</th>
              <th scope="col">价格</th>
              <th scope="col">数量（股）</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in askDisplay" :key="`ask-${row.level}`" :data-testid="`book-ask-${row.level}`">
              <td>卖{{ 6 - row.level }}</td>
              <td class="v3-number" :class="`trend-${priceTone(row.price)}`">{{ formatPrice(row.price) }}</td>
              <td class="v3-number">{{ formatShares(row.volume) }}</td>
            </tr>
            <tr class="book__mid" aria-hidden="true"><td colspan="3" /></tr>
            <tr v-for="row in bidDisplay" :key="`bid-${row.level}`" :data-testid="`book-bid-${row.level}`">
              <td>买{{ row.level }}</td>
              <td class="v3-number" :class="`trend-${priceTone(row.price)}`">{{ formatPrice(row.price) }}</td>
              <td class="v3-number">{{ formatShares(row.volume) }}</td>
            </tr>
          </tbody>
        </table>
        <dl class="book__summary">
          <div v-for="row in summaryRows" :key="row.label">
            <dt>{{ row.label }}</dt>
            <dd class="v3-number">
              {{ row.value }}
              <span v-if="row.derived" class="book__derived" data-testid="book-derived-mark">推导值</span>
            </dd>
          </div>
        </dl>
      </div>
      <p v-if="derived" class="book__note" data-testid="book-derived-note">
        委比/委差可由盘口总量推导，已标记 derived_fields。
      </p>
    </template>
  </div>
</template>

<style scoped>
.book__header {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
  margin-bottom: var(--v3-space-2);
}
.book__header h3 {
  margin: 0;
  font-size: var(--v3-font-size-lg);
  font-weight: 700;
}
.book__unit {
  margin: 0 0 var(--v3-space-3);
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.book__layout {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(180px, 0.8fr);
  gap: var(--v3-space-4);
}
.book__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--v3-font-size-sm);
}
.book__table th,
.book__table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--v3-border-subtle);
  text-align: left;
}
.book__table th {
  color: var(--v3-text-muted);
  font-weight: 600;
  font-size: var(--v3-font-size-xs);
}
.book__mid td {
  border-bottom: 1px dashed var(--v3-border);
  height: 8px;
  padding: 0;
}
.book__summary {
  margin: 0;
  display: grid;
  gap: var(--v3-space-2);
}
.book__summary > div {
  display: flex;
  justify-content: space-between;
  gap: var(--v3-space-2);
  padding: 6px 0;
  border-bottom: 1px solid var(--v3-border-subtle);
  font-size: var(--v3-font-size-sm);
}
.book__summary dt {
  color: var(--v3-text-muted);
}
.book__summary dd {
  margin: 0;
  font-weight: 600;
  color: var(--v3-text);
}
.book__derived {
  margin-left: 4px;
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface-muted);
  color: var(--v3-text-muted);
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 600;
}
.book__note {
  margin: var(--v3-space-3) 0 0;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.book__skeleton {
  display: grid;
  gap: 8px;
}
.book__sk-row {
  height: 22px;
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface-muted);
}
.trend-up { color: var(--v3-market-up); }
.trend-down { color: var(--v3-market-down); }
.trend-flat { color: var(--v3-market-flat); }
@media (max-width: 640px) {
  .book__layout { grid-template-columns: 1fr; }
}
</style>
