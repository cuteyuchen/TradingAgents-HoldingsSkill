<script setup lang="ts">
import { computed } from 'vue'
import type { SparklinePoint } from './useV3Holdings'

const props = defineProps<{
  points: SparklinePoint[] | null | undefined
  width?: number
  height?: number
}>()

const w = computed(() => props.width ?? 72)
const h = computed(() => props.height ?? 22)

const path = computed(() => {
  const list = props.points
  if (!list || list.length < 2) return null
  const values = list.map((item) => item.close)
  const min = Math.min(...values)
  const max = Math.max(...values)
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null
  const span = max - min || 1
  const step = (w.value - 2) / (values.length - 1)
  return values
    .map((value, index) => {
      const x = 1 + index * step
      const y = h.value - 1 - ((value - min) / span) * (h.value - 2)
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
})

const tone = computed(() => {
  const list = props.points
  if (!list || list.length < 2) return 'flat'
  const first = list[0].close
  const last = list[list.length - 1].close
  if (last > first) return 'up'
  if (last < first) return 'down'
  return 'flat'
})
</script>

<template>
  <span class="spark" data-testid="v3-holding-sparkline">
    <svg
      v-if="path"
      :width="w"
      :height="h"
      :viewBox="`0 0 ${w} ${h}`"
      role="img"
      aria-label="近30日收盘趋势"
    >
      <path :d="path" fill="none" :class="`spark__line spark__line--${tone}`" stroke-width="1.25" />
    </svg>
    <span v-else class="spark__na" data-testid="v3-holding-sparkline-na">—</span>
  </span>
</template>

<style scoped>
.spark {
  display: inline-flex;
  align-items: center;
  min-width: 72px;
}
.spark__line--up { stroke: var(--v3-market-up); }
.spark__line--down { stroke: var(--v3-market-down); }
.spark__line--flat { stroke: var(--v3-market-flat); }
.spark__na {
  color: var(--v3-text-muted);
  font-size: 12px;
}
</style>
