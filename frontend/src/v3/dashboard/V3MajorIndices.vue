<script setup lang="ts">
import { computed } from 'vue'
import V3LoadingState from '../components/V3LoadingState.vue'
import { formatNumber, formatPercentFromPoints } from './dashboard-formatters'
import type { V3IndexCardVM } from './dashboard-types'

const props = defineProps<{
  items: V3IndexCardVM[]
  loading?: boolean
}>()

const emit = defineEmits<{ select: [code: string] }>()

const trendSymbol = computed(() => (item: V3IndexCardVM) => {
  if (item.direction === 'up') return '▲'
  if (item.direction === 'down') return '▼'
  if (item.direction === 'flat') return '—'
  return ''
})
</script>

<template>
  <section class="indices" aria-labelledby="major-indices-title" data-testid="v3-major-indices">
    <header class="indices__header">
      <h2 id="major-indices-title" class="indices__title">六大指数</h2>
      <p class="indices__desc">固定顺序展示，缺项显示不可用，不静默删除。</p>
    </header>
    <V3LoadingState v-if="loading && !items.length" label="加载指数…" />
    <div v-else class="indices__grid">
      <button
        v-for="item in items"
        :key="item.code"
        type="button"
        class="index-card"
        :class="[`index-card--${item.direction}`, { 'index-card--unavailable': item.status !== 'available' }]"
        :data-testid="`v3-index-${item.code}`"
        :aria-label="`${item.name} ${item.code}，点击查看详情`"
        @click="emit('select', item.code)"
      >
        <div class="index-card__top">
          <span class="index-card__name">{{ item.name }}</span>
          <span class="index-card__code">{{ item.code }}</span>
        </div>
        <div class="index-card__price v3-number">
          {{ item.status === 'available' ? formatNumber(item.last, 2) : '—' }}
        </div>
        <div class="index-card__change v3-number">
          <span class="index-card__trend" aria-hidden="true">{{ trendSymbol(item) }}</span>
          <span v-if="item.status === 'available'">
            {{ formatNumber(item.change, 2) }}
            /
            {{ formatPercentFromPoints(item.changePct, 2) }}
          </span>
          <span v-else>不可用</span>
        </div>
        <div class="index-card__meta">
          <span v-if="item.quoteTs" class="index-card__ts">{{ item.quoteTs }}</span>
          <span v-if="item.status !== 'available'" class="index-card__badge">unavailable</span>
          <span v-else-if="item.stale" class="index-card__badge index-card__badge--stale">stale</span>
        </div>
      </button>
    </div>
  </section>
</template>

<style scoped>
.indices__header { margin-bottom: var(--v3-space-3); }
.indices__title { margin: 0; font-size: var(--v3-font-size-xl); font-weight: 700; }
.indices__desc { margin: var(--v3-space-1) 0 0; color: var(--v3-text-muted); font-size: var(--v3-font-size-sm); }
.indices__grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: var(--v3-space-3);
}
.index-card {
  display: grid;
  gap: 4px;
  min-width: 0;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  padding: var(--v3-space-3);
  text-align: left;
  cursor: pointer;
  transition: border-color var(--v3-duration-fast) var(--v3-ease), box-shadow var(--v3-duration-fast) var(--v3-ease);
}
.index-card:hover { border-color: var(--v3-border-strong); box-shadow: var(--v3-shadow-sm); }
.index-card--unavailable { opacity: 0.72; background: var(--v3-surface-muted); }
.index-card__top { display: flex; justify-content: space-between; gap: 6px; min-width: 0; }
.index-card__name { font-size: var(--v3-font-size-sm); font-weight: 700; color: var(--v3-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.index-card__code { color: var(--v3-text-muted); font-size: 10px; font-family: var(--v3-font-mono); }
.index-card__price { font-size: var(--v3-font-size-xl); font-weight: 700; }
.index-card__change { font-size: var(--v3-font-size-sm); display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.index-card--up .index-card__price,
.index-card--up .index-card__change { color: var(--v3-market-up); }
.index-card--down .index-card__price,
.index-card--down .index-card__change { color: var(--v3-market-down); }
.index-card--flat .index-card__price,
.index-card--flat .index-card__change { color: var(--v3-market-flat); }
.index-card__meta { display: flex; flex-wrap: wrap; gap: 6px; color: var(--v3-text-muted); font-size: 10px; }
.index-card__badge {
  border-radius: 999px;
  background: var(--v3-surface-muted);
  padding: 1px 6px;
}
.index-card__badge--stale { background: var(--v3-status-warning-soft); color: var(--v3-status-warning); }
@media (max-width: 1100px) {
  .indices__grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .indices__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
