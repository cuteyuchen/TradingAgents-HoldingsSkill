<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import type { DataTableColumns } from 'naive-ui'
import { useRoute } from 'vue-router'
import { api } from '../api'
import type { Holding, HoldingTimeline } from '../api/types'
import AlphaChart from '../components/AlphaChart.vue'
import { emptyText, fmtDateTime, fmtPct, pctClass, renderCode, renderPct } from '../utils/ui'

const route = useRoute()
const portfolio = ref<{ run_id: number | null; timestamp: string | null; holdings: Holding[] }>({ run_id: null, timestamp: null, holdings: [] })
const timeline = ref<HoldingTimeline | null>(null)
const selectedCode = ref<string>('')
const err = ref('')

async function loadPortfolio() {
  err.value = ''
  try {
    portfolio.value = await api.currentPortfolio()
    if (!selectedCode.value && portfolio.value.holdings.length) {
      selectedCode.value = portfolio.value.holdings[0].code
    }
    if (selectedCode.value) await loadTimeline(selectedCode.value)
  } catch (e) {
    err.value = (e as Error).message
  }
}

async function loadTimeline(code: string) {
  try {
    timeline.value = await api.holdingTimeline(code, 10)
  } catch (e) {
    err.value = (e as Error).message
  }
}

watch(() => route.params.code, (c) => { if (c) { selectedCode.value = c as string; loadTimeline(c as string) } })
const columns: DataTableColumns<Holding> = [
  { title: '代码', key: 'code', width: 108, render: (row) => renderCode(row.code) },
  { title: '名称', key: 'name', minWidth: 160, render: (row) => row.name || emptyText },
  { title: '现价', key: 'price', width: 110, render: (row) => row.price ?? emptyText },
  { title: '成本', key: 'cost', width: 110, render: (row) => row.cost ?? emptyText },
  { title: '盈亏', key: 'pnl', width: 120, render: (row) => renderPct(row.pnl) },
  { title: 'Alpha', key: 'alpha', width: 120, render: (row) => renderPct(row.alpha) },
]

const rowProps = (row: Holding) => ({
  class: 'clickable',
  onClick: () => {
    selectedCode.value = row.code
    void loadTimeline(row.code)
  },
})

const rowClassName = (row: Holding): string => selectedCode.value === row.code ? 'selected-row' : ''

onMounted(loadPortfolio)
</script>

<template>
  <n-alert v-if="err" type="error" :show-icon="false" class="mb-4">{{ err }}</n-alert>
  <n-card :title="`当前持仓（${portfolio.timestamp ? fmtDateTime(portfolio.timestamp) : '无'}）`">
    <n-data-table
      :columns="columns"
      :data="portfolio.holdings"
      :bordered="false"
      :single-line="false"
      :scroll-x="730"
      :row-props="rowProps"
      :row-class-name="rowClassName"
    />
  </n-card>
  <n-card v-if="selectedCode" :title="`${selectedCode} 收益 / Alpha 曲线`">
    <AlphaChart :points="timeline?.points || []" />
  </n-card>
  <n-card v-if="timeline" title="最近决策">
    <n-descriptions :column="4" label-placement="left" bordered size="small">
      <n-descriptions-item label="评级">{{ timeline.verdict?.rating || '—' }}</n-descriptions-item>
      <n-descriptions-item label="动作">{{ timeline.proposal?.action || '—' }}</n-descriptions-item>
      <n-descriptions-item label="触发价">{{ timeline.proposal?.trigger_price ?? '—' }}</n-descriptions-item>
      <n-descriptions-item label="止损">{{ timeline.proposal?.stop_loss || '—' }}</n-descriptions-item>
    </n-descriptions>
  </n-card>
</template>

<style scoped>
:deep(.selected-row td) {
  background: var(--app-row-hover);
}

:deep(.n-card + .n-card) {
  margin-top: 16px;
}

@media (max-width: 768px) {
  :deep(.n-descriptions) {
    overflow-x: auto;
  }
}
</style>
