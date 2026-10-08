<script setup lang="ts">
import { computed } from 'vue'
import type { CapitalFlow, InstrumentQuote, MarketDataProvenance, OrderBook } from '@/api/types'
import { DASH, formatDataBasis, formatDateTime, formatQuality } from './formatters'

const props = defineProps<{
  quote: InstrumentQuote | null
  orderBook: OrderBook | null
  capitalFlow: CapitalFlow | null
  metadataProvenance: MarketDataProvenance | null
}>()

interface ProvenanceRow {
  module: string
  provider: string
  source: string
  observedAt: string
  fetchedAt: string
  tradingDate: string
  dataBasis: string
  quality: string
  status: string
  flags: string
  errorCode: string
}

function project(module: string, p: MarketDataProvenance | null): ProvenanceRow {
  return {
    module,
    provider: p?.fallback ? `${p.provider || DASH}（备用）` : p?.provider || DASH,
    source: p?.source || DASH,
    observedAt: p?.observed_at ? formatDateTime(p.observed_at) : DASH,
    fetchedAt: p?.fetched_at ? formatDateTime(p.fetched_at) : DASH,
    tradingDate: p?.trading_date || DASH,
    dataBasis: formatDataBasis(p?.data_basis),
    quality: formatQuality(p?.quality),
    status: p?.status || DASH,
    flags: p?.quality_flags?.length ? p.quality_flags.join(', ') : '—',
    errorCode: p?.error_code || '—',
  }
}

const rows = computed(() => [
  project('行情', props.quote),
  project('盘口', props.orderBook),
  project('资金流', props.capitalFlow),
  project('元数据', props.metadataProvenance),
])
</script>

<template>
  <div class="dq" data-testid="instrument-data-quality">
    <h3 class="dq__title">数据说明 / 数据质量</h3>
    <p class="dq__disclaimer">
      provider / source 仅作溯源；行情时间以 observed_at 为准，fetched_at 为系统获取时间，二者不可混用。
      资金流为数据提供方分类估算，不代表交易所确认的投资者身份或 Level-2 真值。
    </p>
    <div class="dq__table-wrap">
      <table class="dq__table">
        <thead>
          <tr>
            <th>模块</th>
            <th>数据源</th>
            <th>观察时间</th>
            <th>获取时间</th>
            <th>交易日</th>
            <th>基准</th>
            <th>质量</th>
            <th>状态</th>
            <th>标记</th>
            <th>错误码</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.module" :data-testid="`dq-row-${row.module}`">
            <td>{{ row.module }}</td>
            <td>{{ row.provider }}</td>
            <td class="v3-number">{{ row.observedAt }}</td>
            <td class="v3-number">{{ row.fetchedAt }}</td>
            <td class="v3-number">{{ row.tradingDate }}</td>
            <td>{{ row.dataBasis }}</td>
            <td>{{ row.quality }}</td>
            <td>{{ row.status }}</td>
            <td>{{ row.flags }}</td>
            <td>{{ row.errorCode }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.dq__title {
  margin: 0 0 var(--v3-space-2);
  font-size: var(--v3-font-size-lg);
  font-weight: 700;
}
.dq__disclaimer {
  margin: 0 0 var(--v3-space-3);
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
  line-height: 1.5;
}
.dq__table-wrap {
  overflow-x: auto;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
}
.dq__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--v3-font-size-xs);
  min-width: 720px;
}
.dq__table th,
.dq__table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--v3-border-subtle);
  text-align: left;
  white-space: nowrap;
}
.dq__table th {
  background: var(--v3-surface-muted);
  color: var(--v3-text-muted);
  font-weight: 600;
}
.dq__table td {
  color: var(--v3-text-secondary);
}
</style>
