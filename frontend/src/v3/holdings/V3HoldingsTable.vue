<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import V3HoldingSparkline from './V3HoldingSparkline.vue'
import {
  directionClass,
  formatMoney,
  formatPercentFromPoints,
  formatPrice,
  formatQty,
  formatRatioPercent,
  HOLDING_STATUS_LABEL,
} from './holdings-formatters'
import type { V3HoldingFilter, V3HoldingRowVM, V3HoldingSort } from './holdings-types'
import type { SparklinePoint } from './useV3Holdings'

const props = defineProps<{
  rows: V3HoldingRowVM[]
  sparklines: Map<string, SparklinePoint[] | null>
  loading?: boolean
  filter: V3HoldingFilter
  sort: V3HoldingSort
}>()

const emit = defineEmits<{
  rowClick: [row: V3HoldingRowVM]
  updateFilter: [value: V3HoldingFilter]
  updateSort: [value: V3HoldingSort]
  visibleSparklines: [codes: string[]]
}>()

const tableRef = ref<HTMLElement | null>(null)

const filterOptions: { value: V3HoldingFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'actionable', label: '需行动' },
  { value: 'conditional', label: '条件观察' },
  { value: 'risk_data', label: '风险/数据问题' },
]

const sortOptions: { value: V3HoldingSort; label: string }[] = [
  { value: 'default', label: '默认' },
  { value: 'weight', label: '仓位' },
  { value: 'pnl', label: '盈亏' },
  { value: 'change', label: '涨跌' },
  { value: 'judgment', label: '系统判断' },
]

const visibleCodes = computed(() => props.rows
  .map((row) => row.sparklineCode)
  .filter((code): code is string => Boolean(code)))

watch(visibleCodes, (codes) => {
  emit('visibleSparklines', codes)
}, { immediate: true })

onMounted(() => {
  emit('visibleSparklines', visibleCodes.value)
})

function judgmentTone(row: V3HoldingRowVM): 'info' | 'warning' | 'danger' | 'blocked' | 'neutral' {
  const status = row.judgment.status
  if (status === 'ADD' || status === 'REDUCE' || status === 'EXIT') return 'danger'
  if (status === 'CONDITIONAL_ADD' || status === 'CONDITIONAL_REDUCE' || status === 'WATCH') return 'warning'
  if (status === 'RISK_BLOCKED') return 'blocked'
  if (status === 'DATA_INSUFFICIENT') return 'neutral'
  return 'info'
}

function quoteBadge(row: V3HoldingRowVM): { label: string; tone: 'info' | 'warning' | 'danger' | 'neutral' } | null {
  if (row.unresolved) return { label: '身份不完整', tone: 'warning' }
  if (row.quoteStatus === 'STALE') return { label: '过期', tone: 'warning' }
  if (row.quoteStatus === 'DEGRADED') return { label: '降级', tone: 'warning' }
  if (row.quoteStatus === 'MISSING') return { label: '行情缺失', tone: 'neutral' }
  return null
}

function onRowClick(row: V3HoldingRowVM): void {
  emit('rowClick', row)
}
</script>

