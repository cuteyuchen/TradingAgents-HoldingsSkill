<script setup lang="ts">
/** V3 状态徽章：基础语义，不硬编码交易状态 */
import { computed } from 'vue'

export type V3StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'blocked'

const props = withDefaults(
  defineProps<{
    label: string
    tone?: V3StatusTone
    size?: 'sm' | 'md'
  }>(),
  { tone: 'neutral', size: 'sm' },
)

const toneClass = computed(() => `v3-status-badge--${props.tone}`)
</script>

<template>
  <span class="v3-status-badge" :class="[toneClass, `v3-status-badge--${size}`]">
    <span class="v3-status-badge__dot" aria-hidden="true" />
    <span class="v3-status-badge__label">{{ label }}</span>
  </span>
</template>

<style scoped>
.v3-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: var(--v3-radius-pill);
  border: 1px solid transparent;
  font-weight: 600;
  white-space: nowrap;
  line-height: 1;
}
.v3-status-badge--sm { padding: 4px 8px; font-size: var(--v3-font-size-xs); }
.v3-status-badge--md { padding: 6px 10px; font-size: var(--v3-font-size-sm); }
.v3-status-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}

.v3-status-badge--neutral {
  color: var(--v3-text-muted);
  background: var(--v3-surface-muted);
  border-color: var(--v3-border);
}
.v3-status-badge--info {
  color: var(--v3-status-info);
  background: var(--v3-status-info-soft);
  border-color: color-mix(in srgb, var(--v3-status-info) 25%, transparent);
}
.v3-status-badge--success {
  color: var(--v3-status-success);
  background: var(--v3-status-success-soft);
  border-color: color-mix(in srgb, var(--v3-status-success) 25%, transparent);
}
.v3-status-badge--warning {
  color: var(--v3-status-warning);
  background: var(--v3-status-warning-soft);
  border-color: color-mix(in srgb, var(--v3-status-warning) 25%, transparent);
}
.v3-status-badge--danger {
  color: var(--v3-status-danger);
  background: var(--v3-status-danger-soft);
  border-color: color-mix(in srgb, var(--v3-status-danger) 25%, transparent);
}
.v3-status-badge--blocked {
  color: var(--v3-status-blocked);
  background: var(--v3-status-blocked-soft);
  border-color: var(--v3-border-strong);
}
</style>
