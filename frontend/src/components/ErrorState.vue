<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, RefreshCw } from 'lucide-vue-next'
import { ApiError, errorMessage, requestIdOf } from '../api'

const props = withDefaults(defineProps<{
  error?: unknown
  title?: string
  retryLabel?: string
}>(), {
  title: '数据暂时加载失败',
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
      <n-collapse v-if="kind !== 'network' && (props.error instanceof ApiError)" class="error-details">
        <n-collapse-item title="技术详情" name="error">
          <dl><div><dt>Code</dt><dd>{{ (props.error as ApiError).code || '—' }}</dd></div><div><dt>Status</dt><dd>{{ (props.error as ApiError).status || '—' }}</dd></div><div><dt>Request ID</dt><dd>{{ requestId || '—' }}</dd></div></dl>
        </n-collapse-item>
      </n-collapse>
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
.error-details { margin-top: 8px; color: var(--app-text-muted); }.error-details :deep(.n-collapse-item__header) { padding: 3px 0; font-size: 11px; }.error-details dl { margin: 0; }.error-details dl div { display: flex; gap: 10px; padding: 3px 0; }.error-details dt { width: 70px; }.error-details dd { margin: 0; color: var(--app-text); word-break: break-word; }
</style>
