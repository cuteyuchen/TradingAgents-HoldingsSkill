<script setup lang="ts">
withDefaults(defineProps<{ message?: string }>(), { message: '正在读取数据…' })
</script>

<template>
  <div class="shared-loading" role="status" aria-live="polite">
    <n-spin size="small" />
    <span>{{ message }}</span>
  </div>
</template>

<style scoped>
.shared-loading { display: flex; min-height: 96px; align-items: center; justify-content: center; gap: 10px; color: var(--app-text-muted); }
</style>
