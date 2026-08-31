<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  Activity,
  Archive,
  Database,
  Download,
  FileArchive,
  RefreshCw,
  Server,
  ShieldCheck,
} from 'lucide-vue-next'
import { useMessage, type DataTableColumns } from 'naive-ui'

import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import LoadingState from '../components/LoadingState.vue'
import { fmtDateTime, formatPercent, unavailableText } from '../utils/ui'
import type {
  HistoryCoverage,
  HistoryCoverageItem,
  HistorySyncRun,
  SystemBackup,
  SystemDiagnostics,
  SystemHealth,
  LiveValidationReadiness,
  SystemReadiness,
  SystemRecoveryReport,
  SystemRelease,
} from '../api/types'

const message = useMessage()
const loading = ref(false)
const release = ref<SystemRelease | null>(null)
const health = ref<SystemHealth | null>(null)
const readiness = ref<SystemReadiness | null>(null)
const liveReadiness = ref<LiveValidationReadiness | null>(null)
const recovery = ref<SystemRecoveryReport | null>(null)
const backups = ref<SystemBackup[]>([])
const historyCoverage = ref<HistoryCoverage | null>(null)
const syncRuns = ref<HistorySyncRun[]>([])
const syncDataType = ref('valuation')
const syncStartDate = ref('')
const syncEndDate = ref('')
const syncProvider = ref('AUTO')
const syncLoading = ref(false)
const loadError = ref<unknown>(null)
const backupLoading = ref<string | null>(null)
const diagnosticsLoading = ref(false)

const historyStatus = computed(() => {
  if (!historyCoverage.value?.items.length) return 'DATA_GAP'
  const statuses = historyCoverage.value.items.map((item) => item.status)
  if (statuses.some((status) => status === 'LEAKAGE_BLOCKED')) return 'BLOCKED'
  if (statuses.some((status) => status === 'DATA_GAP')) return 'PARTIAL'
  if (statuses.some((status) => status === 'PARTIAL')) return 'PARTIAL'
  return 'FULL'
})

type TagType = 'success' | 'warning' | 'error' | 'info' | 'default'
function statusType(value?: string | null): TagType {
  const status = String(value || '').toUpperCase()
  if (['OK', 'READY', 'CURRENT', 'ACTIVE', 'PASS'].includes(status)) return 'success'
  if (['DEGRADED', 'READY_WITH_WARNINGS', 'BEHIND', 'UNKNOWN', 'WARNING'].includes(status)) return 'warning'
  if (['BLOCKED', 'NOT_READY', 'AHEAD', 'BROKEN', 'FAILED'].includes(status)) return 'error'
  return 'info'
}

function fmt(value?: string | null): string {
  return fmtDateTime(value)
}

function shortHash(value?: string | null): string {
  return value && value !== 'UNKNOWN' ? value.slice(0, 12) : (value || '—')
}

