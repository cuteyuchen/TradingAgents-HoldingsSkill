<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  description?: string
  eyebrow?: string
}>(), { eyebrow: '' })
</script>

<template>
  <header class="page-header">
    <div class="page-header-copy">
      <p v-if="eyebrow" class="page-eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <p v-if="description" class="page-description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header-actions"><slot name="actions" /></div>
  </header>
</template>

<style scoped>
.page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; min-width: 0; }
.page-header-copy { min-width: 0; }
.page-eyebrow { margin: 0 0 5px; color: var(--primary); font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
h1 { margin: 0; font-size: 26px; line-height: 1.2; }
.page-description { max-width: 760px; margin: 7px 0 0; color: var(--text-muted); line-height: 1.6; }
.page-header-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
@media (max-width: 680px) { .page-header { align-items: flex-start; flex-direction: column; } .page-header-actions { width: 100%; justify-content: flex-start; } }
</style>
