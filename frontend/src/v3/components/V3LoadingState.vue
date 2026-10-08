<script setup lang="ts">
/** V3 加载态：优先 skeleton，避免整页 spinner */
withDefaults(
  defineProps<{
    rows?: number
    /** 骨架形态 */
    variant?: 'text' | 'metric' | 'table' | 'card'
    label?: string
  }>(),
  { rows: 3, variant: 'text', label: '加载中' },
)
</script>

<template>
  <div class="v3-loading" data-testid="v3-loading-state" role="status" :aria-label="label">
    <template v-if="variant === 'metric'">
      <div class="v3-loading__metrics">
        <div v-for="i in Math.max(rows, 1)" :key="i" class="v3-loading__metric">
          <div class="v3-skeleton v3-skeleton--label" />
          <div class="v3-skeleton v3-skeleton--value" />
        </div>
      </div>
    </template>
    <template v-else-if="variant === 'table'">
      <div class="v3-loading__table">
        <div class="v3-skeleton v3-skeleton--row v3-skeleton--head" />
        <div v-for="i in Math.max(rows, 1)" :key="i" class="v3-skeleton v3-skeleton--row" />
      </div>
    </template>
    <template v-else-if="variant === 'card'">
      <div class="v3-skeleton v3-skeleton--card" />
    </template>
    <template v-else>
      <div v-for="i in Math.max(rows, 1)" :key="i" class="v3-skeleton v3-skeleton--line" :style="{ width: `${100 - (i % 3) * 12}%` }" />
    </template>
    <span class="v3-loading__sr-only">{{ label }}</span>
  </div>
</template>

<style scoped>
.v3-loading { min-width: 0; }
.v3-loading__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--v3-space-4);
}
.v3-loading__metric { display: grid; gap: var(--v3-space-2); }
.v3-loading__table { display: grid; gap: var(--v3-space-2); }
.v3-skeleton {
  border-radius: var(--v3-radius-sm);
  background: linear-gradient(
    90deg,
    var(--v3-surface-muted) 0%,
    var(--v3-surface-hover) 50%,
    var(--v3-surface-muted) 100%
  );
  background-size: 200% 100%;
  animation: v3-shimmer 1.4s ease-in-out infinite;
}
.v3-skeleton--line { height: 14px; }
.v3-skeleton--label { height: 12px; width: 48%; }
.v3-skeleton--value { height: 22px; width: 72%; }
.v3-skeleton--row { height: 36px; }
.v3-skeleton--head { height: 28px; opacity: 0.7; }
.v3-skeleton--card { height: 160px; }
.v3-loading__sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  border: 0;
}
@keyframes v3-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .v3-skeleton { animation: none; }
}
</style>
