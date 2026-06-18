<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { Candidate } from '../api/types'

const list = ref<Candidate[]>([])
const statusFilter = ref('')
const err = ref('')
const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '待触发', value: '待触发' },
  { label: '已命中', value: '已命中' },
  { label: '已取消', value: '已取消' },
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
  <div class="card">
    <h3>候选跟踪</h3>
    <div class="toolbar">
      <n-select v-model:value="statusFilter" :options="statusOptions" @update:value="load" />
      <n-button @click="load">刷新</n-button>
    </div>
    <n-alert v-if="err" type="error" :show-icon="false" class="mb-3">{{ err }}</n-alert>
    <table class="data-table">
      <thead>
        <tr><th>候选</th><th>类型</th><th>评分</th><th>入场触发</th><th>仓位</th><th>止盈1/2</th><th>止损</th><th>状态</th></tr>
      </thead>
      <tbody>
        <tr v-for="(c, i) in list" :key="i">
          <td data-label="候选">{{ c.name }} <code>{{ c.code }}</code></td>
          <td data-label="类型">{{ c.type || '—' }}</td>
          <td data-label="评分">{{ c.score ?? '—' }}</td>
          <td data-label="入场触发" class="muted">{{ c.entry_trigger || '—' }}</td>
          <td data-label="仓位">{{ c.initial_size || '—' }}</td>
          <td data-label="止盈1/2">{{ [c.take_profit_1, c.take_profit_2].filter(Boolean).join(' / ') || '—' }}</td>
          <td data-label="止损">{{ c.stop_loss || '—' }}</td>
          <td data-label="状态">{{ c.status }}</td>
        </tr>
        <tr v-if="!list.length"><td colspan="8" class="muted">暂无候选</td></tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.toolbar :deep(.n-select) { width: 180px; }
@media (max-width: 640px) {
  .toolbar :deep(.n-select),
  .toolbar :deep(.n-button) { width: 100%; }
}
</style>
