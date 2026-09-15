<script setup lang="ts">
import V3EmptyState from '../components/V3EmptyState.vue'
import type { V3ActionRowVM } from './dashboard-types'

defineProps<{
  holdingActions: V3ActionRowVM[]
  candidateActions: V3ActionRowVM[]
  kind: string
}>()

const emit = defineEmits<{ openInstrument: [code: string] }>()
</script>

<template>
  <div class="actions" data-testid="v3-action-list">
    <div v-if="holdingActions.length" class="actions__block">
      <h3 class="actions__title">持仓操作</h3>
      <ul class="actions__list" data-testid="v3-holding-actions">
        <li v-for="(row, index) in holdingActions" :key="`${row.code || 'x'}-${index}`" class="actions__row">
          <button
            v-if="row.code"
            type="button"
            class="actions__code"
            :data-testid="`v3-action-code-${row.code}`"
            @click="emit('openInstrument', row.code)"
          >
            {{ row.code }}
          </button>
          <span v-else class="actions__code">—</span>
          <span class="actions__name">{{ row.name || '—' }}</span>
          <span class="actions__action">{{ row.action || '—' }}</span>
          <span class="actions__reason">{{ row.reason || row.stage || '' }}</span>
        </li>
      </ul>
    </div>
    <div v-if="candidateActions.length" class="actions__block">
      <h3 class="actions__title">新机会</h3>
      <ul class="actions__list" data-testid="v3-candidate-actions">
        <li v-for="(row, index) in candidateActions" :key="`${row.code || 'c'}-${index}`" class="actions__row">
          <button
            v-if="row.code"
            type="button"
            class="actions__code"
            @click="emit('openInstrument', row.code)"
          >
            {{ row.code }}
          </button>
          <span v-else class="actions__code">—</span>
          <span class="actions__name">{{ row.name || '—' }}</span>
          <span class="actions__action">{{ row.action || '—' }}</span>
          <span class="actions__reason">{{ row.reason || row.stage || '' }}</span>
        </li>
      </ul>
    </div>
    <V3EmptyState
      v-if="!holdingActions.length && !candidateActions.length"
      title="暂无结构化行动行"
      :description="kind === 'ACTIONABLE' ? '结论需要行动，但未暴露具体持仓/候选行。' : '今日没有持仓操作或新机会。'"
      data-testid="v3-action-empty"
    />
  </div>
</template>

<style scoped>
.actions__block + .actions__block { margin-top: var(--v3-space-4); }
.actions__title { margin: 0 0 var(--v3-space-2); font-size: var(--v3-font-size-md); font-weight: 700; }
.actions__list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--v3-space-2); }
.actions__row {
  display: grid;
  grid-template-columns: 110px 1fr 80px 1.4fr;
  gap: var(--v3-space-2);
  align-items: center;
  border: 1px solid var(--v3-border-subtle);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface-muted);
  padding: var(--v3-space-2) var(--v3-space-3);
  font-size: var(--v3-font-size-sm);
}
.actions__code {
  border: 0;
  background: none;
  color: var(--v3-primary);
  cursor: pointer;
  font-family: var(--v3-font-mono);
  font-weight: 700;
  text-align: left;
  padding: 0;
}
.actions__action { font-weight: 700; color: var(--v3-status-warning); }
.actions__reason { color: var(--v3-text-muted); }
@media (max-width: 700px) {
  .actions__row { grid-template-columns: 1fr 1fr; }
}
</style>