<template>
  <section class="table-section" data-testid="v3-holdings-table-section">
    <header class="table-section__header">
      <div class="table-section__filters" role="toolbar" aria-label="持仓筛选">
        <button
          v-for="option in filterOptions"
          :key="option.value"
          type="button"
          class="chip"
          :class="{ 'chip--active': filter === option.value }"
          :data-testid="`v3-holdings-filter-${option.value}`"
          @click="emit('updateFilter', option.value)"
        >{{ option.label }}</button>
      </div>
      <label class="sort-control">
        <span>排序</span>
        <select
          :value="sort"
          data-testid="v3-holdings-sort"
          @change="emit('updateSort', ($event.target as HTMLSelectElement).value as V3HoldingSort)"
        >
          <option v-for="option in sortOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
    </header>

    <div ref="tableRef" class="table-scroll" data-testid="v3-holdings-table">
      <table class="dense-table">
        <thead>
          <tr>
            <th>标的</th>
            <th>趋势</th>
            <th>现价</th>
            <th>今日涨跌</th>
            <th>成本</th>
            <th>持仓</th>
            <th>可用</th>
            <th>行情估算市值</th>
            <th>快照仓位</th>
            <th>浮盈亏</th>
            <th>系统判断</th>
            <th>关键条件</th>
            <th>数据状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="`${row.canonicalCode || row.code}-${row.snapshotId}`"
            class="dense-row"
            :class="{ 'dense-row--clickable': row.canOpenDetail }"
            :data-testid="`v3-holding-row-${row.canonicalCode || row.code}`"
            :data-code="row.canonicalCode || row.code"
            @click="onRowClick(row)"
          >
            <td>
              <div class="instrument">
                <strong>{{ row.name || row.code || '—' }}</strong>
                <span class="instrument__code">{{ row.canonicalCode || row.code || '—' }}</span>
                <span v-if="row.instrumentType" class="instrument__type">{{ row.instrumentType }}</span>
                <span v-if="row.unresolved" class="instrument__incomplete">证券身份不完整</span>
              </div>
            </td>
            <td>
              <V3HoldingSparkline :points="row.sparklineCode ? sparklines.get(row.sparklineCode) : null" />
            </td>
            <td class="v3-number" data-testid="v3-holding-price">
              {{ row.quote?.last == null ? '—' : formatPrice(row.quote.last) }}
            </td>
            <td
              class="v3-number"
              :class="directionClass(row.quote?.direction || 'unknown')"
              data-testid="v3-holding-change"
              :data-direction="row.quote?.direction || 'unknown'"
            >
              {{ row.quote?.changePct == null ? '—' : formatPercentFromPoints(row.quote.changePct) }}
            </td>
            <td class="v3-number">{{ formatPrice(row.cost) }}</td>
            <td class="v3-number" data-testid="v3-holding-qty">{{ formatQty(row.qty) }}</td>
            <td class="v3-number" data-testid="v3-holding-available">
              {{ formatQty(row.availableQty) }}
              <small v-if="row.execution.text" class="constraint" data-testid="v3-holding-constraint">{{ row.execution.text }}</small>
            </td>
            <td class="v3-number" data-testid="v3-holding-mv">
              <template v-if="row.marketValueSource === 'quote_estimate'">
                {{ formatMoney(row.liveMarketValueEstimate) }}
                <small class="source">行情估算</small>
              </template>
              <template v-else-if="row.marketValueSource === 'snapshot'">
                {{ formatMoney(row.snapshotMarketValue) }}
                <small class="source">快照</small>
              </template>
              <template v-else>—</template>
            </td>
            <td class="v3-number" data-testid="v3-holding-weight">{{ formatRatioPercent(row.weight) }}</td>
            <td
              class="v3-number"
              :class="directionClass(
                row.snapshotPnlRatio === null && row.markedPnlEstimate === null
                  ? 'unknown'
                  : (row.snapshotPnlRatio ?? row.markedPnlEstimate ?? 0) > 0
                    ? 'up'
                    : (row.snapshotPnlRatio ?? row.markedPnlEstimate ?? 0) < 0
                      ? 'down'
                      : 'flat'
              )"
              data-testid="v3-holding-pnl"
            >
              <template v-if="row.pnlSource === 'snapshot'">
                {{ formatPercentFromPoints(row.snapshotPnlRatio) }}
                <small v-if="row.snapshotPnlAmount !== null" class="source">{{ formatMoney(row.snapshotPnlAmount) }} · 快照</small>
              </template>
              <template v-else-if="row.pnlSource === 'marked_estimate'">
                {{ formatMoney(row.markedPnlEstimate) }}
                <small class="source">按现价估算</small>
              </template>
              <template v-else>—</template>
            </td>
            <td data-testid="v3-holding-judgment" :data-status="row.judgment.status">
              <div class="judgment">
                <V3StatusBadge :label="row.judgment.label" :tone="judgmentTone(row)" />
                <small v-if="row.judgment.secondary" class="judgment__secondary">{{ row.judgment.secondary }}</small>
                <small v-if="row.execution.blocking" class="judgment__blocked">不可执行</small>
              </div>
            </td>
            <td data-testid="v3-holding-trigger">
              <div class="trigger">
                <span>{{ row.judgment.keyTrigger || '—' }}</span>
                <small v-if="row.judgment.hardCap !== null" class="trigger__cap">
                  权重 {{ formatRatioPercent(row.weight) }} / Hard Cap {{ formatRatioPercent(row.judgment.hardCap) }}
                  <template v-if="row.judgment.headroom !== null"> / 剩余 {{ formatRatioPercent(row.judgment.headroom) }}</template>
                </small>
              </div>
            </td>
            <td data-testid="v3-holding-data-status">
              <div class="data-status">
                <V3StatusBadge
                  v-if="quoteBadge(row)"
                  :label="quoteBadge(row)!.label"
                  :tone="quoteBadge(row)!.tone"
                />
                <V3StatusBadge
                  v-if="row.strategyStatus === 'STALE_SNAPSHOT'"
                  label="策略过期"
                  tone="warning"
                />
                <V3StatusBadge
                  v-if="row.strategyStatus === 'MISSING'"
                  label="策略缺失"
                  tone="neutral"
                />
                <V3StatusBadge
                  v-if="!quoteBadge(row) && row.strategyStatus !== 'STALE_SNAPSHOT' && row.strategyStatus !== 'MISSING'"
                  label="有效"
                  tone="info"
                />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="loading && !rows.length" class="table-loading" data-testid="v3-holdings-table-loading">加载持仓…</div>
      <div v-else-if="!rows.length" class="table-empty" data-testid="v3-holdings-table-empty">当前筛选条件下没有持仓</div>
    </div>
  </section>
