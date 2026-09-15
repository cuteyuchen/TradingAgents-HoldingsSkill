<script setup lang="ts">
/**
 * Instrument K-line chart using modular Apache ECharts.
 * Lazy: echarts modules load when the component mounts.
 */
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type { BarAdjustment, BarInterval, InstrumentBarsResponse } from '@/api/types'
import {
  calculateMACD,
  calculateRSI,
  calculateSMA,
  closesOf,
  type OhlcBar,
} from './indicators'
import { DASH, formatBarInterval, formatMoneyCNY, formatPrice, formatVolumeShares } from './formatters'
import V3ErrorState from '../components/V3ErrorState.vue'
import type { ModuleError } from './useInstrumentDetail'

const props = defineProps<{
  bars: InstrumentBarsResponse | null
  loading?: boolean
  error: ModuleError | null
  interval: BarInterval
  adjustment?: BarAdjustment
  barLimit: number
  supportedIntervals: BarInterval[]
  supportedAdjustments: BarAdjustment[]
  symbolCode: string
  symbolName: string
}>()

const emit = defineEmits<{
  retry: []
  'update:interval': [value: BarInterval]
  'update:adjustment': [value: BarAdjustment]
  'update:barLimit': [value: number]
}>()

const indicatorMode = ref<'macd' | 'rsi'>('macd')
const chartEl = shallowRef<HTMLDivElement | null>(null)
let chart: { setOption: (o: unknown, n?: boolean) => void; resize: () => void; dispose: () => void } | null = null
let echartsMod: any = null

const intervalOptions = computed(() =>
  (props.supportedIntervals?.length ? props.supportedIntervals : (['1d', '1w', '1M'] as BarInterval[])).map((v) => ({
    value: v as BarInterval,
    label: formatBarInterval(v),
  })),
)

const adjustmentOptions = computed(() => {
  const map: Record<string, string> = { forward: '前复权', none: '不复权', backward: '后复权' }
  return (props.supportedAdjustments || []).map((v) => ({
    value: v as BarAdjustment,
    label: map[v] || v,
  }))
})

const limitOptions = [60, 120, 250, 500]

const barList = computed<OhlcBar[]>(() => {
  const rows = props.bars?.bars || []
  return rows.map((r) => ({
    time: r.time,
    open: r.open,
    high: r.high,
    low: r.low,
    close: r.close,
    volume: r.volume,
    turnover: r.turnover,
  }))
})

const ariaSummary = computed(() => {
  const rows = barList.value
  if (!rows.length) return `${props.symbolName} ${formatBarInterval(props.interval)}：暂无K线数据`
  const first = rows[0]
  const last = rows[rows.length - 1]
  return `${props.symbolName}（${props.symbolCode}）${formatBarInterval(props.interval)} 起止 ${first.time} 至 ${last.time}，最新收盘 ${formatPrice(last.close)}，共 ${rows.length} 根。`
})

function upColor() {
  return getComputedStyle(document.documentElement).getPropertyValue('--v3-market-up').trim() || '#c73535'
}
function downColor() {
  return getComputedStyle(document.documentElement).getPropertyValue('--v3-market-down').trim() || '#17865b'
}

async function ensureEcharts() {
  if (echartsMod) return echartsMod
  const echarts = await import('echarts/core')
  const { CandlestickChart, BarChart, LineChart } = await import('echarts/charts')
  const {
    GridComponent,
    TooltipComponent,
    AxisPointerComponent,
    DataZoomComponent,
    LegendComponent,
  } = await import('echarts/components')
  const { CanvasRenderer } = await import('echarts/renderers')
  echarts.use([
    CandlestickChart,
    BarChart,
    LineChart,
    GridComponent,
    TooltipComponent,
    AxisPointerComponent,
    DataZoomComponent,
    LegendComponent,
    CanvasRenderer,
  ])
  echartsMod = echarts
  return echarts
}

