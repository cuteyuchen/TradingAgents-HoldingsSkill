<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  CalendarRange,
  Database,
  Download,
  FlaskConical,
  Play,
  RefreshCw,
  ShieldAlert,
  Square,
} from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type {
  BacktestRun,
  CalibrationReport,
  Portfolio,
  ReplayAvailabilityItem,
  ReplayAvailabilityManifest,
  ReplayMode,
  ResearchScope,
} from '../api/types'

const message = useMessage()
const loading = ref(false)
const running = ref(false)
const availability = ref<ReplayAvailabilityManifest | null>(null)
const portfolios = ref<Portfolio[]>([])
const runs = ref<BacktestRun[]>([])
const reports = ref<CalibrationReport[]>([])
const selectedRun = ref<BacktestRun | null>(null)
const selectedReport = ref<CalibrationReport | null>(null)

function isoDate(value: Date): string {
  return value.toISOString().slice(0, 10)
}

const endDate = isoDate(new Date())
const start = new Date()
start.setDate(start.getDate() - 60)

const runForm = reactive({
  scope: 'MARKET' as ResearchScope,
  replay_mode: 'PRODUCTION_REPLAY' as ReplayMode,
  start_date: isoDate(start),
  end_date: endDate,
  portfolio_id: null as number | null,
  horizons: [1, 5, 20] as number[],
  experiment_name: '',
})
const calibrationForm = reactive({
  target_parameter: 'decision_edge_threshold',
  scope: null as ResearchScope | null,
  replay_mode: 'PRODUCTION_REPLAY' as ReplayMode,
  start_date: isoDate(start),
  end_date: endDate,
  portfolio_id: null as number | null,
  horizons: [1, 5, 20] as number[],
  parameter_grid: '',
})

const scopeOptions = [
  { label: 'Market Score', value: 'MARKET' },
  { label: 'Candidate', value: 'CANDIDATE' },
  { label: 'Portfolio Decision', value: 'PORTFOLIO_DECISION' },
  { label: 'Decision Memory', value: 'MEMORY_DECISION' },
  { label: 'Bar Factor Diagnostic', value: 'BAR_FACTOR' },
]
const replayModeOptions = [
  { label: 'Production Replay', value: 'PRODUCTION_REPLAY' },
  { label: 'Deterministic Recompute', value: 'DETERMINISTIC_RECOMPUTE' },
  { label: 'Bar-only Diagnostic', value: 'BAR_ONLY_DIAGNOSTIC' },
]
const horizonOptions = [1, 5, 10, 20, 60, 120]
const availabilityKeys = [
  ['market_score', 'Market Score'],
  ['candidate_runs', 'Candidate'],
  ['portfolio_snapshots', 'Portfolio'],
  ['fundamentals', 'Fundamental'],
  ['valuation', 'Valuation'],
  ['daily_bars', 'DailyBar'],
  ['decision_memory', 'Memory'],
] as const

const availabilityRows = computed(() => availabilityKeys.map(([key, label]) => ({
  key,
  label,
  item: (availability.value?.[key] as ReplayAvailabilityItem | undefined) || null,
})))
const survivorship = computed(() => availability.value?.survivorship as ReplayAvailabilityItem | undefined)
const portfolioOptions = computed(() => [
  { label: '全局研究', value: null },
  ...portfolios.value.map((item) => ({ label: item.name, value: item.id })),
])
const selectedMetricRows = computed(() => (selectedRun.value?.metric_slices || []).slice(0, 30))
const canCancel = computed(() => selectedRun.value && ['QUEUED', 'RUNNING'].includes(selectedRun.value.status))

function statusType(status?: string | null): 'success' | 'warning' | 'error' | 'info' | 'default' {
  const value = String(status || '').toUpperCase()
  if (['VALID', 'FULL', 'COMPLETED', 'PASS'].includes(value)) return 'success'
  if (['PARTIAL', 'DEGRADED', 'DIAGNOSTIC_ONLY', 'DATA_GAP', 'INSUFFICIENT', 'INSUFFICIENT_DATA', 'INSUFFICIENT_EVIDENCE'].includes(value)) return 'warning'
  if (['FAILED', 'BLOCKED', 'LEAKAGE_BLOCKED', 'INVALIDATED', 'REJECT_CHANGE'].includes(value)) return 'error'
  return 'info'
}

