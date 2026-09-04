<script setup lang="ts">
withDefaults(defineProps<{ message?: string; rows?: number }>(), { message: '正在读取数据…', rows: 3 })
</script>

<template>
  <div class="shared-loading" role="status" aria-live="polite">
    <div class="loading-copy"><n-spin size="small" /><span>{{ message }}</span></div>
    <div class="loading-lines" aria-hidden="true">
      <span v-for="index in rows" :key="index" />
    </div>
  </div>
</template>

<style scoped>
.shared-loading { display: grid; min-height: 150px; align-content: center; gap: 18px; color: var(--app-text-muted); }
.loading-copy { display: flex; align-items: center; justify-content: center; gap: 10px; }
.loading-lines { display: grid; gap: 9px; }
.loading-lines span { display: block; height: 12px; border-radius: 4px; background: linear-gradient(90deg, var(--surface-muted), var(--border), var(--surface-muted)); background-size: 200% 100%; animation: skeleton 1.2s ease-in-out infinite; }
.loading-lines span:nth-child(2) { width: 82%; }.loading-lines span:nth-child(3) { width: 64%; }
@keyframes skeleton { from { background-position: 100% 0; } to { background-position: -100% 0; } }
</style>
