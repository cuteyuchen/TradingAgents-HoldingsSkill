<script setup lang="ts">
import { computed } from 'vue'
import { Clock3 } from 'lucide-vue-next'
import { fmtDateTime } from '../utils/ui'

const props = defineProps<{ freshness?: string | null; at?: string | null }>()
const text = computed(() => String(props.freshness || 'MISSING').toUpperCase())
const label = computed(() => ({ FRESH: '数据正常', STALE: '数据稍旧', FROZEN: '使用冻结数据', MISSING: '数据缺失' }[text.value] || '数据状态未知'))
const type = computed(() => text.value === 'FRESH' ? 'success' : text.value === 'STALE' || text.value === 'FROZEN' ? 'warning' : 'error')
</script>

<template>
  <span class="freshness-label">
    <Clock3 :size="13" aria-hidden="true" />
    <n-tag size="small" :type="type" :bordered="false" :title="text">{{ label }}</n-tag>
    <span v-if="at">{{ fmtDateTime(at) }}</span>
  </span>
</template>

<style scoped>
.freshness-label { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 6px; color: var(--app-text-muted); font-size: 11px; }
</style>
