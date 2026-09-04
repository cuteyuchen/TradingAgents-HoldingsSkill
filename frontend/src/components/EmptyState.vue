<script setup lang="ts">
withDefaults(defineProps<{ title?: string; description?: string }>(), { title: '这里还没有可展示的内容', description: '完成下一步后，这里会出现相关信息。' })
</script>

<template>
  <div class="shared-empty">
    <n-empty :description="description">
      <template #default><strong>{{ title }}</strong></template>
      <template v-if="$slots.action" #extra><slot name="action" /></template>
    </n-empty>
  </div>
</template>

<style scoped>
.shared-empty { min-height: 150px; display: grid; place-items: center; padding: 22px 14px; }
.shared-empty :deep(.n-empty__description) { max-width: 420px; line-height: 1.6; }
</style>
