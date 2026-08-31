<script setup lang="ts">
withDefaults(defineProps<{ description?: string }>(), { description: '暂无数据' })
</script>

<template>
  <div class="shared-empty">
    <n-empty :description="description">
      <template v-if="$slots.action" #extra><slot name="action" /></template>
    </n-empty>
  </div>
</template>

<style scoped>
.shared-empty { min-height: 110px; display: grid; place-items: center; padding: 14px; }
</style>
