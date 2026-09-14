<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type { CapitalFlow, CapitalFlowSnapshot } from '@/api/types'
import { DASH, formatMoneyCNY, marketTrend } from './formatters'
import V3EmptyState from '../components/V3EmptyState.vue'
import V3ErrorState from '../components/V3ErrorState.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import type { ModuleError } from './useInstrumentDetail'

const props = defineProps<{
  capitalFlow: CapitalFlow | null
  loading?: boolean
  error: ModuleError | null
  supported: boolean
}>()

const emit = defineEmits<{ retry: [] }>()

type FlowKey = 'main_net_inflow' | 'super_large_net_inflow' | 'large_net_inflow' | 'medium_net_inflow' | 'small_net_inflow'
type HistoryMetric = 'main' | 'super_large' | 'large' | 'medium' | 'small'

const historyMetric = ref<HistoryMetric>('main')
const chartEl = shallowRef<HTMLDivElement | null>(null)
let chart: { setOption: (o: unknown, n?: boolean) => void; resize: () => void; dispose: () => void } | null = null

const flowLabels: Record<FlowKey, string> = {
  main_net_inflow: '主力',
  super_large_net_inflow: '超大单',
  large_net_inflow: '大单',
  medium_net_inflow: '中单',
  small_net_inflow: '小单',
}

const metricToField: Record<HistoryMetric, FlowKey> = {
  main: 'main_net_inflow',
  super_large: 'super_large_net_inflow',
  large: 'large_net_inflow',
  medium: 'medium_net_inflow',
  small: 'small_net_inflow',
}

const currentCards = computed(() => {
  const c = props.capitalFlow?.current
  const keys: FlowKey[] = [
    'main_net_inflow',
    'super_large_net_inflow',
    'large_net_inflow',
    'medium_net_inflow',
    'small_net_inflow',
  ]
  return keys.map((key) => {
    const value = c?.[key] ?? null
    return {
      key,
      label: flowLabels[key],
      value: formatMoneyCNY(value),
      trend: marketTrend(value),
      raw: value,
    }
  })
})

const unsupported = computed(() => !props.supported || props.capitalFlow?.status === 'unsupported')
const status = computed(() => props.capitalFlow?.status ?? null)
const empty = computed(() => status.value === 'empty')
const unavailable = computed(() => status.value === 'unavailable' && !props.capitalFlow?.current)

async function ensureChart() {
  if (!chartEl.value) return
  const echarts = await import('echarts/core')
  const { BarChart } = await import('echarts/charts')
  const { GridComponent, TooltipComponent, AxisPointerComponent, DataZoomComponent } = await import('echarts/components')
  const { CanvasRenderer } = await import('echarts/renderers')
  echarts.use([BarChart, GridComponent, TooltipComponent, AxisPointerComponent, DataZoomComponent, CanvasRenderer])
  if (!chart) {
    chart = echarts.init(chartEl.value)
  }
  renderChart()
}

function renderChart() {
  if (!chart) return
  const history = props.capitalFlow?.history || []
  const field = metricToField[historyMetric.value]
  const xs = history.map((h) => h.time)
  const ys = history.map((h) => (h[field] == null ? null : h[field]))
  chart.setOption(
    {
      animation: false,
      grid: { left: 64, right: 16, top: 24, bottom: 40 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: any) => {
          const item = Array.isArray(params) ? params[0] : params
          const label = flowLabels[field]
          return `${item.name}<br/>${label}: ${formatMoneyCNY(item.value as number)}`
        },
      },
      xAxis: { type: 'category', data: xs, axisLabel: { hideOverlap: true } },
      yAxis: {
        type: 'value',
        axisLabel: {
          formatter: (v: number) => {
            if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(1)}亿`
            if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}万`
            return String(v)
          },
        },
      },
      series: [
        {
          type: 'bar',
          data: ys,
          itemStyle: {
            color: (p: any) => {
              const v = p.value as number | null
              if (v == null) return '#999'
              if (v > 0) return getComputedStyle(document.documentElement).getPropertyValue('--v3-market-up').trim() || '#c73535'
              if (v < 0) return getComputedStyle(document.documentElement).getPropertyValue('--v3-market-down').trim() || '#17865b'
              return getComputedStyle(document.documentElement).getPropertyValue('--v3-market-flat').trim() || '#66727f'
            },
          },
        },
      ],
      dataZoom: [{ type: 'inside', start: 50, end: 100 }],
    },
    true,
  )
}

function onResize() {
  chart?.resize()
}

watch(historyMetric, () => {
  renderChart()
})

watch(
  () => props.capitalFlow,
  async () => {
    await nextTick()
    if (chartEl.value && props.capitalFlow?.history?.length) void ensureChart()
    else renderChart()
  },
  { deep: false },
)

onMounted(() => {
  window.addEventListener('resize', onResize)
  if (props.capitalFlow?.history?.length) void ensureChart()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})

