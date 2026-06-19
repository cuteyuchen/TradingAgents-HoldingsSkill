<script setup lang="ts">
import {h, onMounted, ref} from 'vue'
import {useRouter} from 'vue-router'
import {Trash2} from 'lucide-vue-next'
import {NButton, NPopconfirm, type DataTableColumns, useMessage} from 'naive-ui'
import {api} from '../api'
import type {RunSummary} from '../api/types'
import {fmtDateTime, ratingLabel, renderGrade} from '../utils/ui'

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
  {label: '全部检查点', value: ''},
  {label: '09:25', value: '09:25'},
  {label: '10:00', value: '10:00'},
  {label: '12:00', value: '12:00'},
  {label: '14:30', value: '14:30'},
]
const gradeOptions = [
  {label: '全部质量', value: ''},
  {label: 'A', value: 'A'},
  {label: 'B', value: 'B'},
  {label: 'C', value: 'C'},
  {label: 'D', value: 'D'},
  {label: 'F', value: 'F'},
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

const columns: DataTableColumns<RunSummary> = [
  {title: '时间', key: 'timestamp', render: (row) => fmtDateTime(row.timestamp)},
  {title: '检查点', key: 'checkpoint', render: (row) => row.checkpoint || '—'},
  {title: '数据质量', key: 'data_quality_grade', render: (row) => renderGrade(row.data_quality_grade)},
  {title: '组合评级', key: 'pm_rating', render: (row) => ratingLabel(row.pm_rating)},
  {title: '持仓数', key: 'holdings_count'},
  {title: '候选数', key: 'candidates_count',},
  {
    title: '操作',
    key: 'actions',
    width: 86,
    render: (row) =>
        h('div', {onClick: (event: MouseEvent) => event.stopPropagation()}, [
          h(NPopconfirm, {
            positiveText: '删除',
            negativeText: '取消',
            showIcon: false,
            onPositiveClick: () => removeRun(row.id),
          }, {
            trigger: () => h(NButton, {
              quaternary: true,
              circle: true,
              type: 'error',
              loading: deletingId.value === row.id,
              disabled: deletingId.value !== null && deletingId.value !== row.id,
              ariaLabel: '删除决策',
            }, {icon: () => h(Trash2, {size: 17})}),
            default: () => '删除这条决策记录？相关持仓快照、论点和候选会一起删除。',
          }),
        ]),
  },
]

const rowProps = (row: RunSummary) => ({
  class: 'clickable',
  onClick: () => router.push(`/runs/${row.id}`),
})

onMounted(load)
</script>

<template>
  <n-card class="list-card" title="决策列表">
    <template #header-extra>
      <n-tag type="info" round :bordered="false" aria-live="polite">共 {{ runs.length }} 条</n-tag>
    </template>
    <div class="toolbar list-toolbar" aria-label="决策筛选">
      <n-input v-model:value="codeFilter" clearable placeholder="按代码筛选，如 600519" @keyup.enter="load"/>
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
      <n-select v-model:value="checkpointFilter" :options="checkpointOptions"/>
      <n-select v-model:value="gradeFilter" :options="gradeOptions"/>
      <n-button type="primary" class="query-button" :loading="loading" @click="load">查询</n-button>
    </div>
    <n-alert v-if="err" type="error" :show-icon="false" class="mb-3">{{ err }}</n-alert>
    <n-data-table
        v-if="runs.length || loading"
        :columns="columns"
        :data="runs"
        :loading="loading"
        :bordered="false"
        :single-line="false"
        :scroll-x="850"
        :row-props="rowProps"
    />
    <n-empty v-else description="暂无决策记录。先在 skill 执行一次并上传。" class="py-8"/>
  </n-card>
</template>

<style scoped>
.list-card {
  overflow: hidden;
}

.list-toolbar {
  margin-bottom: 18px;
}

.toolbar :deep(.n-input) {
  width: 180px;
}

.toolbar :deep(.n-date-picker) {
  width: 180px;
}

.toolbar :deep(.n-select) {
  width: 140px;
}

.query-button {
  min-width: 88px;
}

@media (max-width: 640px) {
  .toolbar :deep(.n-input),
  .toolbar :deep(.n-date-picker),
  .toolbar :deep(.n-select),
  .toolbar :deep(.n-button) {
    width: 100%;
  }
}
</style>
