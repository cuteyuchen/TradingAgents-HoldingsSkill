<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  status?: string | null
  label?: string
}>(), { status: 'unknown', label: '' })

const normalized = computed(() => String(props.status || 'unknown').toLowerCase())
const text = computed(() => props.label || ({ ok: '正常', setup: '需要配置', degraded: '数据不完整', error: '异常', unknown: '状态未知' }[normalized.value] || props.status || '状态未知'))
</script>

<template>
  <span class="status-indicator" :class="`status-${normalized}`" :title="String(status || '').toUpperCase()">
    <span class="status-dot" aria-hidden="true" />
    <span>{{ text }}</span>
  </span>
</template>

<style scoped>
.status-indicator { display: inline-flex; align-items: center; gap: 7px; color: var(--text-muted); font-size: 12px; white-space: nowrap; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 14%, transparent); }
.status-ok { color: var(--positive); }.status-setup { color: var(--warning); }.status-degraded, .status-error { color: var(--danger); }
</style>
