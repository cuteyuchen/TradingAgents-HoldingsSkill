<script setup lang="ts">
withDefaults(defineProps<{ title?: string; name?: string; defaultOpen?: boolean; native?: boolean }>(), { title: '技术详情', name: 'technical', defaultOpen: false, native: false })
</script>

<template>
  <details v-if="native" :open="defaultOpen" class="technical-details"><summary>{{ title }}</summary><div class="technical-details-body"><slot /></div></details>
  <n-collapse v-else :default-expanded-names="defaultOpen ? [name] : []" class="technical-details">
    <n-collapse-item :title="title" :name="name">
      <div class="technical-details-body"><slot /></div>
    </n-collapse-item>
  </n-collapse>
</template>

<style scoped>
.technical-details { border-top: 1px solid var(--border); }
.technical-details-body { color: var(--text-muted); font-size: 12px; }
.technical-details-body :deep(pre) { max-height: 440px; margin: 0; overflow: auto; background: var(--surface-muted); color: var(--text); white-space: pre-wrap; word-break: break-word; }
</style>
