<script setup lang="ts">
import type { ResearchVerdict, PMFinal } from '../api/types'

defineProps<{
  title: string
  verdict?: ResearchVerdict | PMFinal | null
  accent?: 'blue' | 'green'
}>()

const ratingColor = (rating?: string | null): string => {
  if (!rating) return ''
  return ({
    Buy: 'var(--app-danger)',
    Overweight: 'var(--app-warning)',
    Hold: 'var(--app-text-muted)',
    Underweight: 'var(--app-primary)',
    Sell: 'var(--app-success)',
  }[rating]) || 'var(--app-text-muted)'
}
</script>

<template>
  <div class="verdict-card" :class="accent">
    <div class="vc-head">{{ title }}</div>
    <div v-if="verdict" class="vc-body">
      <div class="vc-row">
        <span class="vc-label">评级</span>
        <span class="vc-rating" :style="{ color: ratingColor(verdict.rating) }">{{ verdict.rating || '—' }}</span>
      </div>
      <div v-if="'winner' in verdict && verdict.winner" class="vc-row">
        <span class="vc-label">胜出方</span>
        <span>{{ verdict.winner === 'bull' ? '多头' : '空头' }}</span>
      </div>
      <div v-if="'rationale' in verdict && verdict.rationale" class="vc-row">
        <span class="vc-label">理由</span>
        <span>{{ verdict.rationale }}</span>
      </div>
      <div v-if="'cash_target' in verdict && verdict.cash_target" class="vc-row">
        <span class="vc-label">现金目标</span>
        <span>{{ verdict.cash_target }}</span>
      </div>
      <div v-if="'confidence' in verdict && verdict.confidence" class="vc-row">
        <span class="vc-label">置信度</span>
        <span>{{ verdict.confidence }}</span>
      </div>
      <div v-if="'priority_notes' in verdict && verdict.priority_notes" class="vc-row">
        <span class="vc-label">备注</span>
        <span>{{ verdict.priority_notes }}</span>
      </div>
    </div>
    <div v-else class="muted">无裁决记录</div>
  </div>
</template>

<style scoped>
.verdict-card {
  border: 1px solid var(--app-border);
  border-left: 4px solid var(--app-primary);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-surface-strong) 82%, transparent);
  padding: 14px 18px;
}

.verdict-card.green {
  border-left-color: var(--app-success);
}

.vc-head {
  margin-bottom: 12px;
  color: var(--app-text);
  font-size: 14px;
  font-weight: 700;
}

.vc-body {
  display: grid;
  gap: 8px;
}

.vc-row {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  font-size: 13px;
  line-height: 1.6;
}

.vc-row span:last-child {
  overflow-wrap: anywhere;
}

.vc-label {
  color: var(--app-text-muted);
  font-weight: 700;
}

.vc-rating {
  font-size: 15px;
  font-weight: 800;
}

@media (max-width: 480px) {
  .vc-row {
    grid-template-columns: 1fr;
    gap: 3px;
  }
}
</style>