function humanSize(bytes?: number | null): string {
  if (!bytes && bytes !== 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

const liveCheckLabels: Record<string, string> = {
  database: '数据库',
  schema: 'Schema',
  disk: '磁盘空间',
  backup: '备份策略',
  scheduler: '调度器权威',
  worker_recovery: 'Worker 恢复',
  governance: '参数治理',
  trading_calendar: '交易日历',
  market_provider: '行情 Provider',
  quote_pipeline: 'Quote Pipeline',
  market_refresh: '行情刷新',
  portfolio_snapshot: '组合快照',
  analysis_smoke: '分析 Smoke',
  candidate_smoke: '候选 Smoke',
  shadow_subsystem: 'Shadow 子系统',
  future_quote_observation: '未来行情观察',
  real_broker_write_path: '真实券商写入路径',
}
const liveCheckRows = computed(() => Object.entries(liveReadiness.value?.checks || {}).map(([key, check]) => ({
  key,
  label: liveCheckLabels[key] || key,
  check,
})))
function checkReason(check?: { reason?: string | null; status?: string | null }) {
  return check?.reason || (String(check?.status || '').toUpperCase() === 'OK' ? '检查通过' : unavailableText)
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [releaseResult, healthResult, readinessResult, liveReadinessResult, recoveryResult, backupResult, historyResult, syncResult] = await Promise.all([
      api.getSystemRelease(),
      api.getSystemHealth(),
      api.getSystemReadiness(),
      api.getLiveValidationReadiness(),
      api.getSystemRecovery(),
      api.listSystemBackups(),
      api.getHistoryCoverage(),
      api.listHistorySyncRuns(),
    ])
    release.value = releaseResult
    health.value = healthResult
    readiness.value = readinessResult
    liveReadiness.value = liveReadinessResult
    recovery.value = recoveryResult
    backups.value = backupResult.backups
    historyCoverage.value = historyResult
    syncRuns.value = syncResult.runs.slice(0, 10)
  } catch (error) {
    loadError.value = error
  } finally {
    loading.value = false
  }
}

async function runHistorySync() {
  syncLoading.value = true
  try {
    await api.runHistorySync({
      data_type: syncDataType.value,
      start_date: syncStartDate.value || undefined,
      end_date: syncEndDate.value || undefined,
      provider: syncProvider.value,
      market: 'CN',
    })
    message.success('历史 sync 已执行')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    syncLoading.value = false
  }
}

async function createBackup() {
  if (!window.confirm('创建一次 verified SQLite backup？')) return
  if (backupLoading.value) return
  backupLoading.value = 'create'
  try {
    const result = await api.createSystemBackup('MANUAL')
    message.success(`备份 ${result.backup_id} 已完成并校验`)
    await load()
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    backupLoading.value = null
  }
}

async function verifyBackup(backup: SystemBackup) {
  if (backupLoading.value) return
  backupLoading.value = 'verify-' + backup.backup_id
  try {
    const result = await api.verifySystemBackup(backup.backup_id)
    message.success(result.verified ? '备份校验通过' : '备份校验失败')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    backupLoading.value = null
  }
}

async function runRestoreDrill(backup: SystemBackup) {
  if (!window.confirm(`对备份 ${backup.backup_id} 执行 restore drill？不会修改生产 DB。`)) return
  if (backupLoading.value) return
  backupLoading.value = 'drill-' + backup.backup_id
  try {
    const result = await api.restoreDrill(backup.backup_id)
    message.success(`Restore drill：${String(result.status || 'DONE')}`)
    await load()
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    backupLoading.value = null
  }
}

async function generateDiagnostics() {
  if (diagnosticsLoading.value) return
  diagnosticsLoading.value = true
  try {
    const diagnostics: SystemDiagnostics = await api.createDiagnostics()
    const blob = await api.downloadDiagnostics(diagnostics.bundle_id)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = diagnostics.filename
    anchor.click()
    URL.revokeObjectURL(url)
    message.success(`诊断包 ${diagnostics.bundle_id} 已生成`)
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    diagnosticsLoading.value = false
  }
}

const backupColumns: DataTableColumns<SystemBackup> = [
  { title: 'ID', key: 'backup_id', ellipsis: { tooltip: true } },
  { title: '类型', key: 'type', width: 120 },
  { title: '完成时间', key: 'completed_at', width: 190, render: (row) => fmt(row.completed_at) },
  { title: '大小', key: 'backup_size', width: 90, render: (row) => humanSize(row.backup_size) },
  { title: 'SHA-256', key: 'sha256', width: 150, render: (row) => shortHash(row.sha256) },
  { title: '操作', key: 'actions', width: 170, render: (row) => {
      return h('div', { style: 'display:flex;gap:6px' }, [
        h('button', { class: 'link-action', onClick: () => void verifyBackup(row) }, 'Verify'),
        h('button', { class: 'link-action', onClick: () => void runRestoreDrill(row) }, 'Drill'),
      ])
  } },
]

const historyColumns: DataTableColumns<HistoryCoverageItem> = [
  { title: 'Data Type', key: 'data_type', width: 170 },
  { title: 'Status', key: 'status', width: 120, render: (row) => h('span', { class: 'status-text' }, row.status) },
  { title: 'Coverage', key: 'coverage', width: 100, render: (row) => row.coverage === null || row.coverage === undefined ? '—' : `${(row.coverage * 100).toFixed(1)}%` },
  { title: 'Earliest', key: 'earliest_supported_at', render: (row) => fmt(row.earliest_supported_at) },
  { title: 'Latest', key: 'latest_supported_at', render: (row) => fmt(row.latest_supported_at) },
  { title: 'Last Sync', key: 'last_sync', render: (row) => row.last_sync ? `${row.last_sync.status} · ${row.last_sync.inserted_count} in` : '—' },
]

const syncRunColumns: DataTableColumns<HistorySyncRun> = [
  { title: 'ID', key: 'id', width: 60 },
  { title: 'Type', key: 'data_type', width: 150 },
  { title: 'Status', key: 'status', width: 120 },
  { title: 'Progress', key: 'progress_percent', width: 90, render: (row) => `${row.progress_percent}%` },
  { title: 'In/Up/Skip', key: 'counts', width: 110, render: (row) => `${row.inserted_count}/${row.updated_count}/${row.skipped_count}` },
  { title: 'Created', key: 'created_at', render: (row) => fmt(row.created_at) },
]

onMounted(() => void load())
</script>

<template>
  <div class="system-page">
    <div class="page-head">
      <div>
        <h1>系统运维</h1>
        <span class="muted">系统健康、数据新鲜度与真实验证前置检查</span>
      </div>
      <div class="head-actions">
        <n-tag v-if="liveReadiness" :type="statusType(liveReadiness.status)" size="large">{{ liveReadiness.status }}</n-tag>
        <n-button :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
      </div>
    </div>

    <ErrorState v-if="loadError" :error="loadError" @retry="load" />
    <n-spin :show="loading">
      <section class="panel-card live-readiness-card">
        <div class="section-title">
          <ShieldCheck :size="18" />
          <div><strong>Live Validation Readiness</strong><p>是否可以开始真实验证的只读检查，不会启动交易或修改系统状态。</p></div>
          <n-tag v-if="liveReadiness" size="large" :type="statusType(liveReadiness.status)">{{ liveReadiness.status }}</n-tag>
        </div>
        <LoadingState v-if="!liveReadiness && loading" message="正在评估真实验证就绪度" />
        <template v-else-if="liveReadiness">
          <div class="live-readiness-banner" :class="liveReadiness.ready ? 'ready' : 'blocked'">
            <strong>{{ liveReadiness.ready ? '可以进入真实验证前置阶段' : '暂不能进入真实验证' }}</strong>
            <span>评估时间：{{ fmt(liveReadiness.evaluated_at) }}</span>
          </div>
          <div v-if="liveReadiness.blockers.length" class="reason-group blocker-group">
            <strong>Blockers</strong>
            <span v-for="item in liveReadiness.blockers" :key="item.key">{{ liveCheckLabels[item.key] || item.key }}：{{ item.reason }}</span>
          </div>
          <div v-if="liveReadiness.warnings.length" class="reason-group warning-group">
            <strong>Warnings</strong>
            <span v-for="item in liveReadiness.warnings" :key="item.key">{{ liveCheckLabels[item.key] || item.key }}：{{ item.reason }}</span>
          </div>
          <div class="live-check-grid">
            <div v-for="item in liveCheckRows" :key="item.key" class="live-check-row">
              <div><strong>{{ item.label }}</strong><small>{{ checkReason(item.check) }}</small></div>
              <n-tag size="small" :type="statusType(item.check.status)">{{ item.check.status }}</n-tag>
            </div>
          </div>
        </template>
        <EmptyState v-else description="暂无 Live Validation Readiness 结果">
          <template #action><n-button secondary size="small" @click="load">重新检查</n-button></template>
        </EmptyState>
      </section>

      <div class="system-grid">
        <section class="panel-card system-section">
          <div class="section-title"><Server :size="18" /><strong>Release</strong></div>
          <div v-if="release" class="kv-list">
            <div><span>App Version</span><code>{{ release.app_version }}</code></div>
            <div><span>Git SHA</span><code>{{ shortHash(release.git_sha) }}</code></div>
            <div><span>Schema</span><span>{{ release.schema_state }} <template v-if="release.alembic_db_revision">· {{ release.alembic_db_revision }}</template></span></div>
            <div><span>Code Head</span><code>{{ release.alembic_code_head_revision }}</code></div>
            <div><span>Runtime</span><span>{{ release.runtime_contract_version }} / {{ release.decision_contract_version }}</span></div>
            <div><span>Parameter Set</span><span>{{ release.active_parameter_set_version || '—' }} · {{ shortHash(release.active_parameter_set_hash) }}</span></div>
            <div><span>Uptime</span><span>{{ Math.floor(release.uptime_seconds / 60) }} min</span></div>
          </div>
        </section>

        <section class="panel-card system-section">
          <div class="section-title"><Activity :size="18" /><strong>Readiness</strong></div>
          <div v-if="readiness" class="check-list">
            <div v-for="(check, key) in readiness.checks" :key="key" class="check-row">
              <div><span>{{ key }}</span><small>{{ check.reason || '检查通过' }}</small></div>
              <n-tag size="small" :type="statusType(check.status)">{{ check.status }}</n-tag>
            </div>
          </div>
          <EmptyState v-else description="暂无基础 readiness 结果" />
        </section>
      </div>

      <div class="system-grid">
        <section class="panel-card system-section">
          <div class="section-title">
            <Archive :size="18" /><strong>Backup</strong>
            <n-button size="small" type="primary" :loading="backupLoading === 'create'" :disabled="Boolean(backupLoading)" @click="createBackup">Create Backup</n-button>
          </div>
          <n-data-table v-if="backups.length" size="small" :columns="backupColumns" :data="backups" :max-height="280" />
          <EmptyState v-else description="还没有 verified backup">
            <template #action><n-button secondary size="small" @click="createBackup">创建第一份备份</n-button></template>
          </EmptyState>
        </section>

        <section class="panel-card system-section">
          <div class="section-title"><Database :size="18" /><strong>Database / Runtime</strong></div>
          <div v-if="health" class="kv-list">
            <div><span>DB</span><span>{{ health.components.database?.status }} · {{ health.components.database?.quick_check || '—' }}</span></div>
            <div><span>WAL</span><span>{{ humanSize(health.components.database?.wal_size) }}</span></div>
            <div><span>Disk Free</span><span>{{ formatPercent(health.components.storage?.free_ratio) }}</span></div>
            <div><span>Scheduler</span><span>{{ health.components.scheduler?.status }}</span></div>
            <div><span>Monitor</span><span>{{ health.components.realtime_monitor?.status }}</span></div>
            <div><span>Recovery</span><span>{{ health.components.worker_recovery?.status }} · {{ recovery ? Object.values(recovery.counts).reduce((a, b) => a + b, 0) : '—' }} stale</span></div>
            <div><span>Backup</span><span>{{ health.components.backup?.status }} · {{ health.components.backup?.backup_count ?? unavailableText }}</span></div>
            <div><span>Shadow</span><span>{{ health.components.shadow?.status }} · {{ health.components.shadow?.pending_intents ?? unavailableText }} pending / {{ health.components.shadow?.blocked_intents ?? unavailableText }} blocked</span></div>
          </div>
        </section>
      </div>

      <div class="panel-card system-section">
        <div class="section-title">
          <FileArchive :size="18" /><strong>Diagnostics</strong>
          <n-button size="small" :loading="diagnosticsLoading" :disabled="diagnosticsLoading" @click="generateDiagnostics"><template #icon><Download :size="15" /></template>Generate Bundle</n-button>
        </div>
        <div class="muted small">
          <ShieldCheck :size="14" /> 诊断包已做 secret redaction，不包含 DB、backup 或 token。
        </div>
      </div>

      <section class="panel-card system-section">
        <div class="section-title">
          <Database :size="18" /><strong>Historical Data</strong>
          <n-tag v-if="historyCoverage" size="small" :type="statusType(historyStatus)">{{ historyStatus }}</n-tag>
        </div>
        <div class="sync-bar">
          <select v-model="syncDataType" class="sync-select" aria-label="Data Type">
            <option v-for="item in ['security_lifecycle', 'trading_status', 'st_classification', 'valuation', 'fundamentals', 'etf_metadata', 'price_basis']" :key="item" :value="item">{{ item }}</option>
          </select>
          <input v-model="syncStartDate" type="date" aria-label="Start Date" />
          <input v-model="syncEndDate" type="date" aria-label="End Date" />
          <input v-model="syncProvider" class="sync-provider" placeholder="Provider" aria-label="Provider" />
          <n-button size="small" type="primary" :loading="syncLoading" @click="runHistorySync">Run Sync</n-button>
          <n-button size="small" @click="load">刷新 Runs</n-button>
        </div>
        <n-data-table v-if="syncRuns.length" size="small" :columns="syncRunColumns" :data="syncRuns" :max-height="220" />
        <n-empty v-else description="还没有历史 sync run" />
        <n-data-table
          v-if="historyCoverage && historyCoverage.items.length"
          size="small"
          :columns="historyColumns"
          :data="historyCoverage.items"
          :max-height="300"
        />
        <n-empty v-else description="Historical PIT 数据尚未导入" />
      </section>
    </n-spin>
  </div>
</template>

<style scoped>
.system-page { display: grid; gap: 18px; }
.page-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.page-head h1 { margin: 0 0 4px; font-size: 24px; }
.head-actions { display: flex; align-items: center; gap: 10px; }
.system-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 18px; }
.system-section { padding: 18px; }
.live-readiness-card { padding: 18px; }
.live-readiness-card .section-title { align-items: flex-start; }
.live-readiness-card .section-title > div { min-width: 0; }
.live-readiness-card .section-title p { margin: 4px 0 0; color: var(--app-text-muted); font-size: 11px; font-weight: 400; }
.live-readiness-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-left: 4px solid var(--app-success); background: color-mix(in srgb, var(--app-success) 8%, transparent); padding: 11px 13px; }
.live-readiness-banner.blocked { border-left-color: var(--app-danger); background: color-mix(in srgb, var(--app-danger) 8%, transparent); }
.live-readiness-banner strong { font-size: 15px; }
.live-readiness-banner span { color: var(--app-text-muted); font-size: 11px; }
.reason-group { display: grid; gap: 4px; margin-top: 12px; border-left: 3px solid var(--app-danger); padding-left: 10px; color: var(--app-danger); font-size: 12px; }
.warning-group { border-left-color: var(--app-warning); color: var(--app-warning); }
.reason-group strong { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
.live-check-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
.live-check-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; border: 1px solid var(--app-border-soft); padding: 9px 10px; }
.live-check-row > div { display: grid; min-width: 0; gap: 3px; }
.live-check-row strong { overflow-wrap: anywhere; font-size: 12px; }
.live-check-row small { overflow-wrap: anywhere; color: var(--app-text-muted); font-size: 10px; line-height: 1.4; }
.section-title { display: flex; align-items: center; gap: 9px; margin-bottom: 14px; }
.section-title strong { font-size: 15px; }
.section-title .n-button { margin-left: auto; }
.sync-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.sync-bar input, .sync-bar .sync-select, .sync-bar .sync-provider { height: 30px; border: 1px solid var(--app-border-soft); border-radius: 6px; padding: 0 8px; font-size: 13px; background: var(--app-surface); color: var(--app-text); }
.sync-select { max-width: 220px; }
.sync-provider { width: 110px; }
.kv-list { display: grid; gap: 8px; }
.kv-list > div { display: grid; grid-template-columns: 120px 1fr; gap: 8px; align-items: start; }
.kv-list span:first-child { color: var(--app-text-muted); }
.kv-list code, .kv-list span:last-child { overflow-wrap: anywhere; }
.check-list { display: grid; gap: 7px; }
.check-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--app-border-soft); padding-bottom: 7px; }
.check-row > div { display: grid; gap: 2px; min-width: 0; }
.check-row span { color: var(--app-text-muted); }
.check-row small { overflow-wrap: anywhere; color: var(--app-text-muted); font-size: 10px; }
.small { display: inline-flex; align-items: center; gap: 6px; color: var(--app-text-muted); }
.link-action { border: 0; background: transparent; color: var(--app-primary); cursor: pointer; padding: 2px 4px; }
.link-action:hover { text-decoration: underline; }
@media (max-width: 900px) {
  .system-grid { grid-template-columns: 1fr; }
  .live-check-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .page-head { align-items: flex-start; flex-direction: column; }
}
@media (max-width: 560px) {
  .live-readiness-banner { align-items: flex-start; flex-direction: column; }
  .live-check-grid { grid-template-columns: 1fr; }
}
</style>
