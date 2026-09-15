<script setup lang="ts">
import { computed } from 'vue'
import V3EmptyState from '../components/V3EmptyState.vue'
import V3LoadingState from '../components/V3LoadingState.vue'
import V3Metric from '../components/V3Metric.vue'
import { formatMoney, formatNumber, formatRatioPercent } from './dashboard-formatters'
import type { V3PortfolioSummaryVM } from './dashboard-types'

const props = defineProps<{
  portfolio: V3PortfolioSummaryVM | null
  hasPortfolio: boolean
  loading?: boolean
  error?: boolean
}>()

const emit = defineEmits<{ goHoldings: [] }>()

const freshnessLabel = computed(() => {
  const map: Record<string, string> = {
    FRESH: '正常',
    STALE: '组合快照过期',
    MISSING: '快照缺失',
    FROZEN: '快照冻结',
    UNKNOWN: '状态未知',
  }
  return map[props.portfolio?.freshness || 'UNKNOWN'] || props.portfolio?.freshness || '状态未知'
})
</script>

<template>
  <section class="portfolio" data-testid="v3-portfolio-snapshot" aria-labelledby="portfolio-title">
    <header class="portfolio__header">
      <h2 id="portfolio-title" class="portfolio__title">组合状态</h2>
      <span v-if="portfolio" class="portfolio__name" data-testid="v3-portfolio-name">{{ portfolio.portfolioName }}</span>
      <span class="portfolio__freshness" data-testid="v3-portfolio-freshness">{{ freshnessLabel }}</span>
    </header>

    <V3LoadingState v-if="loading && !portfolio" label="加载组合…" />
    <V3EmptyState
      v-else-if="!hasPortfolio"
      title="尚未选择/创建投资组合"
      description="导入持仓或创建组合后，这里会显示总资产、仓位与风险质量。"
      data-testid="v3-no-portfolio"
    >
      <template #actions>
        <button type="button" class="link-btn" @click="emit('goHoldings')">去导入持仓</button>
      </template>
    </V3EmptyState>
    <div v-else-if="portfolio && portfolio.status === 'MISSING'" class="portfolio__missing" data-testid="v3-portfolio-missing">
      <p>组合快照缺失，无法展示最新持仓市值。</p>
      <button type="button" class="link-btn" @click="emit('goHoldings')">更新持仓</button>
    </div>
    <div v-else-if="portfolio" class="portfolio__metrics">
      <V3Metric
        label="总资产"
        :value="formatMoney(portfolio.totalAssets)"
        data-testid="v3-portfolio-total-assets"
      />
      <V3Metric label="持仓市值" :value="formatMoney(portfolio.marketValue)" />
      <V3Metric label="现金" :value="formatMoney(portfolio.spendableCash)" />
      <V3Metric
        label="仓位/总暴露"
        :value="portfolio.grossExposure === null ? '—' : formatRatioPercent(portfolio.grossExposure, 1)"
        data-testid="v3-portfolio-exposure"
      />
      <V3Metric label="持仓数" :value="portfolio.positionCount === null ? '—' : String(portfolio.positionCount)" />
      <V3Metric
        label="组合质量"
        :value="portfolio.qualityStatus"
        :status="portfolio.qualityStatus === 'VALID' || portfolio.qualityStatus === 'OK' ? 'info' : 'warning'"
      />
      <V3Metric
        label="当日收益"
        :value="portfolio.dayReturnAvailable ? formatNumber(portfolio.dayReturn, 2) : '—'"
        secondary="无 authoritative 合约"
        data-testid="v3-portfolio-day-return"
      />
      <V3Metric
        label="浮动盈亏"
        :value="portfolio.floatingPnlAvailable ? formatMoney(portfolio.floatingPnl) : '—'"
        secondary="无 authoritative 合约"
        data-testid="v3-portfolio-floating-pnl"
      />
    </div>
    <p v-if="error && portfolio" class="portfolio__error" data-testid="v3-portfolio-stale">刷新失败，显示上次成功数据。</p>
  </section>
</template>

<style scoped>
.portfolio__header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--v3-space-2);
  margin-bottom: var(--v3-space-3);
}
.portfolio__title { margin: 0; font-size: var(--v3-font-size-xl); font-weight: 700; }
.portfolio__name { color: var(--v3-text-secondary); font-weight: 600; }
.portfolio__freshness {
  margin-left: auto;
  border-radius: var(--v3-radius-pill);
  background: var(--v3-surface-muted);
  color: var(--v3-text-muted);
  padding: 2px 8px;
  font-size: var(--v3-font-size-xs);
}
.portfolio__metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--v3-space-3);
}
.portfolio__missing,
.portfolio__error {
  color: var(--v3-text-secondary);
  font-size: var(--v3-font-size-sm);
}
.portfolio__error { color: var(--v3-status-warning); }
.link-btn {
  border: 0;
  background: none;
  color: var(--v3-primary);
  cursor: pointer;
  font-weight: 600;
  padding: 0;
}
@media (max-width: 900px) {
  .portfolio__metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