const historyMetrics: { name: HistoryMetric; label: string }[] = [
  { name: 'main', label: '主力' },
  { name: 'super_large', label: '超大单' },
  { name: 'large', label: '大单' },
  { name: 'medium', label: '中单' },
  { name: 'small', label: '小单' },
]
</script>

<template>
  <div class="flow" data-testid="instrument-capital-flow">
    <div class="flow__header">
      <h3>资金流</h3>
      <V3StatusBadge v-if="capitalFlow?.status === 'stale'" label="可能过期" tone="warning" data-testid="flow-stale" />
      <V3StatusBadge v-if="capitalFlow?.fallback" label="备用数据源" tone="info" data-testid="flow-fallback" />
    </div>

    <p class="flow__disclaimer" data-testid="flow-disclaimer">
      资金流为数据提供方分类估算，不代表交易所确认的投资者身份或 Level-2 真值。
      <span v-if="capitalFlow?.provider_derived" data-testid="flow-provider-derived"> provider_derived · {{ capitalFlow.methodology }}</span>
    </p>

    <V3EmptyState
      v-if="unsupported"
      title="当前标的/数据源不支持资金流"
      description="不会请求资金流接口。指数与部分 ETF 默认不支持。"
      data-testid="flow-unsupported"
    />
    <V3EmptyState
      v-else-if="empty"
      title="当前没有资金流数据"
      description="数据源返回空观测。"
      data-testid="flow-empty"
    />
    <V3EmptyState
      v-else-if="unavailable"
      title="数据源当前无法提供资金流"
      description="请稍后重试。"
      data-testid="flow-unavailable"
    />
    <V3ErrorState
      v-else-if="error"
      :title="error.title"
      :description="error.description"
      data-testid="flow-error"
      @retry="emit('retry')"
    />
    <div v-else-if="loading && !capitalFlow" class="flow__skeleton" data-testid="flow-loading" aria-busy="true">
      <span v-for="i in 5" :key="i" class="flow__sk-card" />
    </div>
    <template v-else-if="capitalFlow">
      <div class="flow__cards" data-testid="flow-cards">
        <div v-for="card in currentCards" :key="card.key" class="flow__card" :data-testid="`flow-${card.key}`">
          <span class="flow__label">{{ card.label }}</span>
          <strong class="v3-number" :class="`trend-${card.trend}`">{{ card.value }}</strong>
          <span class="flow__sign">正=净流入 负=净流出 · CNY</span>
        </div>
      </div>

      <div v-if="capitalFlow.history?.length" class="flow__history">
        <div class="flow__history-toolbar" role="toolbar" aria-label="资金流历史指标">
          <button
            v-for="m in historyMetrics"
            :key="m.name"
            type="button"
            class="flow__chip"
            :class="{ 'flow__chip--active': historyMetric === m.name }"
            :data-testid="`flow-history-${m.name}`"
            :aria-pressed="historyMetric === m.name"
            @click="historyMetric = m.name"
          >{{ m.label }}</button>
        </div>
        <div
          ref="chartEl"
          class="flow__chart"
          style="height: 240px"
          role="img"
          :aria-label="`资金流历史：${capitalFlow.instrument.code} ${historyMetric} 指标柱状图`"
          data-testid="flow-history-chart"
        />
      </div>
      <p v-else class="flow__no-history">暂无资金流历史序列。</p>
    </template>
  </div>
</template>

<style scoped>
.flow__header {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
  margin-bottom: var(--v3-space-2);
}
.flow__header h3 {
  margin: 0;
  font-size: var(--v3-font-size-lg);
  font-weight: 700;
}
.flow__disclaimer {
  margin: 0 0 var(--v3-space-3);
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
  line-height: 1.5;
}
.flow__cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--v3-space-3);
  margin-bottom: var(--v3-space-4);
}
.flow__card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--v3-space-3);
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
}
.flow__label {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.flow__card strong {
  font-size: var(--v3-font-size-xl);
  font-weight: 700;
}
.flow__sign {
  color: var(--v3-text-muted);
  font-size: 10px;
}
.flow__history-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--v3-space-2);
  margin-bottom: var(--v3-space-2);
}
.flow__chip {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-pill);
  background: var(--v3-surface);
  color: var(--v3-text-secondary);
  padding: 4px 12px;
  font-size: var(--v3-font-size-xs);
  font-weight: 600;
  cursor: pointer;
}
.flow__chip--active {
  border-color: var(--v3-primary);
  background: var(--v3-primary-soft);
  color: var(--v3-primary);
}
.flow__chart {
  width: 100%;
  min-height: 240px;
}
.flow__no-history {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.flow__skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--v3-space-3);
}
.flow__sk-card {
  height: 72px;
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface-muted);
}
.trend-up { color: var(--v3-market-up); }
.trend-down { color: var(--v3-market-down); }
.trend-flat { color: var(--v3-market-flat); }
</style>
