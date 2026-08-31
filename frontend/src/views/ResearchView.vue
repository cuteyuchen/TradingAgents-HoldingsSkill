<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
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
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import LoadingState from '../components/LoadingState.vue'
import { usePortfolioContext } from '../composables/portfolio'
import { fmtDateTime, unavailableText } from '../utils/ui'
import type {
  BacktestRun,
  CalibrationReport,
  RecomputeCapabilityManifest,
  ReplayAvailabilityItem,
  ReplayAvailabilityManifest,
  ReplayMode,
  ResearchScope,
} from '../api/types'

const message = useMessage()
const loading = ref(false)
const running = ref(false)
const cancelLoading = ref(false)
const loadError = ref<unknown>(null)
const availability = ref<ReplayAvailabilityManifest | null>(null)
const runs = ref<BacktestRun[]>([])
const reports = ref<CalibrationReport[]>([])
const selectedRun = ref<BacktestRun | null>(null)
const selectedReport = ref<CalibrationReport | null>(null)
const recomputePreview = ref<RecomputeCapabilityManifest | null>(null)
const previewLoading = ref(false)
let runPollTimer: number | null = null
let runPollBusy = false
let mounted = false

const {
  portfolios,
  selectedPortfolioId,
  selectedPortfolio,
  loadPortfolios,
} = usePortfolioContext()

function isoDate(value: Date): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value)
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return [values.year, values.month, values.day].join('-')
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
  backtest_run_id: null as number | null,
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
const canPreviewRecompute = computed(() =>
  runForm.replay_mode === 'DETERMINISTIC_RECOMPUTE' &&
  ['MARKET', 'CANDIDATE', 'PORTFOLIO_DECISION'].includes(runForm.scope),
)
const selectedRecompute = computed(() => {
  if (selectedRun.value?.replay_mode !== 'DETERMINISTIC_RECOMPUTE') return null
  return (selectedRun.value?.recompute_summary as Record<string, any> | null | undefined) || null
})
const calibrationRunOptions = computed(() => runs.value
  .filter((run) => run.status === 'COMPLETED')
  .map((run) => ({ label: `#${run.id} ${run.scope} ${run.start_date} → ${run.end_date}`, value: run.id })))

function statusType(status?: string | null): 'success' | 'warning' | 'error' | 'info' | 'default' {
  const value = String(status || '').toUpperCase()
  if (['VALID', 'FULL', 'COMPLETED', 'PASS'].includes(value)) return 'success'
  if (['PARTIAL', 'DEGRADED', 'DIAGNOSTIC_ONLY', 'DATA_GAP', 'INSUFFICIENT', 'INSUFFICIENT_DATA', 'INSUFFICIENT_EVIDENCE'].includes(value)) return 'warning'
  if (['FAILED', 'BLOCKED', 'LEAKAGE_BLOCKED', 'INVALIDATED', 'REJECT_CHANGE'].includes(value)) return 'error'
  return 'info'
}

function statusText(status?: string | null): string {
  const labels: Record<string, string> = {
    FULL: 'FULL', FULL_PIT_EQUIVALENT: '完整 PIT 重算', PARTIAL: 'PARTIAL', PARTIAL_PIT_RECOMPUTE: '部分历史输入缺失，仅供研究', DIAGNOSTIC_ONLY: '仅诊断', DATA_GAP: 'DATA_GAP',
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
  return fmtDateTime(value)
}

function portfolioFor(scope: ResearchScope, value: number | null): number | null {
  return scope === 'MARKET' ? null : value
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    await loadPortfolios()
    if (!runForm.portfolio_id && selectedPortfolioId.value) runForm.portfolio_id = selectedPortfolioId.value
    const values = await Promise.all([
      api.getReplayAvailability({ start_date: runForm.start_date, end_date: runForm.end_date, portfolio_id: runForm.portfolio_id || undefined }),
      api.listBacktests(selectedPortfolioId.value || undefined),
      api.listCalibrations(selectedPortfolioId.value || undefined),
    ])
    availability.value = values[0]
    runs.value = values[1]
    reports.value = values[2]
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
    if (selectedRun.value && ['QUEUED', 'RUNNING'].includes(String(selectedRun.value.status).toUpperCase())) startRunPolling()
    else stopRunPolling()
    await refreshRecomputePreview()
  } catch (error) {
    loadError.value = error
  } finally {
    loading.value = false
  }
}

