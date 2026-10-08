<script setup lang="ts">
import { computed } from 'vue'
import type { InstrumentMetadataResponse } from '@/api/types'
import { DASH, formatDate } from './formatters'

const props = defineProps<{
  metadata: InstrumentMetadataResponse | null
}>()

const identity = computed(() => props.metadata?.identity ?? null)
const meta = computed(() => props.metadata?.metadata ?? null)
const type = computed(() => identity.value?.instrument_type ?? null)

const stockRows = computed(() => {
  const m = meta.value
  if (!m) return []
  return [
    { label: '行业', value: m.industry || DASH },
    { label: '概念', value: m.concepts?.length ? m.concepts.join('、') : DASH },
    { label: '上市日期', value: formatDate(m.list_date) },
    { label: 'ST', value: m.is_st ? '是' : '否' },
    { label: '板块', value: m.board || DASH },
    { label: '每手股数', value: m.lot_size == null ? DASH : String(m.lot_size) },
    { label: '涨跌停规则', value: m.price_limit_rule || DASH },
    { label: '可交易', value: m.available_for_trading ? '是' : '否' },
  ]
})

const etfRows = computed(() => {
  const m = meta.value
  if (!m) return []
  return [
    { label: '基金类型', value: m.fund_type || DASH },
    { label: '跟踪指数', value: m.underlying_index || DASH },
    { label: '管理人', value: m.management_company || DASH },
    { label: '费用率', value: m.expense_ratio == null ? DASH : `${(m.expense_ratio * 100).toFixed(2)}%` },
    { label: '跟踪标的', value: m.tracking_target || DASH },
    { label: '每手股数', value: m.lot_size == null ? DASH : String(m.lot_size) },
    { label: '可交易', value: m.available_for_trading ? '是' : '否' },
  ]
})

const indexRows = computed(() => {
  const m = meta.value
  if (!m) return []
  return [
    { label: '发布机构', value: m.publisher || DASH },
    { label: '基日', value: formatDate(m.base_date) },
    { label: '基点', value: m.base_value == null ? DASH : String(m.base_value) },
    { label: '成分数量', value: m.constituent_count == null ? DASH : String(m.constituent_count) },
  ]
})

const rows = computed(() => {
  if (type.value === 'ETF') return etfRows.value
  if (type.value === 'INDEX') return indexRows.value
  return stockRows.value
})
</script>

<template>
  <div class="inst-meta" data-testid="instrument-metadata">
    <h3 class="inst-meta__title">标的资料</h3>
    <dl class="inst-meta__grid">
      <div v-for="row in rows" :key="row.label" class="inst-meta__cell">
        <dt>{{ row.label }}</dt>
        <dd data-testid="instrument-meta-value">{{ row.value }}</dd>
      </div>
    </dl>
  </div>
</template>

<style scoped>
.inst-meta__title {
  margin: 0 0 var(--v3-space-3);
  font-size: var(--v3-font-size-lg);
  font-weight: 700;
}
.inst-meta__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--v3-space-3);
  margin: 0;
}
.inst-meta__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.inst-meta__cell dt {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.inst-meta__cell dd {
  margin: 0;
  color: var(--v3-text-secondary);
  font-size: var(--v3-font-size-md);
  font-weight: 600;
  overflow-wrap: anywhere;
}
</style>
