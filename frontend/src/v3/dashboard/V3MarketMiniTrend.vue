<script setup lang="ts">
/** Lightweight native SVG sparkline — no ECharts on dashboard initial chunk. */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    values: number[]
    width?: number
    height?: number
    label?: string
  }>(),
  { width: 120, height: 28, label: '趋势' },
)

const points = computed(() => {
  if (props.values.length < 2) return ''
  const min = Math.min(...props.values)
  const max = Math.max(...props.values)
  const span = max - min || 1
  const pad = 2
  return props.values
    .map((value, index) => {
      const x = pad + (index / (props.values.length - 1)) * (props.width - pad * 2)
      const y = props.height - pad - ((value - min) / span) * (props.height - pad * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

const ariaSummary = computed(() => {
  if (props.values.length < 2) return `${props.label}：数据不足`
  const first = props.values[0]
  const last = props.values[props.values.length - 1]
  const direction = last > first ? '上升' : last < first ? '下降' : '持平'
  return `${props.label}：从 ${first} 到 ${last}，整体${direction}`
})
</script>

<template>
  <svg
    v-if="values.length >= 2"
    class="mini-trend"
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    role="img"
    :aria-label="ariaSummary"
    data-testid="v3-market-mini-trend"
  >
    <polyline :points="points" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
  </svg>
  <span v-else class="mini-trend__empty" data-testid="v3-market-mini-trend-empty">—</span>
</template>

<style scoped>
.mini-trend { color: var(--v3-primary); display: block; }
.mini-trend__empty { color: var(--v3-text-muted); }
</style>
