<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NPopconfirm, type DataTableColumns, useMessage } from 'naive-ui'
import { api } from '../api'
import type { HealthStatus, WatchlistItem } from '../api/types'
import { emptyText, fmtDateTime, renderCode, renderInstrument, renderMuted, renderStatus } from '../utils/ui'

const message = useMessage()
const list = ref<WatchlistItem[]>([])
const health = ref<HealthStatus[]>([])
const err = ref('')
const submitting = ref(false)
const form = ref<WatchlistItem>({ code: '', name: '', cadence: '10:00', enabled: true })
const cadenceOptions = [
  { label: '09:25 开盘竞价', value: '09:25' },
  { label: '10:00 早盘确认', value: '10:00' },
  { label: '12:00 午间复盘', value: '12:00' },
  { label: '14:30 尾盘风控', value: '14:30' },
]

const healthByCode = computed(() => {
  const map = new Map<string, HealthStatus>()
  for (const h of health.value) {
    if (h.code && (!map.has(h.code) || h.degraded)) map.set(h.code, h)
  }
  return map
})

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
  const code = form.value.code.trim().toUpperCase()
  if (!code) {
    message.error('请输入股票或 ETF 代码')
    return
  }
  submitting.value = true
  err.value = ''
  try {
    await api.addWatchlist({
      ...form.value,
      code,
      name: form.value.name?.trim() || undefined,
    })
    form.value = { code: '', name: '', cadence: '10:00', enabled: true }
    await load()
    message.success('自选股已保存')
  } catch (e) {
    err.value = (e as Error).message
    message.error(err.value)
  } finally {
    submitting.value = false
  }
}

async function remove(code: string) {
  try {
    await api.removeWatchlist(code)
    await load()
    message.success('已删除自选股')
  } catch (e) {
    err.value = (e as Error).message
    message.error(err.value)
  }
}

const watchlistColumns: DataTableColumns<WatchlistItem> = [
  { title: '标的', key: 'code', minWidth: 190, render: (row) => renderInstrument(row.name, row.code) },
  { title: '检查点', key: 'cadence', width: 120, render: (row) => row.cadence || emptyText },
  { title: '状态', key: 'enabled', width: 110, render: (row) => renderStatus(row.enabled ? '启用' : '已停用') },
  {
    title: '停用/降级原因',
    key: 'note',
    minWidth: 240,
    render: (row) => renderMuted(healthByCode.value.get(row.code)?.note),
  },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render: (row) =>
      h(NPopconfirm, {
        positiveText: '删除',
        negativeText: '取消',
        showIcon: false,
        onPositiveClick: () => remove(row.code),
      }, {
        trigger: () => h(NButton, { size: 'small', secondary: true, type: 'error' }, { default: () => '删除' }),
        default: () => `删除 ${row.code}？`,
      }),
  },
]

const healthColumns: DataTableColumns<HealthStatus> = [
  { title: '代码', key: 'code', width: 110, render: (row) => row.code ? renderCode(row.code) : '全局' },
  { title: '检查点', key: 'checkpoint', width: 110 },
  { title: '连续失败', key: 'consecutive_failures', width: 110 },
  { title: '状态', key: 'degraded', width: 110, render: (row) => renderStatus(row.degraded ? '降级' : '正常') },
  { title: '最近成功', key: 'last_success_at', width: 150, render: (row) => renderMuted(fmtDateTime(row.last_success_at)) },
  { title: '最近失败', key: 'last_failure_at', width: 150, render: (row) => renderMuted(fmtDateTime(row.last_failure_at)) },
  { title: '原因', key: 'note', minWidth: 240, render: (row) => renderMuted(row.note) },
]

const healthRowClassName = (row: HealthStatus): string => row.degraded ? 'degraded-row' : ''

onMounted(load)
</script>

<template>
  <n-card title="自选股 / 定时分析">
    <n-alert v-if="err" type="error" :show-icon="false" class="mb-3">{{ err }}</n-alert>
    <n-form label-placement="top" class="mb-4">
      <n-grid :cols="5" :x-gap="12" responsive="screen" item-responsive>
        <n-form-item-gi label="代码" required :span="1">
          <n-input v-model:value="form.code" placeholder="如 600519 或 513040" @keyup.enter="add" />
        </n-form-item-gi>
        <n-form-item-gi label="名称" :span="1">
          <n-input v-model:value="form.name" placeholder="可选" @keyup.enter="add" />
        </n-form-item-gi>
        <n-form-item-gi label="检查点" :span="1">
          <n-select v-model:value="form.cadence" :options="cadenceOptions" />
        </n-form-item-gi>
        <n-form-item-gi label="启用" :span="1">
          <n-switch v-model:value="form.enabled" />
        </n-form-item-gi>
        <n-form-item-gi label="操作" :span="1">
          <n-button type="primary" block :loading="submitting" @click="add">添加 / 更新</n-button>
        </n-form-item-gi>
      </n-grid>
    </n-form>

    <n-data-table
      :columns="watchlistColumns"
      :data="list"
      :bordered="false"
      :single-line="false"
      :scroll-x="820"
    />
  </n-card>

  <n-card title="检查点健康度（连续失败降级）" class="mt-4">
    <n-data-table
      :columns="healthColumns"
      :data="health"
      :bordered="false"
      :single-line="false"
      :scroll-x="1050"
      :row-class-name="healthRowClassName"
    />
  </n-card>
</template>

<style scoped>
:deep(.degraded-row td) {
  background: color-mix(in srgb, var(--app-warning) 12%, transparent);
}

@media (max-width: 640px) {
  :deep(.n-grid) {
    grid-template-columns: 1fr !important;
  }
}
</style>
