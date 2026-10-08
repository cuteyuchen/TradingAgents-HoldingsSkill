<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  status?: string | null
  label?: string
  size?: 'tiny' | 'small' | 'medium' | 'large'
}>(), { size: 'small' })

const normalized = computed(() => String(props.status || 'UNKNOWN').toUpperCase())
const type = computed<'success' | 'warning' | 'error' | 'info' | 'default'>(() => {
  if (['OK', 'READY', 'ACTIVE', 'SUCCESS', 'COMPLETED', 'FULL', 'FILLED', 'PASS', 'VALID'].includes(normalized.value)) return 'success'
  if (['DEGRADED', 'READY_WITH_WARNINGS', 'STALE', 'PARTIAL', 'PENDING', 'RUNNING', 'DRAFT', 'REVIEW', 'UNKNOWN'].includes(normalized.value)) return 'warning'
  if (['BLOCKED', 'FAILED', 'ERROR', 'DATA_GAP', 'MISSING', 'REJECTED', 'SUPERSEDED', 'EXPIRED'].includes(normalized.value)) return 'error'
  return 'info'
})
const text = computed(() => props.label || normalized.value)
</script>

<template>
  <n-tag :type="type" :size="size" :bordered="false">{{ text }}</n-tag>
</template>
