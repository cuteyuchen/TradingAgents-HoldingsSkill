<script setup lang="ts">
import V3Metric from '../components/V3Metric.vue'
import { formatMoney, formatRatioPercent } from './holdings-formatters'
import type { V3HoldingsSummaryVM } from './holdings-types'

const props = defineProps<{
  summary: V3HoldingsSummaryVM | null
  loading?: boolean
  error?: boolean
  hasPortfolio: boolean
  noSnapshot: boolean
}>()

const emit = defineEmits<{ retry: []; openUpdate: [] }>()

const coverageText = props.summary?.quoteCoverage == null
  ? ''
  : `行情覆盖 ${(props.summary.quoteCoverage * 100).toFixed(0)}%`
</script>

<template>
  <section class="summary" data-testid="v3-holdings-summary" aria-label="组合摘要">
    <div v-if="error && !summary" class="summary__error" role="alert" data-testid="v3-holdings-summary-error">
      <p>持仓快照加载失败</p>
      <button type="button" class="link-btn" data-testid="v3-holdings-snapshot-retry" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="!hasPortfolio" class="summary__empty" data-testid="v3-holdings-no-portfolio">
      尚无投资组合
    </div>
    <div v-else-if="noSnapshot" class="summary__empty" data-testid="v3-holdings-no-snapshot">
      <p>还没有确认持仓快照</p>
      <button type="button" class="link-btn" data-testid="v3-holdings-empty-update" @click="emit('openUpdate')">上传/粘贴持仓截图</button>
    </div>
    <div v-else-if="summary" class="summary__grid">
      <V3Metric
        label="总资产"
        :value="formatMoney(summary.totalAssets)"
        secondary="快照权威"
        data-testid="v3-holdings-total-assets"
      />
      <V3Metric
        label="持仓市值"
        :value="formatMoney(summary.marketValue)"
        :secondary="summary.marketValueSource === 'quote_estimate' ? '行情估算' : summary.marketValueSource === 'snapshot' ? '快照' : ''"
        data-testid="v3-holdings-market-value"
      />
      <V3Metric
        label="现金"
        :value="formatMoney(summary.spendableCash)"
        secondary="快照权威"
        data-testid="v3-holdings-cash"
      />
      <V3Metric
        label="仓位/总暴露"
        :value="summary.grossExposure === null ? '—' : formatRatioPercent(summary.grossExposure, 1)"
        data-testid="v3-holdings-exposure"
      />
      <V3Metric
        label="当日盈亏"
        :value="summary.dayPnlAvailable && summary.dayPnlEstimate !== null ? formatMoney(summary.dayPnlEstimate) : '—'"
        :secondary="summary.dayPnlAvailable ? '按行情估算' : `行情覆盖不足${coverageText ? ` ${coverageText}` : ''}`"
        :trend="summary.dayPnlEstimate === null ? null : summary.dayPnlEstimate > 0 ? 'up' : summary.dayPnlEstimate < 0 ? 'down' : 'flat'"
        data-testid="v3-holdings-day-pnl"
      />
      <V3Metric
        label="浮动盈亏"
        :value="summary.floatingPnlAvailable && summary.floatingPnlSnapshot !== null ? formatMoney(summary.floatingPnlSnapshot) : '—'"
        secondary="快照"
        :trend="summary.floatingPnlSnapshot === null ? null : summary.floatingPnlSnapshot > 0 ? 'up' : summary.floatingPnlSnapshot < 0 ? 'down' : 'flat'"
        data-testid="v3-holdings-float-pnl"
      />
      <V3Metric
        label="组合风险"
        :value="summary.riskFlags.length ? summary.riskFlags.join(' · ') : '无风险标记'"
        :status="summary.riskFlags.length || summary.hardCapBreaches.length ? 'warning' : 'neutral'"
        data-testid="v3-holdings-risk"
      />
      <V3Metric
        label="策略状态"
        :value="summary.strategyState"
        data-testid="v3-holdings-strategy-state"
      />
      <p v-if="coverageText" class="summary__coverage" data-testid="v3-holdings-coverage">{{ coverageText }}</p>
    </div>
    <div v-else-if="loading" class="summary__loading" data-testid="v3-holdings-summary-loading">加载组合摘要…</div>
  </section>
</template>

<style scoped>
.summary {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  padding: var(--v3-space-4);
}
.summary__grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--v3-space-4) var(--v3-space-3);
}
.summary__error,
.summary__empty,
.summary__loading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--v3-space-2);
  color: var(--v3-text-muted);
  min-height: 48px;
}
.summary__coverage {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.link-btn {
  border: 0;
  background: none;
  color: var(--v3-primary);
  cursor: pointer;
  font: inherit;
  padding: 0;
}
@media (max-width: 900px) {
  .summary__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 520px) {
  .summary__grid { grid-template-columns: 1fr; }
}
</style>