function stopRunPolling() {
  if (runPollTimer !== null) {
    window.clearInterval(runPollTimer)
    runPollTimer = null
  }
  runPollBusy = false
}

function startRunPolling() {
  stopRunPolling()
  if (!selectedRun.value || !['QUEUED', 'RUNNING'].includes(String(selectedRun.value.status).toUpperCase())) return
  runPollTimer = window.setInterval(async () => {
    if (!selectedRun.value || runPollBusy) return
    runPollBusy = true
    try {
      const latest = await api.getBacktest(selectedRun.value.id)
      selectedRun.value = latest
      const rowIndex = runs.value.findIndex((item) => item.id === latest.id)
      if (rowIndex >= 0) runs.value[rowIndex] = latest
      if (!['QUEUED', 'RUNNING'].includes(String(latest.status).toUpperCase())) stopRunPolling()
    } catch (error) {
      loadError.value = error
    } finally {
      runPollBusy = false
    }
  }, 1500)
}

let previewSequence = 0
async function refreshRecomputePreview() {
  const seq = ++previewSequence
  if (!canPreviewRecompute.value) {
    recomputePreview.value = null
    previewLoading.value = false
    return
  }
  previewLoading.value = true
  try {
    const value = await api.getRecomputeCapability({
      scope: runForm.scope,
      start_date: runForm.start_date,
      end_date: runForm.end_date,
      portfolio_id: runForm.portfolio_id || undefined,
    })
    if (seq === previewSequence) recomputePreview.value = value
  } catch {
    if (seq === previewSequence) recomputePreview.value = null
  } finally {
    if (seq === previewSequence) previewLoading.value = false
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
  if (running.value) return
  if (runForm.start_date > runForm.end_date) {
    message.warning('开始日期不能晚于结束日期')
    return
  }
  if (!runForm.horizons.length) {
    message.warning('至少选择一个 Forward Horizon')
    return
  }
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
    startRunPolling()
    await load()
    message.success('研究 Run 已提交，页面会持续同步进度')
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    running.value = false
  }
}

async function cancelRun() {
  if (!selectedRun.value || cancelLoading.value) return
  cancelLoading.value = true
  try {
    selectedRun.value = await api.cancelBacktest(selectedRun.value.id)
    stopRunPolling()
    await load()
    message.info('研究 Run 已取消')
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    cancelLoading.value = false
  }
}

