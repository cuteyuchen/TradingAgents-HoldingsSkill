<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, RefreshCw, ShieldCheck } from 'lucide-vue-next'

import { api } from '../api'
import type { AnalysisRunSummary, ShadowAccount, ShadowPerformance, ShadowValidation } from '../api/types'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import LoadingState from '../components/LoadingState.vue'
import MetricTile from '../components/MetricTile.vue'
import PageHeader from '../components/PageHeader.vue'
import ResearchView from './ResearchView.vue'
import SectionCard from '../components/SectionCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { usePortfolioContext } from '../composables/portfolio'
import { formatCurrency, formatNumber, formatPercent, fmtDateTime } from '../utils/ui'

type HistoryTab = 'performance' | 'research'

const route = useRoute()
const router = useRouter()
const activeTab = ref<HistoryTab>(route.query.tab === 'research' ? 'research' : 'performance')
const loading = ref(false)
const error = ref<unknown>(null)
const runs = ref<AnalysisRunSummary[]>([])
const accounts = ref<ShadowAccount[]>([])
const performance = ref<ShadowPerformance | null>(null)
const validation = ref<ShadowValidation | null>(null)
const selectedAccountId = ref<number | null>(null)

const { portfolios, selectedPortfolioId, selectedPortfolio, loadPortfolios, setSelectedPortfolio } = usePortfolioContext()
const selectedAccount = computed(() => accounts.value.find((item) => item.id === selectedAccountId.value) || null)
const sampleDays = computed(() => performance.value?.sample_days ?? validation.value?.live_sample_days ?? null)
const sampleDaysDisplay = computed(() => sampleDays.value == null || sampleDays.value <= 0 ? '—' : sampleDays.value)
const sampleInsufficient = computed(() => sampleDays.value == null || sampleDays.value < 20)
const historyStatus = computed(() => {
  if (!selectedAccount.value) return '尚未开始模拟跟随'
  if (sampleInsufficient.value) return '尚在积累样本'
  return '持续记录中'
})
const chartLines = computed(() => {
  const rows = [...(performance.value?.snapshots || [])].sort((a, b) => a.trade_date.localeCompare(b.trade_date))
  if (rows.length < 2) return { equity: '', benchmark: '' }
  const equity = rows.map((item) => item.total_equity == null ? null : Number(item.total_equity))
  const benchmark: Array<number | null> = []
  let benchmarkIndex = 1
  for (const item of rows) {
    if (item.benchmark_return == null || !Number.isFinite(Number(item.benchmark_return))) benchmark.push(null)
    else {
      benchmarkIndex *= 1 + Number(item.benchmark_return)
      benchmark.push(benchmarkIndex * 100)
    }
  }
  const knownEquity = equity.filter((item): item is number => item != null && Number.isFinite(item))
  if (knownEquity.length !== rows.length) return { equity: '', benchmark: '' }
  const normalizedEquity = equity.map((item) => item! / knownEquity[0] * 100)
  const combined = [...normalizedEquity, ...benchmark.filter((item): item is number => item != null && Number.isFinite(item))]
  const min = Math.min(...combined)
  const max = Math.max(...combined)
  const span = max - min || 1
  const points = (values: Array<number | null>) => values
    .map((value, index) => value == null || !Number.isFinite(value) ? null : `${(index / (values.length - 1)) * 100},${96 - ((value - min) / span) * 76}`)
    .filter((value): value is string => Boolean(value))
    .join(' ')
  return { equity: points(normalizedEquity), benchmark: points(benchmark) }
})

function missing(value: unknown, formatter: (input: any) => string): string {
  return value === null || value === undefined || value === '' ? '—' : formatter(value)
}
function money(value: unknown): string { return missing(value, (input) => formatCurrency(input, 2)) }
function percent(value: unknown): string { return missing(value, (input) => formatPercent(input)) }
function number(value: unknown): string { return missing(value, (input) => formatNumber(input, 0)) }
function date(value?: string | null): string { return value ? fmtDateTime(value) : '—' }
function statusType(value?: string | null): 'success' | 'warning' | 'error' | 'info' {
  const normalized = String(value || '').toUpperCase()
  if (['A', 'VALID', 'COMPLETED', 'SUCCEEDED', 'FULL', 'READY'].includes(normalized)) return 'success'
  if (['B', 'PARTIAL', 'DEGRADED', 'INSUFFICIENT', 'INSUFFICIENT_DATA'].includes(normalized)) return 'warning'
  if (['C', 'D', 'F', 'FAILED', 'BLOCKED', 'DATA_GAP'].includes(normalized)) return 'error'
  return 'info'
}
function gradeLabel(value?: string | null): string { return value || '—' }

async function loadPerformance(accountId: number | null) {
  performance.value = accountId ? await api.getShadowPerformance(accountId) : null
}

