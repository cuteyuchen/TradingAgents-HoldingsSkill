<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { WatchlistItem, HealthStatus } from '../api/types'

const list = ref<WatchlistItem[]>([])
const health = ref<HealthStatus[]>([])
const err = ref('')
const form = ref<WatchlistItem>({ code: '', name: '', cadence: '10:00', enabled: true })

async function load() {
  err.value = ''
  try {
    const [w, h] = await Promise.all([api.listWatchlist(), api.health()])
    list.value = w
    health.value = h
  } catch (e) {
    err.value = (e as Error).message
  }
}

async function add() {
  if (!form.value.code) return
  try {
    await api.addWatchlist({ ...form.value })
    form.value = { code: '', name: '', cadence: '10:00', enabled: true }
    await load()
  } catch (e) {
    err.value = (e as Error).message
  }
}

async function remove(code: string) {
  try {
    await api.removeWatchlist(code)
    await load()
  } catch (e) {
    err.value = (e as Error).message
  }
}

onMounted(load)
</script>

<template>
  <div class="card">
    <h3>自选股 / 定时分析</h3>
    <div v-if="err" class="err">{{ err }}</div>
    <div class="add-form">
      <input v-model="form.code" placeholder="代码" />
      <input v-model="form.name" placeholder="名称" />
      <select v-model="form.cadence">
        <option value="09:25">09:25</option>
        <option value="10:00">10:00</option>
        <option value="12:00">12:00</option>
        <option value="14:30">14:30</option>
      </select>
      <button @click="add">添加</button>
    </div>
    <table>
      <thead><tr><th>代码</th><th>名称</th><th>检查点</th><th>启用</th><th></th></tr></thead>
      <tbody>
        <tr v-for="(w, i) in list" :key="i">
          <td><code>{{ w.code }}</code></td>
          <td>{{ w.name || '—' }}</td>
          <td>{{ w.cadence || '—' }}</td>
          <td>{{ w.enabled ? '是' : '否' }}</td>
          <td><button @click="remove(w.code)">删除</button></td>
        </tr>
        <tr v-if="!list.length"><td colspan="5" class="muted">暂无自选股</td></tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h3>检查点健康度（连续失败降级）</h3>
    <table>
      <thead><tr><th>检查点</th><th>连续失败</th><th>状态</th><th>最近成功</th><th>最近失败</th></tr></thead>
      <tbody>
        <tr v-for="(h, i) in health" :key="i" :class="{ 'degraded': h.degraded }">
          <td>{{ h.checkpoint }}</td>
          <td>{{ h.consecutive_failures }}</td>
          <td><span class="tag" :class="h.degraded ? 'grade-C' : 'grade-A'">{{ h.degraded ? '降级' : '正常' }}</span></td>
          <td class="muted">{{ h.last_success_at ? h.last_success_at.slice(5, 16).replace('T', ' ') : '—' }}</td>
          <td class="muted">{{ h.last_failure_at ? h.last_failure_at.slice(5, 16).replace('T', ' ') : '—' }}</td>
        </tr>
        <tr v-if="!health.length"><td colspan="5" class="muted">暂无健康记录</td></tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.add-form { display: flex; gap: 8px; margin-bottom: 12px; }
.add-form input, .add-form select { padding: 6px 10px; border: 1px solid #d9dce0; border-radius: 4px; font-size: 13px; }
.add-form input:first-child { width: 110px; }
.err { color: #cf1322; font-size: 13px; margin-bottom: 8px; }
.degraded { background: #fff7e6; }
</style>
