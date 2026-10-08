<script setup lang="ts">
/**
 * Unified InstrumentDetail core — shared by full page and drawer.
 */
import { computed, toRef } from 'vue'
import { DASH, formatDataBasis, formatQuality } from './formatters'
import { useInstrumentDetail, type InstrumentTab } from './useInstrumentDetail'
import V3InstrumentCapitalFlow from './V3InstrumentCapitalFlow.vue'
import V3InstrumentDataQuality from './V3InstrumentDataQuality.vue'
import V3InstrumentHeader from './V3InstrumentHeader.vue'
import V3InstrumentKlineChart from './V3InstrumentKlineChart.vue'
import V3InstrumentMetadata from './V3InstrumentMetadata.vue'
import V3InstrumentOrderBook from './V3InstrumentOrderBook.vue'
import V3InstrumentQuoteSummary from './V3InstrumentQuoteSummary.vue'
import V3EmptyState from '../components/V3EmptyState.vue'
import V3ErrorState from '../components/V3ErrorState.vue'
import V3LoadingState from '../components/V3LoadingState.vue'
import V3Section from '../components/V3Section.vue'
import V3Tabs from '../components/V3Tabs.vue'

const props = withDefaults(
  defineProps<{
    code: string
    initialTab?: InstrumentTab
    compact?: boolean
  }>(),
  { initialTab: 'quote', compact: false },
)

const controller = useInstrumentDetail({
  code: toRef(props, 'code'),
  initialTab: props.initialTab,
})

const {
  activeTab,
  interval,
  adjustment,
  barLimit,
  metadata,
  quote,
  bars,
  orderBook,
  capitalFlow,
  identity,
  canonicalCode,
  pageLoading,
  pageError,
  pageNotFound,
  quoteLoading,
  barsLoading,
  bookLoading,
  flowLoading,
  quoteError,
  barsError,
  bookError,
  flowError,
  supportsBook,
  supportsFlow,
  supportsBars,
  setTab,
  setInterval,
  setAdjustment,
  setBarLimit,
  refreshAll,
  loadOrderBook,
  loadCapitalFlow,
  loadBars,
  loadQuote,
} = controller

const tabs = computed(() => [
  { name: 'quote', label: '行情' },
  { name: 'book', label: '盘口' },
  { name: 'flow', label: '资金' },
  { name: 'analysis', label: '分析' },
  { name: 'news', label: '新闻' },
  { name: 'history', label: '历史' },
])

const headerQuality = computed(() => quote.value?.quality ?? metadata.value?.quality ?? null)
const headerObservedAt = computed(() => quote.value?.observed_at ?? null)
const headerDataBasis = computed(() => quote.value?.data_basis ?? null)
const headerStale = computed(() => quote.value?.status === 'stale')
const headerFallback = computed(() => quote.value?.fallback === true || bars.value?.fallback === true)
const prevClose = computed(() => quote.value?.prev_close ?? null)

function onTabChange(name: string | number) {
  setTab(String(name) as InstrumentTab)
}
</script>

