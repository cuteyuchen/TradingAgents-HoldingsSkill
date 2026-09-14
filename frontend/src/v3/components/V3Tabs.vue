<script setup lang="ts">
/** V3 Tabs：统一 styling，业务内容由 slot 注入 */
import { computed } from 'vue'

export interface V3TabItem {
  name: string
  label: string
  disable?: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    items: V3TabItem[]
    dense?: boolean
    align?: 'left' | 'center' | 'right' | 'justify'
  }>(),
  { dense: false, align: 'left' },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const current = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})
</script>

<template>
  <div class="v3-tabs" data-testid="v3-tabs">
    <q-tabs
      v-model="current"
      :dense="dense"
      :align="align"
      active-color="primary"
      indicator-color="primary"
      class="v3-tabs__bar"
    >
      <q-tab
        v-for="item in items"
        :key="item.name"
        :name="item.name"
        :label="item.label"
        :disable="item.disable"
        :data-testid="`v3-tab-${item.name}`"
        no-caps
      />
    </q-tabs>
    <div class="v3-tabs__panels">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.v3-tabs {
  min-width: 0;
}
.v3-tabs__bar {
  border-bottom: 1px solid var(--v3-border);
  color: var(--v3-text);
}
.v3-tabs__panels {
  padding-top: var(--v3-space-4);
}
</style>