async function load() {
  loading.value = true
  error.value = null
  try {
    await loadPortfolios()
    const requestedPortfolio = Number(route.query.portfolio)
    if (requestedPortfolio && portfolios.value.some((item) => item.id === requestedPortfolio)) setSelectedPortfolio(requestedPortfolio)
    const portfolioId = selectedPortfolioId.value
    if (!portfolioId) {
      runs.value = []
      accounts.value = []
      performance.value = null
      validation.value = null
      selectedAccountId.value = null
      return
    }
    const [runRows, accountRows, validationRow] = await Promise.all([
      api.listRuns(portfolioId),
      api.listShadowAccounts(portfolioId),
      api.getShadowValidation(portfolioId),
    ])
    runs.value = runRows
    accounts.value = accountRows
    validation.value = validationRow
    const next = accountRows.find((item) => item.id === selectedAccountId.value) || accountRows.find((item) => item.status === 'ACTIVE') || accountRows[0] || null
    selectedAccountId.value = next?.id || null
    await loadPerformance(next?.id || null)
  } catch (reason) {
    error.value = reason
  } finally {
    loading.value = false
  }
}

async function changeAccount(id: number | null) {
  selectedAccountId.value = id
  try {
    await loadPerformance(id)
  } catch (reason) {
    error.value = reason
  }
}

function changeTab(tab: HistoryTab) {
  activeTab.value = tab
  void router.replace({ name: 'history', query: tab === 'research' ? { ...route.query, tab: 'research' } : { ...route.query, tab: undefined } })
}

function openRun(run: AnalysisRunSummary) {
  void router.push({ name: 'analysis', query: { run: run.id, portfolio: selectedPortfolioId.value || undefined } })
}

watch(selectedPortfolioId, (value, previous) => {
  if (value !== previous) void load()
})
watch(() => route.query.tab, (value) => {
  activeTab.value = value === 'research' ? 'research' : 'performance'
})

onMounted(() => void load())
</script>

<template>
  <section class="workbench-page history-page">
    <PageHeader title="历史" description="复盘已经发生的决策，了解模拟跟随和研究证据。">
      <template #actions>
        <n-button secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
      </template>
    </PageHeader>

    <ErrorState v-if="error" :error="error" @retry="load" />
    <LoadingState v-else-if="loading && !runs.length && !accounts.length" message="正在读取历史记录" />

    <n-tabs :value="activeTab" type="line" @update:value="changeTab">
      <n-tab-pane name="performance" tab="历史表现">
        <div class="history-stack">
          <SectionCard title="历史表现" :description="selectedPortfolio ? `${selectedPortfolio.name} · 只展示真实 Shadow 记录与已保存分析` : '选择组合后查看真实记录'">
            <template #actions>
              <n-select v-if="accounts.length > 1" :value="selectedAccountId" size="small" :options="accounts.map((item) => ({ label: item.name, value: item.id }))" aria-label="选择模拟账户" @update:value="changeAccount" />
              <StatusBadge v-if="selectedAccount" :status="selectedAccount.status" :label="historyStatus" />
            </template>
            <EmptyState v-if="!selectedPortfolio" title="还没有可复盘的组合" description="先配置组合并确认第一份持仓快照，之后才能积累历史记录。">
              <template #action><n-button type="primary" @click="router.push({ name: 'holdings', query: { action: 'update' } })">导入持仓</n-button></template>
            </EmptyState>
            <EmptyState v-else-if="!selectedAccount" title="还没有模拟跟随记录" description="历史表现需要一个独立的模拟账户；它不会发送真实订单，也不会改变生产组合。">
              <template #action><n-button type="primary" @click="router.push({ name: 'simulation' })">去模拟</n-button></template>
            </EmptyState>
            <template v-else>
              <div class="metric-grid six">
                <MetricTile label="累计收益" :value="percent(performance?.cumulative_return)" tone="positive" />
                <MetricTile label="同期基准" :value="percent(performance?.benchmark_return)" />
                <MetricTile label="超额收益" :value="percent(performance?.excess_return)" tone="positive" />
                <MetricTile label="最大回撤" :value="percent(performance?.max_drawdown)" tone="risk" />
                <MetricTile label="样本天数" :value="sampleDaysDisplay" />
                <MetricTile label="质量" :value="gradeLabel(performance?.performance_quality)" />
              </div>
              <div class="history-note"><ShieldCheck :size="16" /><span>{{ sampleInsufficient ? '样本不足，暂不能判断策略效果。' : '结果来自已记录的 Shadow snapshot，不计算后端未提供的胜率。' }}</span><small>最近更新：{{ date(performance?.snapshots?.[0]?.trade_date) }}</small></div>
              <div v-if="chartLines.equity" class="history-chart panel-card">
                <div class="chart-heading"><div><h3>模拟净值与基准</h3><p>仅使用真实 daily snapshot；缺少时序数据时不绘制图表。起始值归一为 100。</p></div><span>Shadow Equity vs Benchmark</span></div>
                <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="模拟净值与基准曲线">
                  <line x1="0" y1="96" x2="100" y2="96" />
                  <polyline :points="chartLines.equity" fill="none" stroke="var(--primary)" stroke-width="1.8" vector-effect="non-scaling-stroke" />
                  <polyline v-if="chartLines.benchmark" :points="chartLines.benchmark" fill="none" stroke="var(--text-muted)" stroke-width="1.2" stroke-dasharray="3 2" vector-effect="non-scaling-stroke" />
                </svg>
                <div class="chart-legend"><span><i class="legend-equity" />模拟净值</span><span v-if="chartLines.benchmark"><i class="legend-benchmark" />基准</span></div>
              </div>
              <TechnicalDetails title="历史表现技术详情"><pre>{{ JSON.stringify({ account: selectedAccount, performance, validation }, null, 2) }}</pre></TechnicalDetails>
            </template>
          </SectionCard>

          <SectionCard title="分析历史" :description="`${runs.length} 条已保存分析记录`">
            <div v-if="runs.length" class="history-run-list">
              <button v-for="run in runs" :key="run.id" class="history-run-row" @click="openRun(run)">
                <div><strong>{{ run.summary || `分析记录 #${run.id}` }}</strong><small>#{{ run.id }} · {{ date(run.created_at) }}</small></div>
                <div class="history-run-meta"><StatusBadge :status="run.data_quality_grade || 'UNKNOWN'" :label="`质量 ${run.data_quality_grade || '—'}`" /><span>{{ run.final_rating || '结论待查看' }}</span><ArrowRight :size="16" /></div>
              </button>
            </div>
            <EmptyState v-else title="还没有已保存的分析" description="完成一次组合分析后，这里会留下可回看的结论与证据。">
              <template #action><n-button type="primary" @click="router.push({ name: 'analysis' })">开始分析</n-button></template>
            </EmptyState>
          </SectionCard>
        </div>
      </n-tab-pane>
      <n-tab-pane name="research" tab="策略研究">
        <div class="research-host"><ResearchView /></div>
      </n-tab-pane>
    </n-tabs>
  </section>
