<script setup lang="ts">
/**
 * V3 图表容器：只提供壳（title/toolbar/loading/empty/error），
 * 不自造 SVG K 线，后续可接 ECharts / Lightweight Charts。
 */
import V3EmptyState from './V3EmptyState.vue'
import V3ErrorState from './V3ErrorState.vue'
import V3LoadingState from './V3LoadingState.vue'

withDefaults(
  defineProps<{
    title?: string
    loading?: boolean
    empty?: boolean
    errorMessage?: string
    emptyTitle?: string
    emptyDescription?: string
    height?: number
  }>(),
  {
    title: '',
    loading: false,
    empty: false,
    errorMessage: '',
    emptyTitle: '暂无图表数据',
    emptyDescription: '请选择标的或调整时间范围后重试。',
    height: 280,
  },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="v3-chart-container" data-testid="v3-chart-container">
    <div v-if="title || $slots.toolbar" class="v3-chart-container__header">
      <h3 v-if="title" class="v3-chart-container__title">{{ title }}</h3>
      <div v-if="$slots.toolbar" class="v3-chart-container__toolbar"><slot name="toolbar" /></div>
    </div>
    <div class="v3-chart-container__body" :style="{ minHeight: `${height}px` }">
      <V3LoadingState v-if="loading" variant="card" :label="`${title || '图表'}加载中`" />
      <V3ErrorState
        v-else-if="errorMessage"
        :title="`${title || '图表'}加载失败`"
        :description="errorMessage"
        @retry="emit('retry')"
      />
      <V3EmptyState
        v-else-if="empty"
        :title="emptyTitle"
        :description="emptyDescription"
      />
      <slot v-else />
    </div>
  </div>
</template>

<style scoped>
.v3-chart-container {
  min-width: 0;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  overflow: hidden;
}
.v3-chart-container__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--v3-space-3);
  padding: var(--v3-space-3) var(--v3-space-4);
  border-bottom: 1px solid var(--v3-border-subtle);
}
.v3-chart-container__title {
  margin: 0;
  font-size: var(--v3-font-size-md);
  font-weight: 700;
}
.v3-chart-container__toolbar {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
}
.v3-chart-container__body {
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: var(--v3-space-3);
}
</style>
