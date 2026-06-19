<script setup lang="ts">
import type { DataTableColumns } from 'naive-ui'
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { Candidate } from '../api/types'
import { emptyText, renderInstrument, renderMuted, renderStatus } from '../utils/ui'

const list = ref<Candidate[]>([])
const statusFilter = ref('')
const err = ref('')
const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '待触发', value: '待触发' },
  { label: '已命中', value: '已命中' },
  { label: '已取消', value: '已取消' },
]

const columns: DataTableColumns<Candidate> = [
  {
    title: '候选',
    key: 'name',
    minWidth: 180,
    render: (row) => renderInstrument(row.name, row.code),
  },
  { title: '类型', key: 'type', width: 92, render: (row) => row.type || emptyText },
  { title: '评分', key: 'score', width: 82, render: (row) => row.score ?? emptyText },
  { title: '入场触发', key: 'entry_trigger', minWidth: 280, render: (row) => renderMuted(row.entry_trigger) },
  { title: '仓位', key: 'initial_size', minWidth: 220, render: (row) => row.initial_size || emptyText },
  {
    title: '止盈1/2',
    key: 'take_profit',
    minWidth: 150,
    render: (row) => [row.take_profit_1, row.take_profit_2].filter(Boolean).join(' / ') || emptyText,
  },
  { title: '止损', key: 'stop_loss', minWidth: 120, render: (row) => row.stop_loss || emptyText },
  { title: '状态', key: 'status', width: 120, render: (row) => renderStatus(row.status) },
]

async function load() {
  err.value = ''
  try {
    list.value = await api.listCandidates(statusFilter.value)
  } catch (e) {
    err.value = (e as Error).message
  }
}

onMounted(load)
</script>

<template>
  <n-card title="候选跟踪">
    <n-space class="toolbar" align="center">
      <n-select v-model:value="statusFilter" :options="statusOptions" @update:value="load" />
      <n-button @click="load">刷新</n-button>
    </n-space>
    <n-alert v-if="err" type="error" :show-icon="false" class="mb-3">{{ err }}</n-alert>
    <n-data-table
      :columns="columns"
      :data="list"
      :bordered="false"
      :single-line="false"
      :scroll-x="1260"
    />
  </n-card>
</template>

<style scoped>
.toolbar :deep(.n-select) { width: 180px; }
@media (max-width: 640px) {
  .toolbar :deep(.n-select),
  .toolbar :deep(.n-button) { width: 100%; }
}
</style>
