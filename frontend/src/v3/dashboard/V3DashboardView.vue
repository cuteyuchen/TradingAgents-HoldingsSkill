<script setup lang="ts">
/**
 * V3 Dashboard Decision Workbench
 * 决策顺序：时段/时间 → 六大指数 → 系统性风险 → 组合 → 是否行动 → 最新分析 → 重要事件
 */
import { computed, defineAsyncComponent } from 'vue'
import { useRouter } from 'vue-router'
import { RefreshCw } from 'lucide-vue-next'
import V3EmptyState from '../components/V3EmptyState.vue'
import V3ErrorState from '../components/V3ErrorState.vue'
import V3PageHeader from '../components/V3PageHeader.vue'
import V3Section from '../components/V3Section.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import V3ActionList from './V3ActionList.vue'
import V3DashboardSessionBar from './V3DashboardSessionBar.vue'
import V3DecisionHero from './V3DecisionHero.vue'
import V3ImportantEvents from './V3ImportantEvents.vue'
import V3LatestAnalysis from './V3LatestAnalysis.vue'
import V3MajorIndices from './V3MajorIndices.vue'
import V3PortfolioSnapshot from './V3PortfolioSnapshot.vue'
import V3SystemicRiskPanel from './V3SystemicRiskPanel.vue'
import { useV3Dashboard } from './useV3Dashboard'

const V3InstrumentDetailDrawer = defineAsyncComponent(
  () => import('../instrument-detail/V3InstrumentDetailDrawer.vue'),
)

const router = useRouter()
const {
  portfolios,
  selectedPortfolioId,
  setSelectedPortfolio,
  hasPortfolio,
  portfoliosLoading,
  refreshing,
  sessionLoading,
  indicesLoading,
  riskLoading,
  portfolioLoading,
  sessionError,
  indicesError,
  riskError,
  overviewError,
  portfolioError,
  viewModel,
  instrumentDrawerOpen,
  selectedInstrumentCode,
  openInstrument,
  refreshAll,
} = useV3Dashboard()

const pageTitle = computed(() => {
  const date = viewModel.value.tradeDate
  return date ? `今天 · ${date}` : '今天的投资驾驶舱'
})

const marketRefreshFailed = computed(
  () => Boolean(sessionError.value || indicesError.value || riskError.value || overviewError.value)
    && Boolean(viewModel.value.sessionBar.quoteAt || viewModel.value.majorIndices.some((item) => item.status === 'available')),
)

const portfolioDashboardHasData = computed(() => Boolean(viewModel.value.portfolio && viewModel.value.portfolio.status !== 'MISSING'))

function onPortfolioChange(event: Event): void {
  const raw = Number((event.target as HTMLSelectElement).value)
  if (Number.isInteger(raw) && raw > 0) setSelectedPortfolio(raw)
}

function goHoldings(): void {
  void router.push({ name: 'holdings', query: { action: 'update', portfolio: selectedPortfolioId.value || undefined } })
}

function goAnalysis(): void {
  void router.push({ name: 'analysis', query: { portfolio: selectedPortfolioId.value || undefined } })
}
</script>

