<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
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
import type {
  SystemBackup,
  SystemDiagnostics,
  SystemHealth,
  SystemReadiness,
  SystemRecoveryReport,
  SystemRelease,
} from '../api/types'

const message = useMessage()
const loading = ref(false)
const release = ref<SystemRelease | null>(null)
const health = ref<SystemHealth | null>(null)
const readiness = ref<SystemReadiness | null>(null)
const recovery = ref<SystemRecoveryReport | null>(null)
const backups = ref<SystemBackup[]>([])

type TagType = 'success' | 'warning' | 'error' | 'info' | 'default'
function statusType(value?: string | null): TagType {
  const status = String(value || '').toUpperCase()
  if (['OK', 'READY', 'CURRENT', 'ACTIVE', 'PASS'].includes(status)) return 'success'
  if (['DEGRADED', 'READY_WITH_WARNINGS', 'BEHIND', 'UNKNOWN', 'WARNING'].includes(status)) return 'warning'
  if (['BLOCKED', 'AHEAD', 'BROKEN', 'FAILED'].includes(status)) return 'error'
  return 'info'
}

function fmt(value?: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
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

async function load() {
  loading.value = true
  try {
    const [releaseResult, healthResult, readinessResult, recoveryResult, backupResult] = await Promise.all([
      api.getSystemRelease(),
      api.getSystemHealth(),
      api.getSystemReadiness(),
      api.getSystemRecovery(),
      api.listSystemBackups(),
    ])
    release.value = releaseResult
    health.value = healthResult
    readiness.value = readinessResult
    recovery.value = recoveryResult
    backups.value = backupResult.backups
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

async function createBackup() {
  if (!window.confirm('创建一次 verified SQLite backup？')) return
  try {
    const result = await api.createSystemBackup('MANUAL')
    message.success(`备份 ${result.backup_id} 已完成并校验`)
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function verifyBackup(backup: SystemBackup) {
  try {
    const result = await api.verifySystemBackup(backup.backup_id)
    message.success(result.verified ? '备份校验通过' : '备份校验失败')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function runRestoreDrill(backup: SystemBackup) {
  if (!window.confirm(`对备份 ${backup.backup_id} 执行 restore drill？不会修改生产 DB。`)) return
  try {
    const result = await api.restoreDrill(backup.backup_id)
    message.success(`Restore drill：${String(result.status || 'DONE')}`)
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function generateDiagnostics() {
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

onMounted(() => void load())
</script>

<template>
  <div class="system-page">
    <div class="page-head">
      <div>
        <h1>系统运维</h1>
        <span class="muted">Release · Readiness · Backup · Diagnostics</span>
      </div>
      <div class="head-actions">
        <n-tag v-if="readiness" :type="statusType(readiness.status)" size="large">{{ readiness.status }}</n-tag>
        <n-button :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
      </div>
    </div>

    <n-spin :show="loading">
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
              <span>{{ key }}</span>
              <n-tag size="small" :type="statusType(check.status)">{{ check.status }}</n-tag>
            </div>
          </div>
        </section>
      </div>

      <div class="system-grid">
        <section class="panel-card system-section">
          <div class="section-title">
            <Archive :size="18" /><strong>Backup</strong>
            <n-button size="small" type="primary" @click="createBackup">Create Backup</n-button>
          </div>
          <n-data-table v-if="backups.length" size="small" :columns="backupColumns" :data="backups" :max-height="280" />
          <n-empty v-else description="还没有 verified backup" />
        </section>

        <section class="panel-card system-section">
          <div class="section-title"><Database :size="18" /><strong>Database / Runtime</strong></div>
          <div v-if="health" class="kv-list">
            <div><span>DB</span><span>{{ health.components.database?.status }} · {{ health.components.database?.quick_check || '—' }}</span></div>
            <div><span>WAL</span><span>{{ humanSize(health.components.database?.wal_size) }}</span></div>
            <div><span>Disk Free</span><span>{{ ((health.components.storage?.free_ratio || 0) * 100).toFixed(1) }}%</span></div>
            <div><span>Scheduler</span><span>{{ health.components.scheduler?.status }}</span></div>
            <div><span>Monitor</span><span>{{ health.components.realtime_monitor?.status }}</span></div>
            <div><span>Recovery</span><span>{{ health.components.worker_recovery?.status }} · {{ recovery ? Object.values(recovery.counts).reduce((a, b) => a + b, 0) : '—' }} stale</span></div>
            <div><span>Backup</span><span>{{ health.components.backup?.status }} · {{ health.components.backup?.backup_count || 0 }}</span></div>
          </div>
        </section>
      </div>

      <div class="panel-card system-section">
        <div class="section-title">
          <FileArchive :size="18" /><strong>Diagnostics</strong>
          <n-button size="small" @click="generateDiagnostics"><template #icon><Download :size="15" /></template>Generate Bundle</n-button>
        </div>
        <div class="muted small">
          <ShieldCheck :size="14" /> 诊断包已做 secret redaction，不包含 DB、backup 或 token。
        </div>
      </div>
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
.section-title { display: flex; align-items: center; gap: 9px; margin-bottom: 14px; }
.section-title strong { font-size: 15px; }
.section-title .n-button { margin-left: auto; }
.kv-list { display: grid; gap: 8px; }
.kv-list > div { display: grid; grid-template-columns: 120px 1fr; gap: 8px; align-items: start; }
.kv-list span:first-child { color: var(--app-text-muted); }
.kv-list code, .kv-list span:last-child { overflow-wrap: anywhere; }
.check-list { display: grid; gap: 7px; }
.check-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--app-border-soft); padding-bottom: 7px; }
.check-row span { color: var(--app-text-muted); }
.small { display: inline-flex; align-items: center; gap: 6px; color: var(--app-text-muted); }
.link-action { border: 0; background: transparent; color: var(--app-primary); cursor: pointer; padding: 2px 4px; }
.link-action:hover { text-decoration: underline; }
@media (max-width: 900px) {
  .system-grid { grid-template-columns: 1fr; }
  .page-head { align-items: flex-start; flex-direction: column; }
}
</style>
