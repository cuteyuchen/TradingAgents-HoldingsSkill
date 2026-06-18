<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { Candidate } from '../api/types'

const list = ref<Candidate[]>([])
const statusFilter = ref('')
const err = ref('')

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
      <select v-model="statusFilter" @change="load">
        <option value="">全部状态</option>
        <option value="待触发">待触发</option>
        <option value="已命中">已命中</option>
        <option value="已取消">已取消</option>
      </select>
      <button @click="load">刷新</button>
    </div>
    <div v-if="err" class="err">{{ err }}</div>
    <table>
      <thead>
        <tr><th>候选</th><th>类型</th><th>评分</th><th>入场触发</th><th>仓位</th><th>止盈1/2</th><th>止损</th><th>状态</th></tr>
      </thead>
      <tbody>
        <tr v-for="(c, i) in list" :key="i">
          <td>{{ c.name }} <code>{{ c.code }}</code></td>
          <td>{{ c.type || '—' }}</td>
          <td>{{ c.score ?? '—' }}</td>
          <td class="muted">{{ c.entry_trigger || '—' }}</td>
          <td>{{ c.initial_size || '—' }}</td>
          <td>{{ [c.take_profit_1, c.take_profit_2].filter(Boolean).join(' / ') || '—' }}</td>
          <td>{{ c.stop_loss || '—' }}</td>
          <td>{{ c.status }}</td>
        </tr>
        <tr v-if="!list.length"><td colspan="8" class="muted">暂无候选</td></tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; }
.toolbar select { padding: 6px 10px; border: 1px solid #d9dce0; border-radius: 4px; font-size: 13px; }
.err { color: #cf1322; font-size: 13px; margin-bottom: 8px; }
</style>
