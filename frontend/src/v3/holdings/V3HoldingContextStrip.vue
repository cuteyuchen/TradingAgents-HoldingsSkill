<script setup lang="ts">
import { fmtTime } from '@/utils/ui'

defineProps<{
  snapshotAt: string | null
  quoteAt: string | null
  strategyAt: string | null
  sessionLabel?: string
}>()
</script>

<template>
  <div class="ts-strip" data-testid="v3-holdings-timestamps">
    <span class="ts-strip__item" data-testid="v3-holdings-snapshot-ts">
      <span class="ts-strip__label">持仓</span>
      <span class="ts-strip__value v3-number">{{ snapshotAt ? fmtTime(snapshotAt) : '—' }}</span>
    </span>
    <span class="ts-strip__sep" aria-hidden="true">·</span>
    <span class="ts-strip__item" data-testid="v3-holdings-quote-ts">
      <span class="ts-strip__label">行情</span>
      <span class="ts-strip__value v3-number">{{ quoteAt ? fmtTime(quoteAt) : '—' }}</span>
      <span v-if="sessionLabel" class="ts-strip__state">{{ sessionLabel }}</span>
    </span>
    <span class="ts-strip__sep" aria-hidden="true">·</span>
    <span class="ts-strip__item" data-testid="v3-holdings-strategy-ts">
      <span class="ts-strip__label">策略</span>
      <span class="ts-strip__value v3-number">{{ strategyAt ? fmtTime(strategyAt) : '—' }}</span>
    </span>
  </div>
</template>

<style scoped>
.ts-strip {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.ts-strip__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.ts-strip__value {
  color: var(--v3-text-secondary);
  font-weight: 600;
}
.ts-strip__state {
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface-muted);
  padding: 1px 6px;
  font-size: 10px;
}
.ts-strip__sep { opacity: 0.5; }
</style>
