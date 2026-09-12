<script setup lang="ts">
/** V3 空状态 */
withDefaults(
  defineProps<{
    title?: string
    description?: string
    icon?: string
  }>(),
  { title: '暂无数据', description: '当前筛选条件下没有可展示的内容。', icon: 'inbox' },
)
</script>

<template>
  <div class="v3-empty" data-testid="v3-empty-state">
    <div class="v3-empty__icon" aria-hidden="true">
      <slot name="icon">
        <span class="v3-empty__icon-fallback">{{ icon === 'inbox' ? '∅' : '·' }}</span>
      </slot>
    </div>
    <p class="v3-empty__title">{{ title }}</p>
    <p v-if="description" class="v3-empty__desc">{{ description }}</p>
    <div v-if="$slots.actions" class="v3-empty__actions"><slot name="actions" /></div>
  </div>
</template>

<style scoped>
.v3-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--v3-space-2);
  padding: var(--v3-space-8) var(--v3-space-4);
  text-align: center;
}
.v3-empty__icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface-muted);
  color: var(--v3-text-muted);
  margin-bottom: var(--v3-space-1);
}
.v3-empty__icon-fallback { font-size: 18px; line-height: 1; }
.v3-empty__title {
  margin: 0;
  font-size: var(--v3-font-size-lg);
  font-weight: 600;
  color: var(--v3-text);
}
.v3-empty__desc {
  margin: 0;
  max-width: 360px;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
  line-height: 1.5;
}
.v3-empty__actions { margin-top: var(--v3-space-3); }
</style>