</template>

<style scoped>
.table-section {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  overflow: hidden;
}
.table-section__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--v3-space-3);
  border-bottom: 1px solid var(--v3-border-subtle);
  padding: var(--v3-space-3) var(--v3-space-4);
}
.table-section__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-pill);
  background: var(--v3-surface-muted);
  color: var(--v3-text-secondary);
  padding: 4px 10px;
  font-size: var(--v3-font-size-xs);
  cursor: pointer;
}
.chip--active {
  border-color: var(--v3-primary);
  background: var(--v3-primary-soft);
  color: var(--v3-primary);
  font-weight: 600;
}
.sort-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.sort-control select {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text);
  padding: 4px 8px;
}
.table-scroll {
  overflow-x: auto;
  max-height: 560px;
}
.dense-table {
  width: 100%;
  min-width: 1180px;
  border-collapse: collapse;
  font-size: var(--v3-font-size-sm);
}
.dense-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--v3-surface-muted);
  color: var(--v3-text-muted);
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
  padding: 8px;
  border-bottom: 1px solid var(--v3-border);
}
.dense-table td {
  border-top: 1px solid var(--v3-border-subtle);
  padding: 8px;
  vertical-align: top;
}
.dense-row--clickable { cursor: pointer; }
.dense-row--clickable:hover { background: var(--v3-surface-hover); }
.instrument {
  display: grid;
  gap: 2px;
  min-width: 120px;
}
.instrument__code,
.instrument__type {
  color: var(--v3-text-muted);
  font-size: 11px;
}
.instrument__incomplete {
  color: var(--v3-status-warning);
  font-size: 11px;
  font-weight: 600;
}
.source,
.constraint,
.judgment__secondary,
.judgment__blocked,
.trigger__cap {
  display: block;
  color: var(--v3-text-muted);
  font-size: 11px;
  font-weight: 400;
  margin-top: 2px;
}
.judgment__blocked { color: var(--v3-status-warning); font-weight: 600; }
.market-up { color: var(--v3-market-up); }
.market-down { color: var(--v3-market-down); }
.market-flat { color: var(--v3-market-flat); }
.data-status,
.judgment,
.trigger {
  display: grid;
  gap: 4px;
  justify-items: start;
}
.table-loading,
.table-empty {
  padding: var(--v3-space-6);
  color: var(--v3-text-muted);
  text-align: center;
}
@media (max-width: 680px) {
  .table-scroll { max-height: 420px; }
}
</style>
