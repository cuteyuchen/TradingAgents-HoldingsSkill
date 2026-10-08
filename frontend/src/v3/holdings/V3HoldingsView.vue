<script setup lang="ts">
/**
 * V3 Holdings Workstation
 * 我持有什么？每个标的现在怎么样？什么条件下需要行动？
 */
import { computed, defineAsyncComponent } from 'vue'
import { Camera, RefreshCw } from 'lucide-vue-next'
import V3EmptyState from '../components/V3EmptyState.vue'
import V3ErrorState from '../components/V3ErrorState.vue'
import V3PageHeader from '../components/V3PageHeader.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import V3HoldingContextStrip from './V3HoldingContextStrip.vue'
import V3HoldingsDecisionBar from './V3HoldingsDecisionBar.vue'
import V3HoldingsSummary from './V3HoldingsSummary.vue'
import V3HoldingsTable from './V3HoldingsTable.vue'
import V3HoldingsUpdateDrawer from './V3HoldingsUpdateDrawer.vue'
import { useV3Holdings } from './useV3Holdings'

const V3InstrumentDetailDrawer = defineAsyncComponent(
  () => import('../instrument-detail/V3InstrumentDetailDrawer.vue'),
)

const {
  portfolios,
  selectedPortfolioId,
  setSelectedPortfolio,
  hasPortfolio,
  portfoliosLoading,
  refreshing,
  currentSnapshotError,
  viewModel,
  setFilter,
  setSort,
  instrumentDrawerOpen,
  selectedInstrumentCode,
  openInstrument,
  updateDrawerOpen,
  openUpdate,
  closeUpdate,
  refreshAll,
  loadSnapshot,
  loadQuotes,
  loadDashboard,
  reloadAfterConfirm,
  sparklines,
  requestVisibleSparklines,
} = useV3Holdings()

const showInitialError = computed(() => Boolean(currentSnapshotError.value) && !viewModel.value.snapshotId)

function onPortfolioChange(event: Event): void {
  const raw = Number((event.target as HTMLSelectElement).value)
  if (Number.isInteger(raw) && raw > 0) setSelectedPortfolio(raw)
}

function onRowClick(row: Parameters<typeof openInstrument>[0]): void {
  openInstrument(row)
}

async function onConfirmed(snapshotId: number): Promise<void> {
  await reloadAfterConfirm(snapshotId)
}
</script>

<template>
  <div class="v3-holdings" data-testid="v3-holdings">
    <V3PageHeader title="我的持仓" description="看清持仓事实、实时行情与策略判断，三层时间分离。">
      <template #status>
        <label class="portfolio-select" data-testid="v3-holdings-portfolio-wrap">
          <span class="portfolio-select__label">组合</span>
          <select
            class="portfolio-select__control"
            data-testid="v3-holdings-portfolio-select"
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
          v-if="viewModel.decision.kind === 'STALE_SNAPSHOT'"
          label="策略过期"
          tone="warning"
          data-testid="v3-holdings-stale-strategy"
        />
        <V3StatusBadge
          v-if="viewModel.identityIncomplete"
          label="证券身份不完整"
          tone="warning"
          data-testid="v3-holdings-identity-incomplete"
        />
      </template>
      <template #actions>
        <button
          type="button"
          class="refresh-btn"
          aria-label="刷新持仓"
          data-testid="v3-holdings-refresh"
          :disabled="refreshing"
          @click="refreshAll"
        >
          <RefreshCw :size="15" />
          <span>刷新</span>
        </button>
        <button
          type="button"
          class="refresh-btn refresh-btn--primary"
          aria-label="更新持仓"
          data-testid="v3-holdings-update"
          :disabled="!hasPortfolio"
          @click="openUpdate"
        >
          <Camera :size="15" />
          <span>更新持仓</span>
        </button>
      </template>
    </V3PageHeader>

    <div class="holdings-meta">
      <V3HoldingContextStrip
        :snapshot-at="viewModel.timestamps.snapshotAt"
        :quote-at="viewModel.timestamps.quoteAt"
        :strategy-at="viewModel.timestamps.strategyAt"
        :session-label="viewModel.timestamps.sessionLabel"
      />
    </div>

    <V3ErrorState
      v-if="showInitialError"
      title="持仓快照加载失败"
      description="请重试加载最近确认快照。"
      data-testid="v3-holdings-snapshot-error"
      @retry="loadSnapshot"
    />

    <V3EmptyState
      v-else-if="!hasPortfolio"
      title="尚无投资组合"
      description="创建组合或上传持仓后即可查看工作站。"
      data-testid="v3-holdings-empty-portfolio"
    />

    <V3EmptyState
      v-else-if="viewModel.noSnapshot"
      title="还没有确认持仓快照"
      description="上传/粘贴持仓截图并确认后，这里会显示持仓事实与策略判断。"
      data-testid="v3-holdings-empty-snapshot"
    >
      <template #actions>
        <button type="button" class="refresh-btn refresh-btn--primary" data-testid="v3-holdings-empty-update" @click="openUpdate">
          上传持仓
        </button>
      </template>
    </V3EmptyState>

    <template v-else>
      <V3HoldingsSummary
        :summary="viewModel.summary"
        :has-portfolio="hasPortfolio"
        :no-snapshot="viewModel.noSnapshot"
        :error="Boolean(currentSnapshotError)"
        @retry="loadSnapshot"
        @open-update="openUpdate"
      />

      <V3HoldingsDecisionBar :decision="viewModel.decision" />

      <V3EmptyState
        v-if="viewModel.emptySnapshot"
        title="已确认快照，但暂无持仓"
        description="当前组合没有可展示的持仓行。"
        data-testid="v3-holdings-empty-rows"
      />

      <V3HoldingsTable
        v-else
        :rows="viewModel.rows"
        :sparklines="sparklines"
        :filter="viewModel.filter"
        :sort="viewModel.sort"
        @row-click="onRowClick"
        @update-filter="setFilter"
        @update-sort="setSort"
        @visible-sparklines="requestVisibleSparklines"
      />
    </template>

    <V3HoldingsUpdateDrawer
      v-model="updateDrawerOpen"
      :portfolio-id="selectedPortfolioId"
      @confirmed="onConfirmed"
      @update:model-value="closeUpdate"
    />

    <V3InstrumentDetailDrawer
      v-if="instrumentDrawerOpen && selectedInstrumentCode"
      v-model="instrumentDrawerOpen"
      :code="selectedInstrumentCode"
    />
  </div>
</template>

<style scoped>
.v3-holdings {
  display: grid;
  gap: var(--v3-space-4);
  min-width: 0;
}
.holdings-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--v3-space-3);
}
.portfolio-select {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.portfolio-select__label {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.portfolio-select__control {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text);
  padding: 6px 10px;
  font: inherit;
  min-width: 140px;
}
.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text-secondary);
  padding: 7px 12px;
  cursor: pointer;
  font: inherit;
}
.refresh-btn--primary {
  border-color: var(--v3-primary);
  background: var(--v3-primary);
  color: var(--v3-text-inverse);
}
.refresh-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
@media (max-width: 680px) {
  .portfolio-select__control { min-width: 120px; }
}
</style>
