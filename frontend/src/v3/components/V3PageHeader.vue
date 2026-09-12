<script setup lang="ts">
/** V3 页面页头：title / description / eyebrow / actions / status */
withDefaults(
  defineProps<{
    title: string
    description?: string
    eyebrow?: string
  }>(),
  { description: '', eyebrow: '' },
)
</script>

<template>
  <header class="v3-page-header">
    <div class="v3-page-header__copy">
      <p v-if="eyebrow" class="v3-page-header__eyebrow">{{ eyebrow }}</p>
      <h1 class="v3-page-header__title">{{ title }}</h1>
      <p v-if="description" class="v3-page-header__desc">{{ description }}</p>
    </div>
    <div class="v3-page-header__side">
      <div v-if="$slots.status" class="v3-page-header__status"><slot name="status" /></div>
      <div v-if="$slots.actions" class="v3-page-header__actions"><slot name="actions" /></div>
    </div>
  </header>
</template>

<style scoped>
.v3-page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--v3-space-4);
  min-width: 0;
}
.v3-page-header__copy { min-width: 0; }
.v3-page-header__eyebrow {
  margin: 0 0 var(--v3-space-1);
  color: var(--v3-primary);
  font-size: var(--v3-font-size-xs);
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.v3-page-header__title {
  margin: 0;
  font-size: var(--v3-font-size-3xl);
  line-height: 1.2;
  font-weight: 700;
}
.v3-page-header__desc {
  max-width: 760px;
  margin: var(--v3-space-2) 0 0;
  color: var(--v3-text-muted);
  line-height: 1.6;
}
.v3-page-header__side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--v3-space-2);
}
.v3-page-header__status { display: flex; align-items: center; gap: var(--v3-space-2); }
.v3-page-header__actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--v3-space-2); }
@media (max-width: 680px) {
  .v3-page-header { align-items: flex-start; flex-direction: column; }
  .v3-page-header__side { width: 100%; align-items: flex-start; }
  .v3-page-header__actions { justify-content: flex-start; }
}
</style>