</template>

<style scoped>
.history-stack { display: grid; gap: 18px; }
.metric-grid { display: grid; gap: 14px; }
.metric-grid.six { grid-template-columns: repeat(6, minmax(0, 1fr)); }
.history-note { display: flex; align-items: center; gap: 8px; margin-top: 18px; border-left: 3px solid var(--warning); background: color-mix(in srgb, var(--warning) 8%, var(--surface)); padding: 10px 12px; color: var(--warning); }
.history-note span { color: var(--text); }
.history-note small { margin-left: auto; color: var(--text-muted); }
.history-chart { display: grid; gap: 12px; margin-top: 18px; padding: 16px 18px; }
.chart-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.chart-heading h3 { margin: 0; font-size: 15px; }
.chart-heading p, .chart-heading > span { margin: 4px 0 0; color: var(--text-muted); font-size: 11px; }
.history-chart svg { width: 100%; height: 170px; border-bottom: 1px solid var(--border); background: linear-gradient(to bottom, transparent 24%, var(--border) 25%, transparent 26%, transparent 49%, var(--border) 50%, transparent 51%, transparent 74%, var(--border) 75%, transparent 76%); }
.history-chart line { stroke: var(--border-strong); stroke-width: .5; }
.chart-legend { display: flex; gap: 14px; color: var(--text-muted); font-size: 11px; }
.chart-legend span { display: inline-flex; align-items: center; gap: 6px; }
.chart-legend i { display: inline-block; width: 18px; height: 2px; background: var(--primary); }
.chart-legend .legend-benchmark { background: var(--text-muted); }
.history-run-list { display: grid; }
.history-run-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; width: 100%; border: 0; border-top: 1px solid var(--border); background: transparent; padding: 13px 0; color: inherit; text-align: left; cursor: pointer; }
.history-run-row:first-child { border-top: 0; padding-top: 0; }
.history-run-row:hover { background: var(--row-hover); }
.history-run-row > div:first-child { display: grid; gap: 4px; min-width: 0; }
.history-run-row small, .history-run-meta > span { color: var(--text-muted); font-size: 11px; }
.history-run-meta { display: flex; align-items: center; gap: 10px; white-space: nowrap; }
.research-host { min-width: 0; }
@media (max-width: 1050px) { .metric-grid.six { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 620px) { .metric-grid.six { grid-template-columns: repeat(2, minmax(0, 1fr)); }.history-note { align-items: flex-start; flex-wrap: wrap; }.history-note small { width: 100%; margin-left: 24px; }.chart-heading { align-items: flex-start; flex-direction: column; }.history-run-row, .history-run-meta { align-items: flex-start; flex-direction: column; }.history-run-meta { gap: 5px; } }
@media (max-width: 420px) { .metric-grid.six { grid-template-columns: 1fr; } }
</style>