function statusText(status?: string | null): string {
  const labels: Record<string, string> = {
    FULL: 'FULL', PARTIAL: 'PARTIAL', DIAGNOSTIC_ONLY: 'DIAGNOSTIC_ONLY', DATA_GAP: 'DATA_GAP',
    UNSUPPORTED: 'UNSUPPORTED', LEAKAGE_BLOCKED: 'LEAKAGE_BLOCKED', COMPLETED: 'COMPLETED',
    RUNNING: 'RUNNING', QUEUED: 'QUEUED', CANCELLED: 'CANCELLED', FAILED: 'FAILED',
    INVALIDATED: 'INVALIDATED', INSUFFICIENT_DATA: 'INSUFFICIENT_DATA',
    KEEP_CURRENT: 'KEEP_CURRENT', CONSIDER_CHANGE: 'CONSIDER_CHANGE',
    INSUFFICIENT_EVIDENCE: 'INSUFFICIENT_EVIDENCE', REJECT_CHANGE: 'REJECT_CHANGE',
  }
  return labels[String(status || '').toUpperCase()] || String(status || 'UNKNOWN')
}

function pct(value?: number | null): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}

function fmt(value?: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

function portfolioFor(scope: ResearchScope, value: number | null): number | null {
  return scope === 'MARKET' ? null : value
}

async function load() {
  loading.value = true
  try {
    const values = await Promise.all([
      api.listPortfolios(),
      api.getReplayAvailability({ start_date: runForm.start_date, end_date: runForm.end_date, portfolio_id: runForm.portfolio_id || undefined }),
      api.listBacktests(),
      api.listCalibrations(),
    ])
    portfolios.value = values[0]
    availability.value = values[1]
    runs.value = values[2]
    reports.value = values[3]
    if (selectedRun.value) {
      selectedRun.value = await api.getBacktest(selectedRun.value.id)
    } else if (runs.value[0]) {
      selectedRun.value = runs.value[0]
    }
    if (selectedReport.value) {
      selectedReport.value = await api.getCalibration(selectedReport.value.id)
    } else if (reports.value[0]) {
      selectedReport.value = reports.value[0]
    }
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

async function refreshAvailability() {
  try {
    availability.value = await api.getReplayAvailability({
      start_date: runForm.start_date,
      end_date: runForm.end_date,
      portfolio_id: runForm.portfolio_id || undefined,
    })
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function createRun() {
  running.value = true
  try {
    const run = await api.createBacktest({
      scope: runForm.scope,
      replay_mode: runForm.replay_mode,
      start_date: runForm.start_date,
      end_date: runForm.end_date,
      portfolio_id: portfolioFor(runForm.scope, runForm.portfolio_id),
      horizons: runForm.horizons,
      experiment: runForm.experiment_name ? { name: runForm.experiment_name } : null,
      bootstrap_iterations: 500,
    })
    selectedRun.value = run
    await load()
    message.success(`研究 Run #${run.id} 已完成或进入持久化状态`)
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    running.value = false
  }
}

async function cancelRun() {
  if (!selectedRun.value) return
  try {
    selectedRun.value = await api.cancelBacktest(selectedRun.value.id)
    await load()
    message.info('研究 Run 已取消')
  } catch (error) {
    message.error((error as Error).message)
  }
}

function selectRun(run: BacktestRun) {
  selectedRun.value = run
  void api.getBacktest(run.id).then((value) => { selectedRun.value = value }).catch((error) => message.error(error.message))
}

function parseGrid(value: string): Array<number | string> | undefined {
  const entries = value.split(',').map((item) => item.trim()).filter(Boolean)
  if (!entries.length) return undefined
  return entries.map((item) => {
    const numeric = Number(item)
    return Number.isFinite(numeric) ? numeric : item
  })
}

async function createCalibration() {
  running.value = true
  try {
    const report = await api.createCalibration({
      target_parameter: calibrationForm.target_parameter,
      scope: calibrationForm.scope,
      replay_mode: calibrationForm.replay_mode,
      start_date: calibrationForm.start_date,
      end_date: calibrationForm.end_date,
      portfolio_id: portfolioFor(calibrationForm.scope || 'CANDIDATE', calibrationForm.portfolio_id),
      horizons: calibrationForm.horizons,
      parameter_grid: parseGrid(calibrationForm.parameter_grid),
      bootstrap_iterations: 500,
    })
    selectedReport.value = report
    await load()
    message.success('Calibration Report 已生成')
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    running.value = false
  }
}

function selectReport(report: CalibrationReport) {
  selectedReport.value = report
  void api.getCalibration(report.id).then((value) => { selectedReport.value = value }).catch((error) => message.error(error.message))
}

function downloadJson(label: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${label}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

onMounted(() => void load())
</script>

<template>
  <section class="research-page">
    <header class="research-header">
      <div>
        <div class="research-eyebrow"><FlaskConical :size="15" /> OFFLINE RESEARCH</div>
        <h1>历史回放与参数校准</h1>
        <p>只读取已持久化事实，输出可复现的 Backtest Evidence 与人工评审报告。</p>
      </div>
      <n-button secondary :loading="loading" @click="load">
        <template #icon><RefreshCw :size="16" /></template>
        刷新研究状态
      </n-button>
    </header>

    <div class="research-grid research-grid-top">
      <n-card class="panel-card" :bordered="false">
        <template #header>
          <div class="card-heading"><Database :size="17" /><span>Data Availability</span></div>
        </template>
        <div class="availability-list">
          <div v-for="row in availabilityRows" :key="row.key" class="availability-row">
            <div class="availability-name">
              <strong>{{ row.label }}</strong>
              <span>{{ row.item?.row_count ?? 0 }} rows · {{ pct(row.item?.coverage) }}</span>
            </div>
            <n-tag size="small" :type="statusType(row.item?.status)">{{ statusText(row.item?.status) }}</n-tag>
          </div>
        </div>
        <n-alert v-if="survivorship?.status === 'LEAKAGE_BLOCKED'" class="research-alert" type="warning" :show-icon="true">
          当前 SecurityMaster 没有历史生命周期，Candidate 全市场重建会被标记为 LEAKAGE_BLOCKED。
        </n-alert>
        <div class="availability-foot">
          <span class="muted">Manifest hash</span>
          <code>{{ availability?.data_hash || '—' }}</code>
          <n-button quaternary circle aria-label="刷新可用性" @click="refreshAvailability">
            <template #icon><RefreshCw :size="15" /></template>
          </n-button>
        </div>
      </n-card>

      <n-card class="panel-card" :bordered="false">
        <template #header>
          <div class="card-heading"><CalendarRange :size="17" /><span>Backtest Run</span></div>
        </template>
        <div class="form-grid">
          <label>Scope<n-select v-model:value="runForm.scope" :options="scopeOptions" /></label>
          <label>Replay Mode<n-select v-model:value="runForm.replay_mode" :options="replayModeOptions" /></label>
          <label>开始日期<input v-model="runForm.start_date" type="date" /></label>
          <label>结束日期<input v-model="runForm.end_date" type="date" /></label>
          <label>研究组合<n-select v-model:value="runForm.portfolio_id" :options="portfolioOptions" /></label>
          <label>Experiment 名称<n-input v-model:value="runForm.experiment_name" placeholder="threshold sensitivity" /></label>
        </div>
        <div class="horizon-line">
          <span class="field-label">Forward Horizon</span>
          <n-checkbox-group v-model:value="runForm.horizons">
            <n-space :size="10">
              <n-checkbox v-for="horizon in horizonOptions" :key="horizon" :value="horizon">{{ horizon }}d</n-checkbox>
            </n-space>
          </n-checkbox-group>
        </div>
        <n-alert class="research-alert" type="info" :show-icon="true">
          Backtest 使用 {{ runForm.replay_mode }}；结果不会写入 DecisionMemory、TradeLedger 或生产配置。
        </n-alert>
        <div class="form-actions">
          <n-button type="primary" :loading="running" @click="createRun">
            <template #icon><Play :size="16" /></template>
            启动研究 Run
          </n-button>
        </div>
      </n-card>
    </div>

    <n-card class="panel-card" :bordered="false">
      <template #header>
        <div class="card-heading"><FlaskConical :size="17" /><span>Backtest Runs</span><small>{{ runs.length }} runs</small></div>
      </template>
      <div v-if="!runs.length" class="empty-state">暂无研究 Run。选择范围后启动一次离线回放。</div>
      <div v-else class="run-table">
        <button v-for="run in runs" :key="run.id" class="run-row" :class="{ selected: selectedRun?.id === run.id }" @click="selectRun(run)">
          <span class="run-id">#{{ run.id }}</span>
          <span class="run-main"><strong>{{ run.scope }}</strong><small>{{ run.start_date }} → {{ run.end_date }} · {{ run.replay_mode }}</small></span>
          <span class="run-samples">N={{ run.sample_count }}<small>{{ run.unique_trade_dates }} dates</small></span>
          <n-tag size="small" :type="statusType(run.status)">{{ statusText(run.status) }}</n-tag>
        </button>
      </div>
    </n-card>

    <div class="research-grid research-grid-bottom">
      <n-card class="panel-card" :bordered="false">
        <template #header>
          <div class="card-heading"><ShieldAlert :size="17" /><span>Run Evidence</span></div>
        </template>
        <div v-if="!selectedRun" class="empty-state">选择一个 Run 查看 Evidence。</div>
        <template v-else>
          <div class="evidence-summary">
            <div><span>状态</span><n-tag size="small" :type="statusType(selectedRun.status)">{{ statusText(selectedRun.status) }}</n-tag></div>
            <div><span>Quality</span><n-tag size="small" :type="statusType(selectedRun.quality_status)">{{ statusText(selectedRun.quality_status) }}</n-tag></div>
            <div><span>Leakage</span><n-tag size="small" :type="statusType(selectedRun.leakage_status)">{{ statusText(selectedRun.leakage_status) }}</n-tag></div>
            <div><span>Attempt</span><strong>{{ selectedRun.attempt_count }}</strong></div>
          </div>
          <div class="evidence-meta">
            <span>Stage {{ selectedRun.current_stage }} · {{ selectedRun.progress_percent }}%</span>
            <span>Heartbeat {{ fmt(selectedRun.last_heartbeat_at) }}</span>
            <span>Data {{ selectedRun.data_hash }}</span>
          </div>
          <n-alert v-if="selectedRun.error_message" class="research-alert" type="error" :show-icon="true">{{ selectedRun.error_message }}</n-alert>
          <div v-if="selectedRun.known_limitations?.length" class="limitations">
            <span v-for="item in selectedRun.known_limitations" :key="item">{{ item }}</span>
          </div>
          <div class="metric-list">
            <div v-for="item in selectedMetricRows" :key="item.id" class="metric-row">
              <div><strong>{{ item.metric_family }}</strong><small>{{ item.score_bucket || item.stage || 'aggregate' }} · {{ item.horizon ? `${item.horizon}d` : '—' }}</small></div>
              <div class="metric-value"><span>median {{ item.metrics?.median === null || item.metrics?.median === undefined ? '—' : `${(item.metrics.median * 100).toFixed(2)}%` }}</span><small>N={{ item.sample_count }} · {{ item.quality_status }}</small></div>
            </div>
          </div>
          <div class="evidence-actions">
            <n-button quaternary @click="downloadJson(`backtest-${selectedRun.id}`, selectedRun)">
              <template #icon><Download :size="15" /></template>
              导出 JSON
            </n-button>
            <n-button v-if="canCancel" quaternary type="error" @click="cancelRun">
              <template #icon><Square :size="14" /></template>
              取消 Run
            </n-button>
          </div>
        </template>
      </n-card>

      <n-card class="panel-card" :bordered="false">
        <template #header>
          <div class="card-heading"><FlaskConical :size="17" /><span>Calibration Candidate</span><small>人工评审</small></div>
        </template>
        <div class="form-grid">
          <label>Target Parameter<n-input v-model:value="calibrationForm.target_parameter" /></label>
          <label>Scope<n-select v-model:value="calibrationForm.scope" :options="[{ label: '自动推断', value: null }, ...scopeOptions]" /></label>
          <label>开始日期<input v-model="calibrationForm.start_date" type="date" /></label>
          <label>结束日期<input v-model="calibrationForm.end_date" type="date" /></label>
          <label>研究组合<n-select v-model:value="calibrationForm.portfolio_id" :options="portfolioOptions" /></label>
          <label>Safe Grid<n-input v-model:value="calibrationForm.parameter_grid" placeholder="4,5,6,7" /></label>
        </div>
        <div class="horizon-line">
          <span class="field-label">Forward Horizon</span>
          <n-checkbox-group v-model:value="calibrationForm.horizons">
            <n-space :size="10">
              <n-checkbox v-for="horizon in horizonOptions" :key="horizon" :value="horizon">{{ horizon }}d</n-checkbox>
            </n-space>
          </n-checkbox-group>
        </div>
        <n-alert class="research-alert" type="warning" :show-icon="true">
          Test set 只用于最终报告；没有 Apply 按钮，任何参数变更都必须由人工批准后另行治理。
        </n-alert>
        <div class="form-actions">
          <n-button type="primary" secondary :loading="running" @click="createCalibration">
            <template #icon><Play :size="16" /></template>
            生成 Calibration Report
          </n-button>
        </div>
        <div class="report-list">
          <button v-for="report in reports" :key="report.id" class="report-row" :class="{ selected: selectedReport?.id === report.id }" @click="selectReport(report)">
            <span><strong>{{ report.target_parameter }}</strong><small>#{{ report.id }} · {{ fmt(report.created_at) }}</small></span>
            <n-tag size="small" :type="statusType(report.recommendation)">{{ statusText(report.recommendation) }}</n-tag>
          </button>
        </div>
      </n-card>
    </div>

    <n-card v-if="selectedReport" class="panel-card report-detail" :bordered="false">
      <template #header>
        <div class="card-heading"><FlaskConical :size="17" /><span>Calibration Report #{{ selectedReport.id }}</span><n-tag size="small" :type="statusType(selectedReport.recommendation)">{{ statusText(selectedReport.recommendation) }}</n-tag></div>
      </template>
      <div class="report-columns">
        <div><span>Current</span><strong>{{ JSON.stringify(selectedReport.current_value) }}</strong></div>
        <div><span>Challenger</span><strong>{{ JSON.stringify(selectedReport.challenger_value) }}</strong></div>
        <div><span>Train N</span><strong>{{ selectedReport.sample_counts?.train_case_count ?? 0 }}</strong></div>
        <div><span>Validation N</span><strong>{{ selectedReport.sample_counts?.validation_case_count ?? 0 }}</strong></div>
        <div><span>Test N</span><strong>{{ selectedReport.sample_counts?.test_case_count ?? 0 }}</strong></div>
        <div><span>Robustness</span><strong>{{ selectedReport.robustness?.status || '—' }}</strong></div>
      </div>
      <n-alert v-if="selectedReport.recommendation === 'INSUFFICIENT_EVIDENCE'" class="research-alert" type="warning" :show-icon="true">
        样本不足或数据质量不足，不能据此调整生产参数。
      </n-alert>
      <div class="evidence-actions">
        <n-button quaternary @click="downloadJson(`calibration-${selectedReport.id}`, selectedReport)">
          <template #icon><Download :size="15" /></template>
          导出 JSON
        </n-button>
      </div>
    </n-card>
  </section>
</template>

<style scoped>
.research-page { display: grid; gap: 18px; }
.research-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; padding: 8px 2px 2px; }
.research-eyebrow { display: inline-flex; align-items: center; gap: 7px; color: var(--app-primary); font-size: 11px; font-weight: 800; letter-spacing: .12em; }
h1 { margin: 7px 0 4px; font-size: 34px; line-height: 1.12; letter-spacing: 0; }
.research-header p { margin: 0; color: var(--app-text-muted); }
.research-grid { display: grid; gap: 18px; }
.research-grid-top, .research-grid-bottom { grid-template-columns: minmax(0, 1fr) minmax(0, 1.12fr); }
.card-heading { display: flex; align-items: center; gap: 8px; font-weight: 800; }
.card-heading small { margin-left: auto; color: var(--app-text-muted); font-size: 11px; font-weight: 600; }
.availability-list, .run-table, .metric-list, .report-list { display: grid; gap: 1px; }
.availability-row, .run-row, .report-row, .metric-row { min-width: 0; border-bottom: 1px solid var(--app-border-soft); }
.availability-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 48px; }
.availability-name { display: grid; gap: 2px; min-width: 0; }
.availability-name span, .run-main small, .run-samples small, .report-row small, .metric-row small { color: var(--app-text-muted); font-size: 11px; }
.research-alert { margin-top: 16px; }
.availability-foot { display: flex; align-items: center; gap: 8px; margin-top: 14px; min-width: 0; font-size: 11px; }
.availability-foot code { overflow: hidden; color: var(--app-text-muted); text-overflow: ellipsis; white-space: nowrap; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
label { display: grid; gap: 6px; color: var(--app-text-muted); font-size: 12px; font-weight: 700; }
label :deep(.n-input), label :deep(.n-select) { width: 100%; }
input { width: 100%; min-height: 34px; border: 1px solid var(--app-border); border-radius: 6px; padding: 0 10px; background: var(--app-surface-muted); color: var(--app-text); }
.horizon-line { display: grid; gap: 8px; margin-top: 16px; }
.field-label { color: var(--app-text-muted); font-size: 12px; font-weight: 700; }
.form-actions, .evidence-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
.run-row, .report-row { display: grid; align-items: center; gap: 12px; width: 100%; border: 0; border-bottom: 1px solid var(--app-border-soft); padding: 12px 8px; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.run-row { grid-template-columns: 48px minmax(0, 1fr) 80px auto; }
.run-row:hover, .report-row:hover, .run-row.selected, .report-row.selected { background: var(--app-row-hover); }
.run-id { color: var(--app-primary); font-family: ui-monospace, monospace; font-size: 12px; font-weight: 800; }
.run-main, .report-row span { display: grid; gap: 2px; min-width: 0; }
.run-main small, .report-row small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-samples { display: grid; gap: 2px; color: var(--app-text); font-size: 12px; text-align: right; }
.empty-state { padding: 24px 4px; color: var(--app-text-muted); text-align: center; }
.evidence-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.evidence-summary > div, .report-columns > div { display: grid; gap: 5px; min-width: 0; }
.evidence-summary span, .report-columns span { color: var(--app-text-muted); font-size: 11px; }
.evidence-summary strong, .report-columns strong { overflow-wrap: anywhere; font-size: 13px; }
.evidence-meta { display: flex; flex-wrap: wrap; gap: 7px 14px; margin: 16px 0; color: var(--app-text-muted); font-size: 11px; }
.limitations { display: grid; gap: 5px; margin-bottom: 14px; color: var(--app-warning); font-size: 11px; }
.metric-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 10px 0; }
.metric-row > div:first-child, .metric-value { display: grid; gap: 2px; min-width: 0; }
.metric-value { color: var(--app-primary); text-align: right; }
.metric-value small { color: var(--app-text-muted); }
.report-row { grid-template-columns: minmax(0, 1fr) auto; }
.report-detail { margin-bottom: 4px; }
.report-columns { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 980px) {
  .research-grid-top, .research-grid-bottom { grid-template-columns: 1fr; }
  .report-columns { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 620px) {
  .research-header { align-items: flex-start; flex-direction: column; }
  .form-grid { grid-template-columns: 1fr; }
  .run-row { grid-template-columns: 38px minmax(0, 1fr) auto; }
  .run-samples { display: none; }
  .evidence-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .report-columns { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
