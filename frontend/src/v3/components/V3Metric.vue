<script setup lang="ts">
/**
 * V3 指标块
 * trend up/down 使用 market-up/down（A 股红涨绿跌），不是 success/error
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    label: string
    value?: string | number | null
    secondary?: string
    /** 'up' | 'down' | 'flat' — 走 market token */
    trend?: 'up' | 'down' | 'flat' | null
    /** 业务 status tone — 走 status token，与涨跌分离 */
    status?: 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'blocked' | null
  }>(),
  { value: null, secondary: '', trend: null, status: null },
)

const displayValue = computed(() => (props.value === null || props.value === undefined || props.value === '' ? '—' : String(props.value)))

const trendClass = computed(() => {
  if (props.trend === 'up') return 'v3-metric__value--market-up'
  if (props.trend === 'down') return 'v3-metric__value--market-down'
  if (props.trend === 'flat') return 'v3-metric__value--market-flat'
  if (props.status === 'info') return 'v3-metric__value--status-info'
  if (props.status === 'success') return 'v3-metric__value--status-success'
  if (props.status === 'warning') return 'v3-metric__value--status-warning'
  if (props.status === 'danger') return 'v3-metric__value--status-danger'
  if (props.status === 'blocked') return 'v3-metric__value--status-blocked'
  return ''
})

const accentClass = computed(() => {
  if (props.trend === 'up') return 'v3-metric--accent-market-up'
  if (props.trend === 'down') return 'v3-metric--accent-market-down'
  if (props.trend === 'flat') return 'v3-metric--accent-market-flat'
  if (props.status === 'danger') return 'v3-metric--accent-status-danger'
  if (props.status === 'warning') return 'v3-metric--accent-status-warning'
  if (props.status === 'success') return 'v3-metric--accent-status-success'
  if (props.status === 'info') return 'v3-metric--accent-status-info'
  return ''
})
</script>

<template>
  <div class="v3-metric" :class="accentClass">
    <span class="v3-metric__label">{{ label }}</span>
    <strong class="v3-metric__value v3-number" :class="trendClass">{{ displayValue }}</strong>
    <small v-if="secondary" class="v3-metric__secondary v3-number">{{ secondary }}</small>
  </div>
</template>

<style scoped>
.v3-metric {
  display: grid;
  min-width: 0;
  gap: var(--v3-space-1);
  border-left: 2px solid var(--v3-border-strong);
  padding: var(--v3-space-1) 0 var(--v3-space-1) var(--v3-space-3);
}
.v3-metric__label,
.v3-metric__secondary {
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.v3-metric__value {
  overflow-wrap: anywhere;
  font-size: var(--v3-font-size-2xl);
  line-height: 1.2;
  font-weight: 700;
}
.v3-metric__secondary { min-height: 18px; }

/* A 股涨跌 */
.v3-metric__value--market-up { color: var(--v3-market-up); }
.v3-metric__value--market-down { color: var(--v3-market-down); }
.v3-metric__value--market-flat { color: var(--v3-market-flat); }
/* 业务状态（与涨跌分离） */
.v3-metric__value--status-info { color: var(--v3-status-info); }
.v3-metric__value--status-success { color: var(--v3-status-success); }
.v3-metric__value--status-warning { color: var(--v3-status-warning); }
.v3-metric__value--status-danger { color: var(--v3-status-danger); }
.v3-metric__value--status-blocked { color: var(--v3-status-blocked); }

.v3-metric--accent-market-up { border-left-color: var(--v3-market-up); }
.v3-metric--accent-market-down { border-left-color: var(--v3-market-down); }
.v3-metric--accent-market-flat { border-left-color: var(--v3-market-flat); }
.v3-metric--accent-status-info { border-left-color: var(--v3-status-info); }
.v3-metric--accent-status-success { border-left-color: var(--v3-status-success); }
.v3-metric--accent-status-warning { border-left-color: var(--v3-status-warning); }
.v3-metric--accent-status-danger { border-left-color: var(--v3-status-danger); }
</style>