function buildOption() {
  const rows = barList.value
  const xs = rows.map((r) => r.time)
  const closes = closesOf(rows)
  const ma5 = calculateSMA(closes, 5)
  const ma10 = calculateSMA(closes, 10)
  const ma20 = calculateSMA(closes, 20)
  const ma60 = calculateSMA(closes, 60)
  const volumes = rows.map((r) => r.volume)
  const macd = calculateMACD(closes.map((c) => (Number.isFinite(c) ? c : 0)))
  const rsi = calculateRSI(closes.map((c) => (Number.isFinite(c) ? c : 0)), 14)

  const kData = rows.map((r) => [r.open, r.close, r.low, r.high])
  const volumeColors = rows.map((r) => (r.close >= r.open ? upColor() : downColor()))

  const grids = indicatorMode.value === 'macd' || indicatorMode.value === 'rsi'
    ? [
        { left: 56, right: 16, top: 28, height: '48%' },
        { left: 56, right: 16, top: '62%', height: '12%' },
        { left: 56, right: 16, top: '78%', height: '14%' },
      ]
    : [
        { left: 56, right: 16, top: 28, height: '72%' },
        { left: 56, right: 16, top: '78%', height: '14%' },
      ]

  const xAxes = grids.map((_, i) => ({
    type: 'category',
    data: xs,
    gridIndex: i,
    axisLabel: { show: i === grids.length - 1, hideOverlap: true },
    axisLine: { lineStyle: { color: '#999' } },
  }))

  const yAxes = grids.map((_, i) => ({
    type: 'value',
    gridIndex: i,
    scale: true,
    splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' } },
  }))

  const series: any[] = [
    {
      name: 'K线',
      type: 'candlestick',
      data: kData,
      xAxisIndex: 0,
      yAxisIndex: 0,
      itemStyle: {
        color: upColor(),
        color0: downColor(),
        borderColor: upColor(),
        borderColor0: downColor(),
      },
    },
    {
      name: 'MA5',
      type: 'line',
      data: ma5,
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 1 },
    },
    {
      name: 'MA10',
      type: 'line',
      data: ma10,
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      lineStyle: { width: 1 },
    },
    {
      name: 'MA20',
      type: 'line',
      data: ma20,
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      lineStyle: { width: 1 },
    },
    {
      name: 'MA60',
      type: 'line',
      data: ma60,
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      lineStyle: { width: 1 },
    },
    {
      name: '成交量',
      type: 'bar',
      data: volumes,
      xAxisIndex: 1,
      yAxisIndex: 1,
      itemStyle: {
        color: (p: any) => volumeColors[p.dataIndex] || '#999',
      },
    },
  ]

  if (indicatorMode.value === 'macd') {
    series.push(
      {
        name: 'DIF',
        type: 'line',
        data: macd.dif,
        xAxisIndex: 2,
        yAxisIndex: 2,
        showSymbol: false,
        lineStyle: { width: 1 },
      },
      {
        name: 'DEA',
        type: 'line',
        data: macd.dea,
        xAxisIndex: 2,
        yAxisIndex: 2,
        showSymbol: false,
        lineStyle: { width: 1 },
      },
      {
        name: 'MACD',
        type: 'bar',
        data: macd.hist,
        xAxisIndex: 2,
        yAxisIndex: 2,
        itemStyle: {
          color: (p: any) => {
            const v = p.value as number | null
            if (v == null) return '#999'
            return v >= 0 ? upColor() : downColor()
          },
        },
      },
    )
  } else {
    series.push({
      name: 'RSI14',
      type: 'line',
      data: rsi,
      xAxisIndex: 2,
      yAxisIndex: 2,
      showSymbol: false,
      lineStyle: { width: 1.2 },
      areaStyle: { opacity: 0.06 },
    })
  }

  return {
    animation: false,
    legend: {
      data: indicatorMode.value === 'macd'
        ? ['K线', 'MA5', 'MA10', 'MA20', 'MA60', 'DIF', 'DEA', 'MACD']
        : ['K线', 'MA5', 'MA10', 'MA20', 'MA60', 'RSI14'],
      top: 0,
      textStyle: { fontSize: 11 },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params: any) => {
        const list = Array.isArray(params) ? params : [params]
        const time = list[0]?.name || ''
        const lines: string[] = [`<div><b>${time}</b></div>`]
        for (const item of list) {
          if (item.seriesName === 'K线') {
            const v = item.data as number[]
            lines.push(`开 ${formatPrice(v?.[0])}`)
            lines.push(`高 ${formatPrice(v?.[1] != null && Array.isArray(v) ? v[3] : v?.[1])}`)
            // candlestick data is [open, close, low, high] in echarts
            const o = Array.isArray(item.data) ? item.data : []
            lines[lines.length - 1] = `开 ${formatPrice(o[0])}`
            lines.push(`收 ${formatPrice(o[1])}`)
            lines.push(`低 ${formatPrice(o[2])}`)
            lines.push(`高 ${formatPrice(o[3])}`)
          } else if (item.seriesName === '成交量') {
            lines.push(`量 ${formatVolumeShares(item.value as number)}`)
          } else if (item.seriesName === 'MACD') {
            lines.push(`柱 ${formatPrice(item.value as number, 4)}`)
          } else if (item.seriesName !== 'MA5' && item.seriesName !== 'MA10' && item.seriesName !== 'MA20' && item.seriesName !== 'MA60') {
            lines.push(`${item.seriesName} ${formatPrice(item.value as number, item.seriesName?.startsWith('RSI') ? 2 : 4)}`)
          } else {
            lines.push(`${item.seriesName} ${formatPrice(item.value as number)}`)
          }
        }
        // turnover from original bar
        const bar = rows.find((r) => r.time === time)
        if (bar) {
          lines.push(`额 ${bar.turnover == null ? DASH : formatMoneyCNY(bar.turnover)}`)
        }
        return lines.join('<br/>')
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], start: 40, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2], start: 40, end: 100, height: 16, bottom: 8 },
    ],
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    series,
  }
}

