<script setup lang="ts">
import { computed } from 'vue'
import { Clock3 } from 'lucide-vue-next'
import { fmtDateTime } from '../utils/ui'

const props = defineProps<{ freshness?: string | null; at?: string | null }>()
const text = computed(() => String(props.freshness || 'MISSING').toUpperCase())
const type = computed(() => text.value === 'FRESH' ? 'success' : text.value === 'STALE' ? 'warning' : 'error')
</script>

<template>
  <span class="freshness-label">
    <Clock3 :size="13" aria-hidden="true" />
    <n-tag size="small" :type="type" :bordered="false">{{ text }}</n-tag>
    <span v-if="at">{{ fmtDateTime(at) }}</span>
  </span>
</template>

<style scoped>
.freshness-label { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 6px; color: var(--app-text-muted); font-size: 11px; }
</style>
