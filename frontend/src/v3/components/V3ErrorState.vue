<script setup lang="ts">
/** V3 错误状态：AbortError 不应触发此组件（正常控制流） */
withDefaults(
  defineProps<{
    title?: string
    description?: string
    detail?: string
    retryLabel?: string
  }>(),
  {
    title: '加载失败',
    description: '请稍后重试，或检查网络连接。',
    detail: '',
    retryLabel: '重试',
  },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="v3-error" data-testid="v3-error-state" role="alert">
    <div class="v3-error__badge" aria-hidden="true">!</div>
    <p class="v3-error__title">{{ title }}</p>
    <p class="v3-error__desc">{{ description }}</p>
    <p v-if="detail" class="v3-error__detail">{{ detail }}</p>
    <div class="v3-error__actions">
      <button type="button" class="v3-error__retry" data-testid="v3-error-retry" @click="emit('retry')">
        {{ retryLabel }}
      </button>
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.v3-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--v3-space-2);
  padding: var(--v3-space-6) var(--v3-space-4);
  text-align: center;
  border: 1px dashed color-mix(in srgb, var(--v3-status-danger) 35%, var(--v3-border));
  border-radius: var(--v3-radius-md);
  background: var(--v3-status-danger-soft);
}
.v3-error__badge {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--v3-status-danger);
  color: #fff;
  font-weight: 800;
  font-size: 16px;
}
.v3-error__title {
  margin: 0;
  font-size: var(--v3-font-size-lg);
  font-weight: 700;
  color: var(--v3-status-danger);
}
.v3-error__desc {
  margin: 0;
  color: var(--v3-text-secondary);
  font-size: var(--v3-font-size-sm);
}
.v3-error__detail {
  margin: 0;
  max-width: 480px;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
  font-family: var(--v3-font-mono);
  overflow-wrap: anywhere;
}
.v3-error__actions {
  display: flex;
  gap: var(--v3-space-2);
  margin-top: var(--v3-space-2);
}
.v3-error__retry {
  border: 1px solid var(--v3-status-danger);
  border-radius: var(--v3-radius-sm);
  background: transparent;
  color: var(--v3-status-danger);
  padding: 6px 14px;
  font-size: var(--v3-font-size-sm);
  font-weight: 600;
  cursor: pointer;
}
.v3-error__retry:hover {
  background: var(--v3-status-danger);
  color: #fff;
}
</style>