<template>
  <div class="inst-detail" :class="{ 'inst-detail--compact': compact }" data-testid="instrument-detail">
    <V3LoadingState v-if="pageLoading && !metadata" variant="card" label="标的信息加载中" data-testid="detail-page-loading" />

    <V3ErrorState
      v-else-if="pageNotFound"
      title="标的不存在或主数据未收录"
      :description="pageError?.description || '请确认证券代码，SecurityMaster 中没有该标的时不会自动创建。'"
      data-testid="detail-not-found"
      @retry="refreshAll"
    />
    <V3ErrorState
      v-else-if="pageError && !metadata"
      :title="pageError.title"
      :description="pageError.description"
      data-testid="detail-page-error"
      @retry="refreshAll"
    />

    <template v-else>
      <div class="inst-detail__toolbar">
        <button
          type="button"
          class="inst-detail__refresh"
          aria-label="刷新标的数据"
          data-testid="instrument-refresh"
          @click="refreshAll"
        >刷新</button>
      </div>

      <V3InstrumentHeader
        :identity="identity"
        :observed-at="headerObservedAt"
        :data-basis="headerDataBasis"
        :quality="headerQuality"
        :stale="headerStale"
        :fallback="headerFallback"
        :suspended="identity?.is_suspended || false"
      />

      <V3Tabs
        :model-value="activeTab"
        :items="tabs"
        dense
        class="inst-detail__tabs"
        @update:model-value="onTabChange"
      >
        <div v-if="activeTab === 'quote'" data-testid="tab-panel-quote">
          <V3InstrumentQuoteSummary :quote="quote" :loading="quoteLoading" />
          <V3ErrorState
            v-if="quoteError"
            :title="quoteError.title"
            :description="quoteError.description"
            class="inst-detail__module-error"
            data-testid="quote-error"
            @retry="loadQuote"
          />
          <V3Section title="K线与指标" class="inst-detail__block" compact>
            <V3InstrumentKlineChart
              v-if="supportsBars"
              :bars="bars"
              :loading="barsLoading"
              :error="barsError"
              :interval="interval"
              :adjustment="adjustment"
              :bar-limit="barLimit"
              :supported-intervals="(metadata?.capabilities?.bar_intervals as import('@/api/types').BarInterval[] | undefined) || (['1d', '1w', '1M'] as import('@/api/types').BarInterval[])"
              :supported-adjustments="(metadata?.capabilities?.adjustments as import('@/api/types').BarAdjustment[] | undefined) || []"
              :symbol-code="canonicalCode"
              :symbol-name="identity?.name || canonicalCode"
              @retry="loadBars"
              @update:interval="setInterval"
              @update:adjustment="setAdjustment"
              @update:bar-limit="setBarLimit"
            />
            <V3EmptyState
              v-else
              title="该标的不支持K线"
              description="capabilities.bars=false"
              data-testid="kline-unsupported"
            />
          </V3Section>
          <V3Section title="数据说明" class="inst-detail__block" compact>
            <V3InstrumentDataQuality
              :quote="quote"
              :order-book="orderBook"
              :capital-flow="capitalFlow"
              :metadata-provenance="metadata"
            />
          </V3Section>
          <V3Section title="标的资料" class="inst-detail__block" compact>
            <V3InstrumentMetadata :metadata="metadata" />
          </V3Section>
        </div>

        <div v-else-if="activeTab === 'book'" data-testid="tab-panel-book">
          <V3InstrumentOrderBook
            :order-book="orderBook"
            :loading="bookLoading"
            :error="bookError"
            :supported="supportsBook"
            :prev-close="prevClose"
            @retry="loadOrderBook(true)"
          />
        </div>

        <div v-else-if="activeTab === 'flow'" data-testid="tab-panel-flow">
          <V3InstrumentCapitalFlow
            :capital-flow="capitalFlow"
            :loading="flowLoading"
            :error="flowError"
            :supported="supportsFlow"
            @retry="loadCapitalFlow(true)"
          />
        </div>

        <div v-else-if="activeTab === 'analysis'" data-testid="tab-panel-analysis">
          <V3EmptyState
            title="当前版本暂无统一结构化标的分析接口"
            description="将在结构化分析接口接通后提供；不会从 Markdown 报告或 Agent hidden reasoning 猜结论。"
            data-testid="analysis-unavailable"
          />
        </div>

        <div v-else-if="activeTab === 'news'" data-testid="tab-panel-news">
          <V3EmptyState
            title="当前版本暂无统一标的新闻数据接口"
            description="将仅消费按 instrument code 查询的结构化新闻 contract，不前端直连外部源。"
            data-testid="news-unavailable"
          />
        </div>

        <div v-else-if="activeTab === 'history'" data-testid="tab-panel-history">
          <V3EmptyState
            title="历史决策将在结构化接口接通后提供"
            description="本阶段不解析报告猜结论，也不伪造历史决策列表。"
            data-testid="history-unavailable"
          />
        </div>
      </V3Tabs>

      <p class="inst-detail__footer-meta v3-number" data-testid="detail-footer-meta">
        canonical={{ canonicalCode || DASH }} · basis={{ formatDataBasis(headerDataBasis) }} · quality={{ formatQuality(headerQuality) }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.inst-detail {
  display: flex;
  flex-direction: column;
  gap: var(--v3-space-4);
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
  contain: inline-size;
}
.inst-detail__toolbar {
  display: flex;
  justify-content: flex-end;
}
.inst-detail__refresh {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text-secondary);
  padding: 6px 12px;
  font-size: var(--v3-font-size-sm);
  font-weight: 600;
  cursor: pointer;
}
.inst-detail__refresh:hover {
  border-color: var(--v3-primary);
  color: var(--v3-primary);
}
.inst-detail__block {
  margin-top: var(--v3-space-5);
}
.inst-detail__module-error {
  margin-top: var(--v3-space-4);
}
.inst-detail__footer-meta {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.inst-detail--compact .inst-detail__footer-meta {
  display: none;
}
</style>
