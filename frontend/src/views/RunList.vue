<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Trash2 } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'
import { api } from '../api'
import type { RunSummary } from '../api/types'

const router = useRouter()
const message = useMessage()
const runs = ref<RunSummary[]>([])
const loading = ref(false)
const deletingId = ref<number | null>(null)
const err = ref('')
const codeFilter = ref('')
const fromFilter = ref<string | null>(null)
const toFilter = ref<string | null>(null)
const checkpointFilter = ref('')
const gradeFilter = ref('')
const checkpointOptions = [
  { label: '全部检查点', value: '' },
  { label: '09:25', value: '09:25' },
  { label: '10:00', value: '10:00' },
  { label: '12:00', value: '12:00' },
  { label: '14:30', value: '14:30' },
]
const gradeOptions = [
  { label: '全部质量', value: '' },
  { label: 'A', value: 'A' },
  { label: 'B', value: 'B' },
  { label: 'C', value: 'C' },
  { label: 'D', value: 'D' },
  { label: 'F', value: 'F' },
]

async function load() {
  loading.value = true
  err.value = ''
  try {
    const params = new URLSearchParams()
    const code = codeFilter.value.trim().toUpperCase()
    if (code) params.set('code', code)
    if (fromFilter.value) params.set('from', fromFilter.value)
    if (toFilter.value) params.set('to', toFilter.value)
    if (checkpointFilter.value) params.set('checkpoint', checkpointFilter.value)
    if (gradeFilter.value) params.set('grade', gradeFilter.value)
    const query = params.toString()
    runs.value = await api.listRuns(query ? `?${query}` : '')
  } catch (e) {
    err.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function removeRun(id: number) {
  deletingId.value = id
  err.value = ''
  try {
    await api.deleteRun(id)
    runs.value = runs.value.filter((r) => r.id !== id)
    message.success('决策已删除')
  } catch (e) {
    err.value = (e as Error).message
    message.error(err.value)
  } finally {
    deletingId.value = null
  }
}

const gradeClass = (g?: string | null): string => (g ? `grade-${g}` : '')

onMounted(load)
</script>

<template>
  <div class="card list-card">
    <div class="list-head">
      <h3>决策列表</h3>
      <span class="count-pill" aria-live="polite">共 {{ runs.length }} 条</span>
    </div>
    <div class="toolbar list-toolbar" aria-label="决策筛选">
      <n-input v-model:value="codeFilter" clearable placeholder="按代码筛选，如 600519" @keyup.enter="load" />
      <n-date-picker
        v-model:formatted-value="fromFilter"
        type="date"
        value-format="yyyy-MM-dd"
        clearable
        placeholder="开始日期"
        title="开始日期"
      />
      <n-date-picker
        v-model:formatted-value="toFilter"
        type="date"
        value-format="yyyy-MM-dd"
        clearable
        placeholder="结束日期"
        title="结束日期"
      />
      <n-select v-model:value="checkpointFilter" :options="checkpointOptions" />
      <n-select v-model:value="gradeFilter" :options="gradeOptions" />
      <n-button type="primary" class="query-button" :loading="loading" @click="load">查询</n-button>
    </div>
    <n-alert v-if="err" type="error" :show-icon="false" class="mb-3">{{ err }}</n-alert>
    <table v-if="runs.length" class="data-table list-table">
      <thead>
        <tr>
          <th>时间</th><th>检查点</th><th>数据质量</th><th>组合评级</th>
          <th>持仓数</th><th>候选数</th><th class="action-col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in runs" :key="r.id" class="clickable" @click="router.push(`/runs/${r.id}`)">
          <td data-label="时间">{{ r.timestamp.slice(0, 16).replace('T', ' ') }}</td>
          <td data-label="检查点">{{ r.checkpoint || '—' }}</td>
          <td data-label="数据质量"><span v-if="r.data_quality_grade" class="tag" :class="gradeClass(r.data_quality_grade)">{{ r.data_quality_grade }}</span><span v-else>—</span></td>
          <td data-label="组合评级">{{ r.pm_rating || '—' }}</td>
          <td data-label="持仓数">{{ r.holdings_count }}</td>
          <td data-label="候选数">{{ r.candidates_count }}</td>
          <td data-label="操作" class="action-cell" @click.stop>
            <n-popconfirm
              positive-text="删除"
              negative-text="取消"
              :show-icon="false"
              @positive-click="() => removeRun(r.id)"
            >
              <template #trigger>
                <n-button
                  quaternary
                  circle
                  type="error"
                  :loading="deletingId === r.id"
                  :disabled="deletingId !== null && deletingId !== r.id"
                  aria-label="删除决策"
                >
                  <template #icon><Trash2 :size="17" /></template>
                </n-button>
              </template>
              删除这条决策记录？相关持仓快照、论点和候选会一起删除。
            </n-popconfirm>
          </td>
        </tr>
      </tbody>
    </table>
    <n-empty v-else-if="!loading" description="暂无决策记录。先在 skill 执行一次并上传。" class="py-8" />
  </div>
</template>

<style scoped>
.list-card {
  overflow: hidden;
}

.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.list-head h3 {
  margin-bottom: 0;
}

.count-pill {
  display: inline-flex;
  min-height: 32px;
  align-items: center;
  justify-content: center;
  border: 1px solid color-mix(in srgb, var(--app-primary) 26%, var(--app-border));
  border-radius: 999px;
  background: var(--app-primary-soft);
  padding: 4px 12px;
  color: var(--app-text);
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
}

.list-toolbar {
  margin-bottom: 18px;
}

.toolbar :deep(.n-input) { width: 180px; }
.toolbar :deep(.n-date-picker) { width: 180px; }
.toolbar :deep(.n-select) { width: 140px; }

.query-button {
  min-width: 88px;
}

.list-table {
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  overflow: hidden;
}

.list-table tbody tr {
  border-left: 3px solid transparent;
}

.list-table tbody tr:hover {
  border-left-color: var(--app-primary);
}

.list-table td:first-child {
  font-weight: 700;
}

.action-col,
.action-cell {
  width: 82px;
  text-align: center;
  white-space: nowrap;
}

.action-cell :deep(.n-button) {
  min-width: 44px;
  min-height: 44px;
}

.action-cell :deep(.n-button:hover) {
  background: color-mix(in srgb, var(--app-danger) 12%, transparent);
}

@media (max-width: 640px) {
  .list-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .toolbar :deep(.n-input),
  .toolbar :deep(.n-date-picker),
  .toolbar :deep(.n-select),
  .toolbar :deep(.n-button) {
    width: 100%;
  }

  .action-cell {
    text-align: left;
  }

  .action-cell :deep(.n-button) {
    border: 1px solid color-mix(in srgb, var(--app-danger) 24%, transparent);
  }
}
</style>