<template>
  <div class="v3-dashboard" data-testid="v3-dashboard">
    <V3PageHeader :title="pageTitle" description="先看市场状态，再看组合今天要不要行动。">
      <template #status>
        <label class="portfolio-select" data-testid="v3-portfolio-select-wrap">
          <span class="portfolio-select__label">组合</span>
          <select
            class="portfolio-select__control"
            data-testid="v3-portfolio-select"
            aria-label="选择组合"
            :disabled="!portfolios.length || portfoliosLoading"
            :value="selectedPortfolioId ?? ''"
            @change="onPortfolioChange"
          >
            <option v-if="!portfolios.length" value="" disabled>暂无组合</option>
            <option v-for="item in portfolios" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <V3StatusBadge
          v-if="marketRefreshFailed"
          label="市场刷新失败 · 保留上次数据"
          tone="warning"
          data-testid="v3-market-stale"
        />
        <V3StatusBadge
          v-if="portfolioError && portfolioDashboardHasData"
          label="组合刷新失败"
          tone="warning"
          data-testid="v3-portfolio-refresh-failed"
        />
      </template>
      <template #actions>
        <button
          type="button"
          class="refresh-btn"
          aria-label="刷新首页数据"
          data-testid="v3-dashboard-refresh"
          :disabled="refreshing"
          @click="refreshAll"
        >
          <RefreshCw :size="14" aria-hidden="true" :class="{ spin: refreshing }" />
          {{ refreshing ? '刷新中' : '刷新' }}
        </button>
      </template>
    </V3PageHeader>

    <div class="v3-dashboard__stack">
      <!-- 1. Session / time -->
      <V3DashboardSessionBar
        :session-bar="viewModel.sessionBar"
        :loading="sessionLoading"
        :stale="marketRefreshFailed"
      />

      <!-- 2. Six major indices -->
      <V3MajorIndices
        :items="viewModel.majorIndices"
        :loading="indicesLoading"
        @select="openInstrument"
      />
      <V3ErrorState
        v-if="indicesError && !viewModel.majorIndices.some((item) => item.status === 'available')"
        title="指数加载失败"
        description="市场指数暂时不可用，不影响组合决策模块。"
        data-testid="v3-indices-error"
      />

      <!-- 3+4. Systemic risk (2/3) + Portfolio (1/3) -->
      <div class="v3-dashboard__row">
        <div class="v3-dashboard__risk">
          <V3Section compact>
            <V3SystemicRiskPanel
              :risk="viewModel.systemicRisk"
              :typical="viewModel.typicalStock"
              :breadth="viewModel.breadth"
              :turnover="viewModel.turnover"
              :concentration="viewModel.concentration"
              :loading="riskLoading"
            />
          </V3Section>
        </div>
        <div class="v3-dashboard__portfolio">
          <V3Section compact>
            <V3PortfolioSnapshot
              :portfolio="viewModel.portfolio"
              :has-portfolio="hasPortfolio"
              :loading="portfolioLoading"
              :error="Boolean(portfolioError)"
              @go-holdings="goHoldings"
            />
          </V3Section>
        </div>
      </div>

      <!-- 5. Decision hero -->
      <V3DecisionHero
        :decision="viewModel.decision"
        @open-instrument="openInstrument"
        @go-analysis="goAnalysis"
      />

      <!-- 6. Actions + Latest analysis -->
      <div class="v3-dashboard__row v3-dashboard__row--half">
        <V3Section title="今日行动" description="仅展示 backend structured decision，不在前端创造买卖建议。" compact>
          <V3ActionList
            :holding-actions="viewModel.decision.holdingActions"
            :candidate-actions="viewModel.decision.candidateActions"
            :kind="viewModel.decision.kind"
            @open-instrument="openInstrument"
          />
        </V3Section>
        <V3Section compact>
          <V3LatestAnalysis :analysis="viewModel.latestAnalysis" @go-analysis="goAnalysis" />
        </V3Section>
      </div>

      <!-- 7. Important events -->
      <V3Section compact>
        <V3ImportantEvents :events="viewModel.importantEvents" />
      </V3Section>

      <V3EmptyState
        v-if="!hasPortfolio"
        title="市场数据仍可查看"
        description="尚未选择组合时，决策区会提示建立组合，不会显示明确无需操作。"
      />
    </div>

    <!-- Single shared InstrumentDetail drawer -->
    <V3InstrumentDetailDrawer
      v-if="instrumentDrawerOpen && selectedInstrumentCode"
      v-model="instrumentDrawerOpen"
      :code="selectedInstrumentCode"
    />
  </div>
</template>

<style scoped>
.v3-dashboard { display: grid; gap: var(--v3-space-4); min-width: 0; }
.v3-dashboard__stack { display: grid; gap: var(--v3-space-4); min-width: 0; }
.v3-dashboard__row {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: var(--v3-space-4);
  min-width: 0;
}
.v3-dashboard__row--half { grid-template-columns: 1fr 1fr; }
.portfolio-select {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--v3-font-size-sm);
  color: var(--v3-text-secondary);
}
.portfolio-select__control {
  min-width: 140px;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text);
  padding: 6px 8px;
  font: inherit;
}
.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text-secondary);
  cursor: pointer;
  padding: 7px 12px;
  font-weight: 600;
}
.refresh-btn:disabled { opacity: 0.6; cursor: default; }
.spin { animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 960px) {
  .v3-dashboard__row,
  .v3-dashboard__row--half { grid-template-columns: 1fr; }
}
</style>
