<script setup lang="ts">
import { computed } from 'vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import type { V3DecisionVM } from './dashboard-types'

const props = defineProps<{
  decision: V3DecisionVM
}>()

const emit = defineEmits<{
  openInstrument: [code: string]
  goAnalysis: []
}>()

const badgeTone = computed(() => {
  switch (props.decision.kind) {
    case 'NO_ACTION':
      return 'info'
    case 'ACTIONABLE':
      return 'warning'
    case 'BLOCKED':
      return 'blocked'
    case 'NO_PORTFOLIO':
      return 'warning'
    default:
      return 'neutral'
  }
})

const kindLabel = computed(() => {
  switch (props.decision.kind) {
    case 'NO_ACTION':
      return 'NO_ACTION'
    case 'ACTIONABLE':
      return 'ACTIONABLE'
    case 'BLOCKED':
      return 'BLOCKED'
    case 'NO_PORTFOLIO':
      return 'NO_PORTFOLIO'
    default:
      return 'MISSING'
  }
})
</script>

<template>
  <section
    class="decision-hero"
    :class="`decision-hero--${decision.kind.toLowerCase().replace('_', '-')}`"
    data-testid="v3-decision-hero"
    :data-decision-kind="decision.kind"
    aria-labelledby="decision-title"
  >
    <div class="decision-hero__main">
      <div class="decision-hero__labels">
        <V3StatusBadge :label="kindLabel" :tone="badgeTone" size="md" data-testid="v3-decision-kind" />
        <span v-if="decision.actionCount > 0" class="decision-hero__count" data-testid="v3-decision-action-count">
          行动 {{ decision.actionCount }} 项
        </span>
      </div>
      <h2 id="decision-title" class="decision-hero__title" data-testid="v3-decision-title">{{ decision.title }}</h2>
      <p class="decision-hero__subtitle" data-testid="v3-decision-subtitle">{{ decision.subtitle }}</p>
      <ul v-if="decision.reasons.length" class="decision-hero__reasons" data-testid="v3-decision-reasons">
        <li v-for="reason in decision.reasons.slice(0, 3)" :key="reason">{{ reason }}</li>
      </ul>
      <div v-if="decision.kind === 'NO_PORTFOLIO'" class="decision-hero__hint">
        市场部分仍可查看；建立组合后再生成组合决策。
      </div>
    </div>
    <div class="decision-hero__side">
      <slot name="actions">
        <button v-if="decision.kind === 'ACTIONABLE' || decision.kind === 'NO_ACTION'" type="button" class="ghost-btn" @click="emit('goAnalysis')">
          查看分析
        </button>
      </slot>
    </div>
  </section>
</template>

<style scoped>
.decision-hero {
  display: flex;
  justify-content: space-between;
  gap: var(--v3-space-4);
  border: 1px solid var(--v3-border);
  border-left-width: 4px;
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  padding: var(--v3-space-5);
}
.decision-hero--no-action {
  border-left-color: var(--v3-status-info);
  background: linear-gradient(90deg, var(--v3-status-info-soft), var(--v3-surface) 48%);
}
.decision-hero--actionable {
  border-left-color: var(--v3-status-warning);
  background: linear-gradient(90deg, var(--v3-status-warning-soft), var(--v3-surface) 48%);
}
.decision-hero--blocked,
.decision-hero--missing {
  border-left-color: var(--v3-status-blocked);
  background: linear-gradient(90deg, var(--v3-status-blocked-soft), var(--v3-surface) 48%);
}
.decision-hero--no-portfolio {
  border-left-color: var(--v3-status-warning);
  background: linear-gradient(90deg, var(--v3-status-warning-soft), var(--v3-surface) 48%);
}
.decision-hero__labels { display: flex; align-items: center; gap: var(--v3-space-2); margin-bottom: var(--v3-space-2); }
.decision-hero__count {
  color: var(--v3-status-warning);
  font-size: var(--v3-font-size-sm);
  font-weight: 700;
}
.decision-hero__title {
  margin: 0;
  font-size: var(--v3-font-size-3xl);
  font-weight: 800;
  line-height: 1.15;
}
.decision-hero--no-action .decision-hero__title { color: var(--v3-status-info); }
.decision-hero--actionable .decision-hero__title { color: var(--v3-status-warning); }
.decision-hero--blocked .decision-hero__title,
.decision-hero--missing .decision-hero__title { color: var(--v3-status-blocked); }
.decision-hero__subtitle {
  margin: var(--v3-space-2) 0 0;
  color: var(--v3-text-secondary);
}
.decision-hero__reasons {
  margin: var(--v3-space-3) 0 0;
  padding-left: 18px;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.decision-hero__hint {
  margin-top: var(--v3-space-3);
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.ghost-btn {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-primary);
  cursor: pointer;
  padding: 8px 12px;
  font-weight: 600;
  white-space: nowrap;
}
@media (max-width: 700px) {
  .decision-hero { flex-direction: column; }
}
</style>
