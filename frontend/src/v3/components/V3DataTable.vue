<script setup lang="ts">
/**
 * V3 通用表格：基于 Quasar QTable
 * UI-0 只做基础能力，不实现完整业务列。
 */
import { computed } from 'vue'
import V3EmptyState from './V3EmptyState.vue'
import V3LoadingState from './V3LoadingState.vue'

export interface V3TableColumn {
  name: string
  label: string
  field: string | ((row: Record<string, unknown>) => unknown)
  align?: 'left' | 'right' | 'center'
  sortable?: boolean
  width?: string
}

const props = withDefaults(
  defineProps<{
    columns: V3TableColumn[]
    rows: Record<string, unknown>[]
    rowKey?: string
    loading?: boolean
    dense?: boolean
    stickyHeader?: boolean
    emptyTitle?: string
    emptyDescription?: string
  }>(),
  {
    rowKey: 'id',
    loading: false,
    dense: true,
    stickyHeader: true,
    emptyTitle: '暂无数据',
    emptyDescription: '当前没有可展示的行。',
  },
)

const emit = defineEmits<{
  rowClick: [row: Record<string, unknown>]
}>()

const quasarColumns = computed(() =>
  props.columns.map((col) => ({
    name: col.name,
    label: col.label,
    field: col.field,
    align: col.align ?? 'left',
    sortable: col.sortable ?? false,
    style: col.width ? `width: ${col.width}` : undefined,
  })),
)
</script>

<template>
  <div class="v3-data-table" data-testid="v3-data-table">
    <V3LoadingState v-if="loading" variant="table" :rows="5" label="表格加载中" />
    <V3EmptyState
      v-else-if="!rows.length"
      :title="emptyTitle"
      :description="emptyDescription"
      data-testid="v3-data-table-empty"
    />
    <q-table
      v-else
      class="v3-data-table__qtable"
      :class="{ 'v3-data-table--sticky': stickyHeader }"
      :rows="rows"
      :columns="quasarColumns"
      :row-key="rowKey"
      :dense="dense"
      flat
      bordered
      hide-pagination
      :rows-per-page-options="[0]"
      @row-click="(_, row) => emit('rowClick', row)"
    >
      <template v-for="(_, name) in $slots" #[name]="slotData">
        <slot :name="name" v-bind="slotData ?? {}" />
      </template>
    </q-table>
  </div>
</template>

<style scoped>
.v3-data-table {
  min-width: 0;
  border-radius: var(--v3-radius-md);
  overflow: hidden;
}
.v3-data-table__qtable {
  background: var(--v3-surface);
}
.v3-data-table--sticky :deep(.q-table__middle) {
  max-height: 480px;
}
.v3-data-table--sticky :deep(thead tr) {
  position: sticky;
  top: 0;
  z-index: 1;
}
:deep(.q-table tbody tr) {
  cursor: pointer;
}
:deep(.q-table tbody tr:hover) {
  background: var(--v3-surface-hover);
}
</style>
