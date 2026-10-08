<script setup lang="ts">
withDefaults(defineProps<{
  label: string
  value?: string | number
  helper?: string
  tone?: 'default' | 'positive' | 'negative' | 'risk'
}>(), { value: '—', helper: '', tone: 'default' })
</script>

<template>
  <div class="metric-tile" :class="`metric-${tone}`">
    <span>{{ label }}</span>
    <strong class="mono-number">{{ value ?? '—' }}</strong>
    <small v-if="helper">{{ helper }}</small>
  </div>
</template>

<style scoped>
.metric-tile { display: grid; min-width: 0; gap: 4px; border-left: 2px solid var(--border-strong); padding: 2px 0 2px 12px; }
.metric-tile > span, .metric-tile > small { color: var(--text-muted); font-size: 12px; }
.metric-tile strong { overflow-wrap: anywhere; font-size: 20px; line-height: 1.2; }
.metric-tile small { min-height: 18px; }
.metric-positive { border-left-color: var(--positive); }.metric-positive strong { color: var(--positive); }
.metric-negative { border-left-color: var(--negative); }.metric-negative strong { color: var(--negative); }
.metric-risk { border-left-color: var(--risk-high); }.metric-risk strong { color: var(--risk-high); }
</style>
