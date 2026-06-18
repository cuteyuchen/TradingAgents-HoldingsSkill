<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import type { RunSummary } from '../api/types'

const router = useRouter()
const runs = ref<RunSummary[]>([])
const loading = ref(false)
const err = ref('')
const codeFilter = ref('')

async function load() {
  loading.value = true
  err.value = ''
  try {
    const params = codeFilter.value ? `?code=${encodeURIComponent(codeFilter.value)}` : ''
    runs.value = await api.listRuns(params)
  } catch (e) {
    err.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

const gradeClass = (g?: string | null): string => (g ? `grade-${g}` : '')

onMounted(load)
</script>

<template>
  <div class="card">
    <h3>决策列表</h3>
    <div class="toolbar">
      <input v-model="codeFilter" placeholder="按代码筛选，如 600519" @keyup.enter="load" />
      <button @click="load">查询</button>
      <span class="muted" style="margin-left: auto">共 {{ runs.length }} 条</span>
    </div>
    <div v-if="err" class="err">{{ err }}</div>
    <table v-if="runs.length">
      <thead>
        <tr>
          <th>时间</th><th>检查点</th><th>数据质量</th><th>组合评级</th>
          <th>持仓数</th><th>候选数</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in runs" :key="r.id" class="clickable" @click="router.push(`/runs/${r.id}`)">
          <td>{{ r.timestamp.slice(0, 16).replace('T', ' ') }}</td>
          <td>{{ r.checkpoint || '—' }}</td>
          <td><span v-if="r.data_quality_grade" class="tag" :class="gradeClass(r.data_quality_grade)">{{ r.data_quality_grade }}</span><span v-else>—</span></td>
          <td>{{ r.pm_rating || '—' }}</td>
          <td>{{ r.holdings_count }}</td>
          <td>{{ r.candidates_count }}</td>
        </tr>
      </tbody>
    </table>
    <div v-else-if="!loading" class="muted" style="padding: 24px; text-align: center">暂无决策记录。先在 skill 执行一次并上传，或检查 Token 是否正确。</div>
  </div>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.toolbar input { padding: 6px 10px; border: 1px solid #d9dce0; border-radius: 4px; font-size: 13px; width: 200px; }
.clickable { cursor: pointer; }
.clickable:hover { background: #f2f6ff; }
.err { color: #cf1322; font-size: 13px; margin-bottom: 8px; }
</style>
