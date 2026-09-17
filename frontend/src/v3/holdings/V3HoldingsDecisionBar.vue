<script setup lang="ts">
import V3StatusBadge from '../components/V3StatusBadge.vue'
import type { V3HoldingsDecisionBarVM } from './holdings-types'

defineProps<{ decision: V3HoldingsDecisionBarVM }>()
</script>

<template>
  <section
    class="decision"
    :class="`decision--${decision.kind.toLowerCase()}`"
    data-testid="v3-holdings-decision-bar"
  >
    <div class="decision__main">
      <h2 class="decision__title" data-testid="v3-holdings-decision-title">{{ decision.title }}</h2>
      <p class="decision__subtitle" data-testid="v3-holdings-decision-subtitle">{{ decision.subtitle }}</p>
      <p v-if="decision.quality" class="decision__quality">质量 {{ decision.quality }}</p>
    </div>
    <div class="decision__metrics">
      <div class="decision__metric">
        <span>需行动</span>
        <strong data-testid="v3-holdings-actionable-count">{{ decision.actionableCount }}</strong>
      </div>
      <div class="decision__metric">
        <span>条件观察</span>
        <strong data-testid="v3-holdings-conditional-count">{{ decision.conditionalCount }}</strong>
      </div>
      <div class="decision__metric">
        <span>目标范围</span>
        <strong data-testid="v3-holdings-target-range">{{ decision.targetRangeText || '—' }}</strong>
      </div>
    </div>
    <div v-if="decision.riskFlags.length" class="decision__risks">
      <V3StatusBadge
        v-for="flag in decision.riskFlags"
        :key="flag"
        :label="flag"
        tone="warning"
      />
    </div>
  </section>
</template>

<style scoped>
.decision {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--v3-space-3);
  border: 1px solid var(--v3-border);
  border-left: 3px solid var(--v3-primary);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  padding: var(--v3-space-4);
}
.decision--blocked,
.decision--stale_snapshot {
  border-left-color: var(--v3-status-warning);
}
.decision--actionable {
  border-left-color: var(--v3-status-danger);
}
.decision__title {
  margin: 0;
  font-size: var(--v3-font-size-lg);
  font-weight: 700;
}
.decision__subtitle,
.decision__quality {
  margin: 4px 0 0;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.decision__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: var(--v3-space-4);
}
.decision__metric {
  display: grid;
  gap: 2px;
  min-width: 72px;
}
.decision__metric span {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.decision__metric strong {
  font-size: var(--v3-font-size-xl);
  font-variant-numeric: tabular-nums;
}
.decision__risks {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
