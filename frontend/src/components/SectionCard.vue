<script setup lang="ts">
withDefaults(defineProps<{ title?: string; description?: string }>(), { title: '', description: '' })
</script>

<template>
  <section class="section-card panel-card">
    <header v-if="title || description || $slots.actions" class="section-card-header">
      <div><h2 v-if="title">{{ title }}</h2><p v-if="description">{{ description }}</p></div>
      <div v-if="$slots.actions" class="section-card-actions"><slot name="actions" /></div>
    </header>
    <slot />
  </section>
</template>

<style scoped>
.section-card { padding: 18px 20px; }
.section-card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.section-card-header h2 { margin: 0; font-size: 17px; }
.section-card-header p { margin: 5px 0 0; color: var(--text-muted); font-size: 12px; line-height: 1.55; }
.section-card-actions { display: flex; flex-wrap: wrap; gap: 8px; }
@media (max-width: 600px) { .section-card { padding: 16px; } .section-card-header { align-items: stretch; flex-direction: column; } }
</style>
