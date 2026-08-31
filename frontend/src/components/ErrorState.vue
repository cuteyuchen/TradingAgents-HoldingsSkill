<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, RefreshCw } from 'lucide-vue-next'
import { ApiError, errorMessage, requestIdOf } from '../api'

const props = withDefaults(defineProps<{
  error?: unknown
  title?: string
  retryLabel?: string
}>(), {
  title: '读取失败',
  retryLabel: '重试',
})
const emit = defineEmits<{ retry: [] }>()
const message = computed(() => errorMessage(props.error, '暂时无法读取后端数据。'))
const requestId = computed(() => requestIdOf(props.error))
const kind = computed(() => props.error instanceof ApiError ? props.error.kind : 'unknown')
</script>

<template>
  <div class="shared-error" role="alert">
    <AlertTriangle :size="18" aria-hidden="true" />
    <div>
      <strong>{{ title }}</strong>
      <p>{{ message }}</p>
      <small v-if="kind === 'network'">请确认后端服务已启动，恢复后可以直接重试。</small>
      <small v-if="requestId">请求 ID：{{ requestId }}</small>
    </div>
    <n-button secondary size="small" @click="emit('retry')">
      <template #icon><RefreshCw :size="14" /></template>
      {{ retryLabel }}
    </n-button>
  </div>
</template>

<style scoped>
.shared-error { display: flex; align-items: flex-start; gap: 10px; border: 1px solid color-mix(in srgb, var(--app-danger) 38%, var(--app-border)); background: color-mix(in srgb, var(--app-danger) 8%, var(--app-surface)); padding: 12px 14px; color: var(--app-danger); }
.shared-error > div { flex: 1; min-width: 0; }
.shared-error strong { display: block; color: var(--app-text); }
.shared-error p { margin: 3px 0; color: var(--app-text); overflow-wrap: anywhere; }
.shared-error small { display: block; margin-top: 3px; color: var(--app-text-muted); overflow-wrap: anywhere; }
</style>
