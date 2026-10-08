<script setup lang="ts">
import { computed } from 'vue'
import { systemStatusLabel } from '../utils/systemLocale'

const props = withDefaults(defineProps<{
  status?: string | null
  label?: string
  size?: 'tiny' | 'small' | 'medium' | 'large'
}>(), { size: 'small' })

const normalized = computed(() => String(props.status || 'UNKNOWN').trim().toUpperCase())
const type = computed<'success' | 'warning' | 'error' | 'info' | 'default'>(() => {
  if (['OK', 'READY', 'ACTIVE', 'SUCCESS', 'SUCCEEDED', 'COMPLETED', 'FULL', 'FILLED', 'PASS', 'VALID', 'CONFIRMED', 'APPROVED', 'RESOLVED'].includes(normalized.value)) return 'success'
  if (['DEGRADED', 'READY_WITH_WARNINGS', 'STALE', 'PARTIAL', 'PENDING', 'RUNNING', 'DRAFT', 'REVIEW', 'UNKNOWN'].includes(normalized.value)) return 'warning'
  if (['BLOCKED', 'FAILED', 'ERROR', 'DATA_GAP', 'MISSING', 'REJECTED', 'SUPERSEDED', 'EXPIRED', 'VETO', 'GATE_BLOCKED'].includes(normalized.value)) return 'error'
  return 'info'
})
const text = computed(() => {
  const label = props.label?.trim()
  return label ? (/^[A-Za-z][A-Za-z_-]*$/.test(label) ? systemStatusLabel(label) : label) : systemStatusLabel(props.status || 'UNKNOWN')
})
</script>

<template>
  <n-tag :type="type" :size="size" :bordered="false" :data-status="normalized">{{ text }}</n-tag>
</template>
