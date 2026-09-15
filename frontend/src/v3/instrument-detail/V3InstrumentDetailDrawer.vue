<script setup lang="ts">
import { computed } from 'vue'
import V3DetailDrawer from '../components/V3DetailDrawer.vue'
import V3InstrumentDetail from './V3InstrumentDetail.vue'
import type { InstrumentTab } from './useInstrumentDetail'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    code: string
    initialTab?: InstrumentTab
    title?: string
  }>(),
  { initialTab: 'quote', title: '标的详情' },
)

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const displayTitle = computed(() => (props.code ? `${props.title} · ${props.code}` : props.title))

/**
 * v-if="open && code" unmounts the shared detail on close so
 * onBeforeUnmount aborts in-flight requests and clears poll timers.
 */
const detailMounted = computed(() => open.value && Boolean(props.code))
</script>

<template>
  <V3DetailDrawer
    v-model="open"
    :title="displayTitle"
    data-testid="instrument-detail-drawer"
  >
    <V3InstrumentDetail
      v-if="detailMounted"
      :code="code"
      :initial-tab="initialTab"
      compact
      data-testid="instrument-detail-drawer-body"
    />
  </V3DetailDrawer>
</template>
