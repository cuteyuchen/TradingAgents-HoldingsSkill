<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { TimelinePoint } from '../api/types'

const props = defineProps<{ points: TimelinePoint[] }>()

const option = computed(() => {
  const xs = props.points.map((p) => (p.timestamp ? p.timestamp.slice(5, 16) : ''))
  const raw = props.points.map((p) => (p.raw_return != null ? +(p.raw_return * 100).toFixed(2) : null))
  const bench = props.points.map((p) => (p.benchmark_return != null ? +(p.benchmark_return * 100).toFixed(2) : null))
  const alpha = props.points.map((p) => (p.alpha != null ? +(p.alpha * 100).toFixed(2) : null))
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v == null ? '—' : v + '%') },
    textStyle: { color: '#94a3b8' },
    legend: { data: ['标的收益', '沪深300', 'Alpha'], top: 0, textStyle: { color: '#94a3b8' } },
    grid: { left: 48, right: 16, top: 36, bottom: 28 },
    xAxis: { type: 'category', data: xs, axisLabel: { fontSize: 11 }, axisLine: { lineStyle: { color: '#64748b' } } },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%', fontSize: 11 }, splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.22)' } } },
    series: [
      { name: '标的收益', type: 'line', data: raw, smooth: true, itemStyle: { color: '#4e83f0' } },
      { name: '沪深300', type: 'line', data: bench, smooth: true, itemStyle: { color: '#86909c' }, lineStyle: { type: 'dashed' } },
      { name: 'Alpha', type: 'line', data: alpha, smooth: true, itemStyle: { color: '#cf1322' }, areaStyle: { opacity: 0.08 } },
    ],
  }
})
</script>

<template>
  <div>
    <VChart v-if="points.length" :option="option" autoresize style="height: 260px" />
    <div v-else class="muted" style="padding: 24px; text-align: center">无历史数据</div>
  </div>
</template>
