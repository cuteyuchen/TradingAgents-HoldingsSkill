<script setup lang="ts">
/**
 * V3 详情抽屉：为下一阶段 InstrumentDetail 打底。
 * 桌面约 60–70vw + max-width；移动端近全屏。
 */
import { computed } from 'vue'
import { X } from 'lucide-vue-next'

export interface V3DetailDrawerProps {
  modelValue: boolean
  title?: string
  subtitle?: string
  /** status slot 旁的语义标签 */
  statusLabel?: string
}

const props = withDefaults(defineProps<V3DetailDrawerProps>(), {
  title: '详情',
  subtitle: '',
  statusLabel: '',
})

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
</script>

<template>
  <q-dialog
    v-model="open"
    position="right"
    full-height
    maximized
    data-testid="v3-detail-drawer"
    class="v3-detail-drawer-dialog"
  >
    <q-card class="v3-detail-drawer">
      <q-card-section class="v3-detail-drawer__header row items-center no-wrap">
        <div class="col">
          <div class="v3-detail-drawer__title-row">
            <h2 class="v3-detail-drawer__title">{{ title }}</h2>
            <slot name="status">
              <span v-if="statusLabel" class="v3-detail-drawer__status">{{ statusLabel }}</span>
            </slot>
          </div>
          <p v-if="subtitle" class="v3-detail-drawer__subtitle">{{ subtitle }}</p>
        </div>
        <q-btn
          flat
          round
          dense
          aria-label="关闭详情"
          data-testid="v3-detail-drawer-close"
          @click="open = false"
        >
          <X :size="18" aria-hidden="true" />
        </q-btn>
      </q-card-section>

      <q-separator />

      <div v-if="$slots.tabs" class="v3-detail-drawer__tabs">
        <slot name="tabs" />
      </div>

      <q-card-section class="v3-detail-drawer__body col scroll">
        <slot />
      </q-card-section>
    </q-card>
  </q-dialog>
</template>

<style scoped>
.v3-detail-drawer {
  width: var(--v3-drawer-desktop-width);
  max-width: var(--v3-drawer-desktop-max);
  background: var(--v3-surface);
  color: var(--v3-text);
  border-left: 1px solid var(--v3-border);
}
.v3-detail-drawer__header {
  padding: var(--v3-space-4) var(--v3-space-5);
  gap: var(--v3-space-3);
}
.v3-detail-drawer__title-row {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
  flex-wrap: wrap;
}
.v3-detail-drawer__title {
  margin: 0;
  font-size: var(--v3-font-size-xl);
  font-weight: 700;
  line-height: 1.3;
}
.v3-detail-drawer__subtitle {
  margin: var(--v3-space-1) 0 0;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.v3-detail-drawer__status {
  border-radius: var(--v3-radius-pill);
  background: var(--v3-primary-soft);
  color: var(--v3-primary);
  padding: 2px 8px;
  font-size: var(--v3-font-size-xs);
  font-weight: 600;
}
.v3-detail-drawer__tabs {
  padding: 0 var(--v3-space-4);
  border-bottom: 1px solid var(--v3-border);
}
.v3-detail-drawer__body {
  padding: var(--v3-space-5);
}
@media (max-width: 768px) {
  .v3-detail-drawer {
    width: 100vw !important;
    max-width: 100vw !important;
  }
}
</style>
