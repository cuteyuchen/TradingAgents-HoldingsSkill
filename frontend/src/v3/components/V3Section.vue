<script setup lang="ts">
/** V3 区块：默认轻量 surface，不要厚重 Card */
withDefaults(
  defineProps<{
    title?: string
    description?: string
    compact?: boolean
    dense?: boolean
  }>(),
  { title: '', description: '', compact: false, dense: false },
)
</script>

<template>
  <section
    class="v3-section"
    :class="{ 'v3-section--compact': compact, 'v3-section--dense': dense }"
  >
    <header v-if="title || description || $slots.actions" class="v3-section__header">
      <div class="v3-section__heading">
        <h2 v-if="title" class="v3-section__title">{{ title }}</h2>
        <p v-if="description" class="v3-section__desc">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="v3-section__actions"><slot name="actions" /></div>
    </header>
    <div class="v3-section__body"><slot /></div>
  </section>
</template>

<style scoped>
.v3-section {
  min-width: 0;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  padding: var(--v3-space-5);
  box-shadow: var(--v3-shadow-sm);
}
.v3-section--compact { padding: var(--v3-space-3) var(--v3-space-4); }
.v3-section--dense { padding: var(--v3-space-2) var(--v3-space-3); }
.v3-section__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--v3-space-3);
  margin-bottom: var(--v3-space-4);
}
.v3-section--dense .v3-section__header,
.v3-section--compact .v3-section__header { margin-bottom: var(--v3-space-3); }
.v3-section__heading { min-width: 0; }
.v3-section__title {
  margin: 0;
  font-size: var(--v3-font-size-xl);
  font-weight: 700;
  line-height: 1.3;
}
.v3-section__desc {
  margin: var(--v3-space-1) 0 0;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
  line-height: 1.55;
}
.v3-section__actions { display: flex; flex-wrap: wrap; gap: var(--v3-space-2); }
@media (max-width: 600px) {
  .v3-section { padding: var(--v3-space-4); }
  .v3-section__header { flex-direction: column; }
}
</style>
