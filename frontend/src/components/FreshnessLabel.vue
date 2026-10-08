<script setup lang="ts">
import { computed } from 'vue'
import { Clock3 } from 'lucide-vue-next'
import { fmtDateTime } from '../utils/ui'

const props = defineProps<{ freshness?: string | null; at?: string | null }>()
const text = computed(() => String(props.freshness || 'MISSING').toUpperCase())
const label = computed(() => ({ FRESH: '数据正常', STALE: '数据已过期', FROZEN: '使用冻结数据', MISSING: '数据缺失', UNKNOWN: '时效未核对' }[text.value] || '数据状态未知'))
const type = computed(() => text.value === 'FRESH' ? 'success' : ['STALE', 'FROZEN', 'UNKNOWN'].includes(text.value) ? 'warning' : 'error')
</script>

<template>
  <span class="freshness-label">
    <Clock3 :size="13" aria-hidden="true" />
    <n-tag size="small" :type="type" :bordered="false" :data-freshness="text">{{ label }}</n-tag>
    <span v-if="at">{{ fmtDateTime(at) }}</span>
  </span>
</template>

<style scoped>
.freshness-label { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 6px; color: var(--app-text-muted); font-size: 11px; }
</style>