async function render() {
  if (!chartEl.value) return
  const echarts = await ensureEcharts()
  if (!chart) chart = echarts.init(chartEl.value)
  chart?.setOption(buildOption(), true)
}

function onResize() {
  chart?.resize()
}

watch(
  () => [props.bars, indicatorMode.value, props.interval, props.adjustment],
  () => {
    if (!barList.value.length) return
    void render()
  },
)

watch(indicatorMode, () => void render())

onMounted(() => {
  window.addEventListener('resize', onResize)
  if (barList.value.length) void render()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="kline" data-testid="instrument-kline">
    <div class="kline__toolbar" role="toolbar" aria-label="K线工具栏" data-testid="kline-toolbar">
      <div class="kline__group" role="group" aria-label="周期">
        <button
          v-for="opt in intervalOptions"
          :key="opt.value"
          type="button"
          class="kline__chip"
          :class="{ 'kline__chip--active': interval === opt.value }"
          :data-testid="`kline-interval-${opt.value}`"
          :aria-pressed="interval === opt.value"
          @click="emit('update:interval', opt.value)"
        >{{ opt.label }}</button>
      </div>
      <div v-if="adjustmentOptions.length" class="kline__group" role="group" aria-label="复权">
        <button
          v-for="opt in adjustmentOptions"
          :key="opt.value"
          type="button"
          class="kline__chip"
          :class="{ 'kline__chip--active': adjustment === opt.value }"
          :data-testid="`kline-adjustment-${opt.value}`"
          :aria-pressed="adjustment === opt.value"
          @click="emit('update:adjustment', opt.value)"
        >{{ opt.label }}</button>
      </div>
      <div class="kline__group" role="group" aria-label="根数">
        <button
          v-for="n in limitOptions"
          :key="n"
          type="button"
          class="kline__chip"
          :class="{ 'kline__chip--active': barLimit === n }"
          :data-testid="`kline-limit-${n}`"
          :aria-pressed="barLimit === n"
          @click="emit('update:barLimit', n)"
        >{{ n }}</button>
      </div>
      <div class="kline__group" role="group" aria-label="副图指标">
        <button
          type="button"
          class="kline__chip"
          :class="{ 'kline__chip--active': indicatorMode === 'macd' }"
          data-testid="kline-indicator-macd"
          :aria-pressed="indicatorMode === 'macd'"
          @click="indicatorMode = 'macd'"
        >MACD</button>
        <button
          type="button"
          class="kline__chip"
          :class="{ 'kline__chip--active': indicatorMode === 'rsi' }"
          data-testid="kline-indicator-rsi"
          :aria-pressed="indicatorMode === 'rsi'"
          @click="indicatorMode = 'rsi'"
        >RSI14</button>
      </div>
    </div>

    <p class="kline__aria" data-testid="kline-aria-summary" role="status">{{ ariaSummary }}</p>

    <V3ErrorState
      v-if="error"
      :title="error.title"
      :description="error.description"
      data-testid="kline-error"
      @retry="emit('retry')"
    />
    <div v-else-if="loading && !bars" class="kline__skeleton" data-testid="kline-loading" aria-busy="true" />
    <div
      v-else-if="barList.length"
      ref="chartEl"
      class="kline__chart"
      style="height: 520px"
      role="img"
      :aria-label="ariaSummary"
      data-testid="kline-chart"
    />
    <p v-else class="kline__empty" data-testid="kline-empty">当前条件下没有K线数据。</p>
  </div>
</template>

<style scoped>
.kline {
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}
.kline__toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--v3-space-3);
  margin-bottom: var(--v3-space-2);
}
.kline__group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.kline__chip {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-pill);
  background: var(--v3-surface);
  color: var(--v3-text-secondary);
  padding: 4px 10px;
  font-size: var(--v3-font-size-xs);
  font-weight: 600;
  cursor: pointer;
}
.kline__chip--active {
  border-color: var(--v3-primary);
  background: var(--v3-primary-soft);
  color: var(--v3-primary);
}
.kline__aria {
  margin: 0 0 var(--v3-space-2);
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.kline__chart {
  width: 100%;
  min-height: 480px;
}
.kline__skeleton {
  height: 480px;
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface-muted);
}
.kline__empty {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
  padding: var(--v3-space-6) 0;
  text-align: center;
}
</style>