function selectRun(run: BacktestRun) {
  selectedRun.value = run
  void api.getBacktest(run.id).then((value) => {
    selectedRun.value = value
    if (['QUEUED', 'RUNNING'].includes(String(value.status).toUpperCase())) startRunPolling()
    else stopRunPolling()
  }).catch((error) => { loadError.value = error })
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
    if (!calibrationForm.backtest_run_id) {
      message.warning('请先选择已完成的 Backtest Run')
      return
    }
    const report = await api.createCalibration({
      backtest_run_id: calibrationForm.backtest_run_id,
      target_parameter: calibrationForm.target_parameter,
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

onMounted(() => { mounted = true; void load() })
onUnmounted(stopRunPolling)

watch(
  () => [runForm.scope, runForm.replay_mode, runForm.start_date, runForm.end_date, runForm.portfolio_id],
  () => { void refreshRecomputePreview() },
)

watch(selectedPortfolioId, (value) => {
  if (!mounted) return
  runForm.portfolio_id = value
  void load()
})
</script>

<template>
  <section class="research-page">
    <header class="research-header">
      <div>
        <div class="research-eyebrow"><FlaskConical :size="15" /> OFFLINE RESEARCH</div>
        <h1>历史回放与参数校准</h1>
        <p>只读取已持久化事实，输出可复现的 Backtest Evidence 与人工评审报告。<span v-if="selectedPortfolio">当前组合：{{ selectedPortfolio.name }}</span></p>
      </div>
      <n-button secondary :loading="loading" @click="load">
        <template #icon><RefreshCw :size="16" /></template>
        刷新研究状态
      </n-button>
    </header>

    <ErrorState v-if="loadError" :error="loadError" @retry="load" />
    <LoadingState v-if="loading && !availability" message="正在读取研究数据" />
    <div class="research-grid research-grid-top">
      <n-card class="panel-card" :bordered="false">
        <template #header>
          <div class="card-heading"><Database :size="17" /><span>Data Availability</span></div>
        </template>
        <div class="availability-list">
          <div v-for="row in availabilityRows" :key="row.key" class="availability-row">
            <div class="availability-name">
              <strong>{{ row.label }}</strong>
              <span>{{ row.item ? (row.item.row_count ?? unavailableText) : unavailableText }} rows · {{ row.item ? pct(row.item.coverage) : unavailableText }}</span>
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
        <div v-if="canPreviewRecompute" class="recompute-preview">
          <div class="preview-heading">
            <strong>Recompute Capability Preview</strong>
            <n-spin v-if="previewLoading" size="small" />
            <n-tag v-else-if="recomputePreview" size="small" :type="statusType(recomputePreview.capability)">
              {{ statusText(recomputePreview.capability) }}
            </n-tag>
          </div>
          <template v-if="recomputePreview">
            <div class="preview-meta">
              <span>Parameter <code>{{ recomputePreview.parameter_version || '—' }}</code></span>
              <span>Config <code>{{ recomputePreview.config_hash ? recomputePreview.config_hash.slice(0, 12) : '—' }}</code></span>
              <span>Universe <code>{{ recomputePreview.universe_version }}</code></span>
              <span>Checkpoint <code>{{ recomputePreview.checkpoint }}</code></span>
            </div>
            <div class="preview-chips">
              <span v-for="key in recomputePreview.missing_inputs" :key="`missing-${key}`" class="chip chip-error">{{ key }}</span>
              <span v-for="key in recomputePreview.partial_inputs" :key="`partial-${key}`" class="chip chip-warning">{{ key }}</span>
              <span v-if="!recomputePreview.missing_inputs.length && !recomputePreview.partial_inputs.length" class="chip chip-ok">required inputs available</span>
            </div>
            <div v-if="recomputePreview.limitations.length" class="preview-limits">
              <span v-for="item in recomputePreview.limitations" :key="item">{{ item }}</span>
            </div>
            <div v-if="recomputePreview.capability !== 'FULL_PIT_EQUIVALENT'" class="preview-warning">
              {{ recomputePreview.capability }} 不是 FULL 等价回测；运行后以实际 capability 为准。
            </div>
          </template>
          <div v-else-if="!previewLoading" class="preview-warning">Capability 预览暂不可用。</div>
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
          <n-button type="primary" :loading="running" :disabled="loading" @click="createRun">
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
      <LoadingState v-if="loading && !runs.length" message="正在读取 Backtest Runs" />
      <EmptyState v-else-if="!runs.length" description="暂无研究 Run">
        <template #action><n-button secondary size="small" @click="createRun">按当前条件启动研究</n-button></template>
      </EmptyState>
      <div v-else class="run-table">
        <button v-for="run in runs" :key="run.id" class="run-row" :class="{ selected: selectedRun?.id === run.id }" @click="selectRun(run)">
          <span class="run-id">#{{ run.id }}</span>
          <span class="run-main"><strong>{{ run.scope }}</strong><small>{{ run.start_date }} → {{ run.end_date }} · {{ run.replay_mode }}</small></span>
          <span class="run-samples">N={{ run.sample_count }}<small>{{ run.unique_trade_dates }} dates</small><n-progress v-if="['QUEUED', 'RUNNING'].includes(run.status)" type="line" :percentage="run.progress_percent" :show-indicator="false" /></span>
          <n-tag size="small" :type="statusType(run.status)">{{ statusText(run.status) }}</n-tag>
        </button>
      </div>
    </n-card>

    <div class="research-grid research-grid-bottom">
      <n-card class="panel-card" :bordered="false">
        <template #header>
          <div class="card-heading"><ShieldAlert :size="17" /><span>Run Evidence</span></div>
        </template>
        <EmptyState v-if="!selectedRun" description="选择一个 Run 查看 Evidence" />
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
          <div v-if="selectedRecompute" class="recompute-result">
            <div class="preview-heading">
              <strong>Deterministic Recompute</strong>
              <n-tag size="small" :type="statusType(selectedRecompute.capability)">{{ statusText(selectedRecompute.capability) }}</n-tag>
            </div>
            <div class="preview-meta">
              <span>Dates <code>{{ selectedRecompute.date_count ?? '—' }}</code></span>
              <span>Queries <code>{{ selectedRecompute.query_count ?? '—' }}</code></span>
              <span>Hash <code>{{ selectedRecompute.deterministic_hash ? selectedRecompute.deterministic_hash.slice(0, 16) : '—' }}</code></span>
              <span>Cases <code>{{ selectedRecompute.candidate_case_count ?? '—' }}</code></span>
              <span>Candidate Action <code>{{ selectedRecompute.candidate_action_count ?? '—' }}</code></span>
              <span>No-action Rate <code>{{ selectedRecompute.candidate_no_action_rate === null || selectedRecompute.candidate_no_action_rate === undefined ? '—' : `${(selectedRecompute.candidate_no_action_rate * 100).toFixed(1)}%` }}</code></span>
              <span v-if="selectedRecompute.portfolio_action_count !== undefined || selectedRecompute.portfolio_no_action_count !== undefined">
                Portfolio <code>{{ selectedRecompute.portfolio_action_count }} ACTION / {{ selectedRecompute.portfolio_no_action_count }} NO_ACTION</code>
              </span>
            </div>
            <div class="preview-chips">
              <span v-for="key in selectedRecompute.missing_inputs || []" :key="`run-missing-${key}`" class="chip chip-error">{{ key }}</span>
              <span v-for="key in selectedRecompute.partial_inputs || []" :key="`run-partial-${key}`" class="chip chip-warning">{{ key }}</span>
            </div>
            <div v-if="selectedRecompute.limitations?.length" class="preview-limits">
              <span v-for="item in selectedRecompute.limitations" :key="item">{{ item }}</span>
            </div>
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
            <n-button v-if="canCancel" quaternary type="error" :loading="cancelLoading" :disabled="running" @click="cancelRun">
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
          <label>Backtest Run<n-select v-model:value="calibrationForm.backtest_run_id" :options="calibrationRunOptions" placeholder="选择已完成的 Run" /></label>
          <label>Safe Grid<n-input v-model:value="calibrationForm.parameter_grid" placeholder="4,5,6,7" /></label>
        </div>
        <n-alert class="research-alert" type="warning" :show-icon="true">
          Calibration 只基于已完成的 Backtest Run；Global Final Test 只用于最终报告，没有 Apply 按钮，参数变更必须人工批准。
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
        <div><span>Train N</span><strong>{{ selectedReport.sample_counts?.train_case_count ?? unavailableText }}</strong></div>
        <div><span>Validation N</span><strong>{{ selectedReport.sample_counts?.validation_case_count ?? unavailableText }}</strong></div>
        <div><span>Test N</span><strong>{{ selectedReport.sample_counts?.test_case_count ?? unavailableText }}</strong></div>
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
.recompute-preview, .recompute-result { display: grid; gap: 10px; margin-top: 16px; border-top: 1px solid var(--app-border-soft); padding-top: 14px; }
.preview-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.preview-meta { display: flex; flex-wrap: wrap; gap: 6px 14px; color: var(--app-text-muted); font-size: 11px; }
.preview-meta code, .preview-heading code { color: var(--app-text); overflow-wrap: anywhere; }
.preview-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border-radius: 5px; padding: 2px 7px; font-size: 11px; font-weight: 700; }
.chip-error { background: color-mix(in srgb, var(--app-error, #d03050) 12%, transparent); color: var(--app-error, #d03050); }
.chip-warning { background: color-mix(in srgb, var(--app-warning, #d08800) 12%, transparent); color: var(--app-warning, #d08800); }
.chip-ok { background: color-mix(in srgb, var(--app-success, #18a058) 12%, transparent); color: var(--app-success, #18a058); }
.preview-limits { display: grid; gap: 4px; color: var(--app-warning); font-size: 11px; }
.preview-warning { border-radius: 6px; background: color-mix(in srgb, var(--app-warning, #d08800) 10%, transparent); color: var(--app-warning, #d08800); padding: 8px 10px; font-size: 12px; font-weight: 700; }
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
