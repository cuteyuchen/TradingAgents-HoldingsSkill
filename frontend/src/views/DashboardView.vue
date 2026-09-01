<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bell,
  Camera,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import ErrorState from '../components/ErrorState.vue'
import { usePortfolioContext } from '../composables/portfolio'
import { fmtDateTime } from '../utils/ui'
import type {
  AnalysisMode,
  DailyDashboard,
  ModelProfile,
  OperatingNotification,
  Portfolio,
  Schedule,
  SystemReadiness,
} from '../api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const loading = ref(false)
const error = ref('')
const profiles = ref<ModelProfile[]>([])
const schedules = ref<Schedule[]>([])
const dashboard = ref<DailyDashboard | null>(null)
const systemReadiness = ref<SystemReadiness | null>(null)
const candidateTab = ref<'action' | 'ready' | 'watchlist'>('action')
const reconciling = ref(false)
const createOpen = ref(false)
const creating = ref(false)
const newPortfolioName = ref('我的持仓')
const analysisOpen = ref(false)
const analysisPortfolio = ref<Portfolio | null>(null)
const analysisMode = ref<AnalysisMode>('deep')
const analysisCheckpoint = ref('10:30')
const analysisNotify = ref(true)
const startingAnalysis = ref(false)
let refreshTimer: number | null = null

const {
  portfolios,
  selectedPortfolioId: selectedId,
  selectedPortfolio,
  loadPortfolios,
  setSelectedPortfolio,
} = usePortfolioContext()
const modelReady = computed(() => profiles.value.some((item) => ['analysis', 'deep_analysis'].includes(item.purpose) && item.is_default))
const schedulesEnabled = computed(() => schedules.value.some((item) => item.enabled))
const health = computed(() => dashboard.value?.data_health as any)
const healthStatus = computed(() => String(health.value?.overall || health.value?.status || 'UNKNOWN').toUpperCase())
const healthIssue = computed(() => health.value?.components?.find((item: any) => item.status !== 'OK'))
const candidateItems = computed(() => dashboard.value?.candidates?.[candidateTab.value] || [])
const staleCandidates = computed(() => dashboard.value?.candidates?.stale || [])
const latestDecision = computed(() => dashboard.value?.decisions?.latest || null)
const finalAction = computed(() => String(dashboard.value?.decisions?.final_action || 'NO_ACTION').toUpperCase())
const latestAnalysis = computed(() => dashboard.value?.analysis?.latest || null)
const todayAnalysisMissing = computed(() => Boolean(
  dashboard.value
  && !dashboard.value.analysis?.analysis_in_progress
  && !latestAnalysis.value
  && !latestDecision.value,
))
const currentCheckpoint = computed(() => dashboard.value?.timeline?.timeline?.find((item: any) => item.is_current))
const nextCheckpoint = computed(() => dashboard.value?.timeline?.timeline?.find((item: any) => item.status === 'PENDING'))
const unreadNotifications = computed(() => Number(dashboard.value?.notifications?.unread_count || 0))

function fmt(value?: string | null) {
  return fmtDateTime(value)
}

function numberText(value: unknown, digits = 2) {
  if (value === null || value === undefined || value === '') return '不可用'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : String(value)
}

function percentText(value: unknown) {
  if (value === null || value === undefined || value === '') return '不可用'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(1)}%` : String(value)
}

function statusType(status?: string | null) {
  const value = String(status || '').toUpperCase()
  if (['OK', 'FRESH', 'SUCCESS', 'VALID', 'COMPLETED', 'HEALTHY'].includes(value)) return 'success'
  if (['BLOCKED', 'ERROR', 'FAILED', 'MISSING', 'UNAVAILABLE'].includes(value)) return 'error'
  if (['INFO', 'REUSED', 'SKIPPED', 'DASHBOARD_ONLY'].includes(value)) return 'info'
  return 'warning'
}

function readinessType(status?: string | null) {
  const value = String(status || '').toUpperCase()
  if (value === 'READY') return 'success'
  if (value === 'BLOCKED') return 'error'
  return 'warning'
}

function actionType(action?: string | null) {
  const value = String(action || '').toUpperCase()
  if (['REDUCE', 'EXIT', 'SELL', 'BLOCKED'].includes(value)) return 'error'
  if (value === 'ACTION') return 'warning'
  return 'info'
}

function actionText(action?: string | null) {
  const value = String(action || 'NO_ACTION').toUpperCase()
  return ['NO_ACTION', 'WATCH_ONLY', 'ACTION', 'REDUCE', 'BLOCKED'].includes(value) ? value : value
}

function candidateStage(item: any) {
  return String(item.display_stage || item.stage || item.candidate_engine_stage || '—').toUpperCase()
}

function candidateReason(item: any) {
  const reasons = item.blocking_reasons || item.blocking_reasons_json || []
  return Array.isArray(reasons) && reasons.length ? reasons.join('、') : '—'
}

function notificationType(item: OperatingNotification) {
  return statusType(item.severity)
}

async function loadDashboard(silent = false) {
  if (!selectedId.value) {
    dashboard.value = null
    return
  }
  if (!silent) loading.value = true
  error.value = ''
  try {
    dashboard.value = await api.getDashboardToday(selectedId.value)
  } catch (err) {
    error.value = (err as Error).message
    if (!silent) message.error(error.value)
  } finally {
    if (!silent) loading.value = false
  }
}

async function loadSystemReadiness() {
  try {
    systemReadiness.value = await api.getSystemReadiness()
  } catch {
    systemReadiness.value = null
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [portfolioRows, profileRows, scheduleRows] = await Promise.all([
      loadPortfolios(),
      api.listProfiles(),
      api.listSchedules(),
    ])
    portfolios.value = portfolioRows
    profiles.value = profileRows
    schedules.value = scheduleRows
    const requestedId = Number(route.query.portfolio)
    const requested = portfolios.value.find((item) => item.id === requestedId)?.id
    if (requested && requested !== selectedId.value) setSelectedPortfolio(requested)
    await Promise.all([loadDashboard(true), loadSystemReadiness()])
  } catch (err) {
    error.value = (err as Error).message
    message.error(error.value)
  } finally {
    loading.value = false
  }
}

async function createPortfolio() {
  const name = newPortfolioName.value.trim()
  if (!name || creating.value) return
  creating.value = true
  try {
    const portfolio = await api.createPortfolio({ name, is_default: portfolios.value.length === 0 })
    portfolios.value.push(portfolio)
    setSelectedPortfolio(portfolio.id)
    createOpen.value = false
    message.success('组合已创建')
    await loadDashboard()
  } catch (err) {
    message.error((err as Error).message)
  } finally {
    creating.value = false
  }
}

function openManualAnalysis(portfolio: Portfolio) {
  if (!portfolio.latest_snapshot_id) {
    message.warning('该组合还没有已确认持仓，请先上传并确认持仓截图')
    void router.push({ name: 'upload', query: { portfolio: portfolio.id } })
    return
  }
  if (!modelReady.value) {
    message.warning('请先在系统设置中配置默认分析模型')
    void router.push({ name: 'settings' })
    return
  }
  analysisPortfolio.value = portfolio
  analysisOpen.value = true
}

async function startManualAnalysis() {
  const portfolio = analysisPortfolio.value
  if (!portfolio?.latest_snapshot_id || startingAnalysis.value) return
  startingAnalysis.value = true
  try {
    const job = await api.createAnalysisJob(
      portfolio.latest_snapshot_id,
      analysisMode.value,
      analysisCheckpoint.value || undefined,
      analysisNotify.value,
    )
    analysisOpen.value = false
    message.success('手动分析任务已创建')
    await router.push({ name: 'upload', query: { portfolio: portfolio.id, job: job.id, focus: 'analysis' } })
  } catch (err) {
    message.error((err as Error).message)
  } finally {
    startingAnalysis.value = false
  }
}

async function reconcile() {
  if (!selectedId.value || reconciling.value) return
  reconciling.value = true
  try {
    await api.reconcileToday(selectedId.value)
    message.success('今日运行状态已恢复')
    await loadDashboard()
  } catch (err) {
    message.error((err as Error).message)
  } finally {
    reconciling.value = false
  }
}

async function markRead(item: OperatingNotification) {
  try {
    await api.markOperatingNotificationRead(item.notification_id, item.portfolio_id)
    item.read = true
    item.read_at = new Date().toISOString()
  } catch (err) {
    message.error((err as Error).message)
  }
}

function startAutoRefresh() {
  if (refreshTimer !== null) window.clearInterval(refreshTimer)
  refreshTimer = window.setInterval(() => {
    if (selectedId.value) void loadDashboard(true)
  }, 20000)
}

onMounted(async () => {
  await load()
  startAutoRefresh()
})

watch(selectedId, (id, previous) => {
  if (id && id !== previous) void loadDashboard()
})

onUnmounted(() => {
  if (refreshTimer !== null) window.clearInterval(refreshTimer)
})
</script>

<template>
  <section class="page-stack">
    <header class="page-heading">
      <div>
        <p class="eyebrow">DAILY INVESTMENT WORKBENCH</p>
        <h1>今日操作台</h1>
        <p>市场、组合、候选、分析、执行和复盘统一对齐到同一数据时点。</p>
      </div>
      <div class="heading-actions">
        <n-select :value="selectedId" :options="portfolios.map((item) => ({ label: item.name, value: item.id }))" placeholder="选择组合" class="portfolio-select" @update:value="setSelectedPortfolio" />
        <n-button secondary :loading="loading" @click="loadDashboard()"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
        <n-button secondary :loading="reconciling" :disabled="!selectedId" @click="reconcile"><template #icon><RotateCcw :size="16" /></template>恢复今日状态</n-button>
        <n-button type="primary" :disabled="!selectedId" @click="router.push({ name: 'upload', query: { portfolio: selectedId } })"><template #icon><Camera :size="16" /></template>更新持仓</n-button>
        <n-button secondary @click="createOpen = true"><template #icon><Plus :size="16" /></template>新建组合</n-button>
      </div>
    </header>

    <ErrorState v-if="error" :error="error" @retry="load" />

    <n-empty v-if="!loading && !portfolios.length" description="暂无持仓组合，请先创建组合或上传持仓。">
      <template #extra><n-button type="primary" @click="createOpen = true"><template #icon><Plus :size="16" /></template>创建组合</n-button></template>
    </n-empty>

    <n-spin v-else :show="loading && !dashboard">
      <template v-if="dashboard">
        <div class="as-of-line">
          <span>组合：{{ selectedPortfolio?.name || '—' }}</span>
          <span>数据时点：{{ fmt(dashboard.as_of) }}</span>
          <span>工作流：{{ dashboard.workflow_state }}</span>
          <span>当前节点：{{ currentCheckpoint?.label || '—' }}</span>
          <span>下一节点：{{ nextCheckpoint?.time || '—' }}</span>
          <span v-if="schedulesEnabled" class="schedule-mark">自动时间表已启用</span>
          <n-tag v-if="unreadNotifications" type="warning" size="small" :bordered="false"><Bell :size="13" /> {{ unreadNotifications }} 条未读</n-tag>
          <n-tag
            v-if="systemReadiness"
            :type="readinessType(systemReadiness.status)"
            size="small"
            :bordered="false"
            class="readiness-badge"
            @click="router.push({ name: 'system' })"
          >系统 {{ systemReadiness.status }}</n-tag>
        </div>

        <div class="hero-grid">
          <section class="panel-card hero-card">
            <div class="section-title"><div><h2>Market State</h2><p>{{ dashboard.market?.market_mode || '—' }} · {{ dashboard.market?.market_score_source || '—' }}</p></div><n-tag :type="statusType(dashboard.market?.freshness)" :bordered="false">{{ dashboard.market?.freshness || 'MISSING' }}</n-tag></div>
            <div class="hero-value">{{ dashboard.market?.score == null ? '不可用' : numberText(dashboard.market.score, 1) }}</div>
            <div class="hero-label">{{ dashboard.market?.regime || 'Regime 不可用' }}</div>
            <div class="inline-meta"><span>Raw {{ numberText(dashboard.market?.raw_score, 1) }}</span><span>置信度 {{ dashboard.market?.confidence ?? '不可用' }}</span><span>15m {{ numberText(dashboard.market?.delta_15m, 1) }}</span></div>
            <div class="metric-strip"><span>Breadth {{ numberText(dashboard.market?.components?.breadth, 1) }}</span><span>Trend {{ numberText(dashboard.market?.components?.trend, 1) }}</span><span>Top5 {{ numberText(dashboard.market?.components?.top5_turnover_concentration, 3) }}</span></div>
            <small class="muted">捕获：{{ fmt(dashboard.market?.captured_at) }} · All-A Median {{ numberText(dashboard.market?.all_a_median?.index_value, 2) }}</small>
          </section>

          <section class="panel-card hero-card">
            <div class="section-title"><div><h2>Portfolio Risk</h2><p>Snapshot {{ fmt(dashboard.portfolio?.snapshot_time) }}</p></div><n-tag :type="statusType(dashboard.portfolio?.status)" :bordered="false">{{ dashboard.portfolio?.status || 'MISSING' }}</n-tag></div>
            <div class="hero-value">{{ percentText(dashboard.portfolio?.cash_ratio) }}</div>
            <div class="hero-label">现金比例</div>
            <div class="risk-grid"><span>资产 {{ numberText(dashboard.portfolio?.total_assets, 0) }}</span><span>储备 {{ percentText(dashboard.portfolio?.reserve_ratio) }}</span><span>暴露 {{ percentText(dashboard.portfolio?.gross_exposure) }}</span><span>HHI {{ numberText(dashboard.portfolio?.hhi, 3) }}</span><span>Vol20 {{ percentText(dashboard.portfolio?.portfolio_vol_20) }}</span><span>持仓 {{ dashboard.portfolio?.position_count ?? 0 }}</span></div>
            <n-alert v-if="dashboard.portfolio?.hard_cap_breaches?.length" type="error" :show-icon="false" class="compact-alert">Hard Cap：{{ dashboard.portfolio.hard_cap_breaches.join('、') }}</n-alert>
            <small v-else class="muted">未发现 Hard Cap breach</small>
          </section>

          <section class="panel-card hero-card decision-card">
            <div class="section-title"><div><h2>Today's Decision</h2><p>{{ fmt(latestDecision?.decision_at || latestAnalysis?.finished_at) }}</p></div><n-tag :type="actionType(finalAction)" :bordered="false">{{ actionText(finalAction) }}</n-tag></div>
            <div class="decision-copy">{{ actionText(finalAction) }}</div>
            <p v-if="todayAnalysisMissing" class="decision-message">今日尚未完成分析。</p>
            <p v-else-if="finalAction === 'NO_ACTION'" class="decision-message">当前建议：保持组合不变。</p>
            <p v-else-if="finalAction === 'BLOCKED'" class="decision-message">组合 Gate 或数据质量阻断风险增加。</p>
            <p v-else class="decision-message">请查看最新报告中的持仓动作与执行前提。</p>
            <div class="decision-meta"><span>质量 {{ latestDecision?.quality || latestAnalysis?.quality || '不可用' }}</span><span>置信度 {{ latestDecision?.confidence || latestAnalysis?.confidence || '不可用' }}</span></div>
            <n-button v-if="latestAnalysis?.analysis_run_id" text type="primary" @click="router.push({ name: 'reports', query: { run: latestAnalysis.analysis_run_id } })">查看完整报告 <ExternalLink :size="14" /></n-button>
            <p class="muted semantic-note">Candidate ACTION 只是进入决策候选，Portfolio Gate 具有最终优先级。</p>
          </section>

          <section class="panel-card hero-card">
            <div class="section-title"><div><h2>System Health</h2><p>{{ dashboard.trade_date }} · {{ dashboard.workflow_state }}</p></div><ShieldCheck v-if="healthStatus === 'OK'" :size="22" class="ok-icon" /><TriangleAlert v-else :size="22" class="warn-icon" /></div>
            <div class="health-value" :class="`health-${healthStatus.toLowerCase()}`">{{ healthStatus }}</div>
            <p class="health-reason">{{ healthIssue?.name || '关键组件运行正常' }}<span v-if="healthIssue">：{{ healthIssue.status }}</span></p>
            <div class="health-summary"><span>组件 {{ health?.components?.length || 0 }}</span><span>Mandatory {{ health?.components?.filter((item: any) => item.mandatory).length || 0 }}</span></div>
            <small class="muted">Dashboard 只读取已持久化事实，不触发重新计算。</small>
          </section>
        </div>

        <div class="section-grid wide-first">
          <section class="panel-card">
            <div class="section-title"><div><h2>Candidates</h2><p>确定性候选阶段，不是交易指令</p></div><n-tag :bordered="false">W {{ dashboard.candidates?.counts?.watchlist ?? 0 }} · R {{ dashboard.candidates?.counts?.ready ?? 0 }} · A {{ dashboard.candidates?.counts?.action ?? 0 }}</n-tag></div>
            <n-alert v-if="dashboard.candidates?.freshness === 'STALE'" type="warning" :show-icon="false" class="compact-alert">上次可靠 CandidateRun 已过期，ACTION 已暂停为可操作展示。</n-alert>
            <n-tabs v-model:value="candidateTab" type="line" animated>
              <n-tab-pane name="action" tab="ACTION">
                <div v-if="candidateItems.length" class="candidate-list"><article v-for="item in candidateItems.slice(0, 10)" :key="item.code" class="candidate-row"><div class="candidate-main"><div><strong>{{ item.name || item.code }}</strong><small>{{ item.code }} · {{ candidateStage(item) }}</small></div><n-tag size="small" type="warning" :bordered="false">进入决策候选</n-tag></div><div class="candidate-metrics"><span>Opportunity {{ numberText(item.opportunity_score, 1) }}</span><span>Entry {{ numberText(item.entry_score, 1) }}</span><span>Fit {{ numberText(item.portfolio_fit_score, 1) }}</span><span>Edge {{ numberText(item.decision_edge, 1) }}</span><span>R/R {{ numberText(item.risk_reward_ratio, 2) }}</span><span>置信度 {{ item.confidence ?? '不可用' }}</span></div><small class="muted">{{ item.funding_mode || 'funding mode 不可用' }} · {{ candidateReason(item) }}</small></article></div>
                <n-empty v-else description="当前没有 ACTION 候选" />
              </n-tab-pane>
              <n-tab-pane name="ready" tab="READY">
                <div v-if="candidateItems.length" class="candidate-list"><article v-for="item in candidateItems.slice(0, 10)" :key="item.code" class="candidate-row"><div class="candidate-main"><div><strong>{{ item.name || item.code }}</strong><small>{{ item.code }}</small></div><n-tag size="small" type="info" :bordered="false">READY</n-tag></div><div class="candidate-metrics"><span>Opportunity {{ numberText(item.opportunity_score, 1) }}</span><span>Entry {{ numberText(item.entry_score, 1) }}</span><span>Fit {{ numberText(item.portfolio_fit_score, 1) }}</span><span>Edge {{ numberText(item.decision_edge, 1) }}</span></div></article></div>
                <n-empty v-else description="当前没有 READY 候选" />
              </n-tab-pane>
              <n-tab-pane name="watchlist" tab="WATCHLIST">
                <div v-if="candidateItems.length" class="candidate-list"><article v-for="item in candidateItems.slice(0, 10)" :key="item.code" class="candidate-row"><div class="candidate-main"><div><strong>{{ item.name || item.code }}</strong><small>{{ item.code }}</small></div><n-tag size="small" :bordered="false">WATCHLIST</n-tag></div><div class="candidate-metrics"><span>Opportunity {{ numberText(item.opportunity_score, 1) }}</span><span>Edge {{ numberText(item.decision_edge, 1) }}</span><span>质量 {{ item.quality_status || '不可用' }}</span></div></article></div>
                <n-empty v-else description="当前没有 WATCHLIST 候选" />
              </n-tab-pane>
            </n-tabs>
            <div v-if="staleCandidates.length" class="stale-list"><strong>旧候选保留</strong><span v-for="item in staleCandidates.slice(0, 5)" :key="item.code">{{ item.name || item.code }} · STALE</span></div>
            <div class="section-footer"><span>Run #{{ dashboard.candidates?.run_id || '—' }} · {{ fmt(dashboard.candidates?.captured_at) }}</span><span v-if="dashboard.candidates?.scan_in_progress">正在扫描 Run #{{ dashboard.candidates.in_progress_run_id }}</span></div>
          </section>

          <section class="panel-card">
            <div class="section-title"><div><h2>Triggers</h2><p>Trigger = 需要重新分析，不是买卖信号</p></div><n-tag :bordered="false">今日 {{ dashboard.triggers?.today_count ?? 0 }}</n-tag></div>
            <div v-if="dashboard.triggers?.items?.length" class="trigger-list"><article v-for="item in dashboard.triggers.items.slice(0, 10)" :key="item.id" class="trigger-row"><div><strong>{{ item.priority }} · {{ item.trigger_type }}</strong><small>{{ item.target || '市场' }} · {{ fmt(item.detected_at) }}</small><span>{{ item.reason || '—' }}</span></div><div class="trigger-status"><n-tag size="small" :type="statusType(item.status)" :bordered="false">{{ item.status }}</n-tag><small v-if="item.analysis_job_id">Job #{{ item.analysis_job_id }}</small></div></article></div>
            <n-empty v-else description="当前没有已确认触发" />
          </section>
        </div>

        <div class="section-grid">
          <section class="panel-card">
            <div class="section-title"><div><h2>Today Timeline</h2><p>Asia/Shanghai · {{ dashboard.timeline?.workflow_state }}</p></div><n-tag type="info" :bordered="false">{{ currentCheckpoint?.label || '—' }}</n-tag></div>
            <div class="timeline-list"><article v-for="item in dashboard.timeline?.timeline || []" :key="item.key" class="timeline-row" :class="{ current: item.is_current }"><span class="timeline-time">{{ item.time }}</span><span class="timeline-dot" /><div><strong>{{ item.label }}</strong><small>{{ item.mode || item.kind }}<span v-if="item.reason"> · {{ item.reason }}</span></small></div><n-tag size="small" :type="statusType(item.status)" :bordered="false">{{ item.status || 'PENDING' }}</n-tag></article></div>
          </section>

          <section class="panel-card">
            <div class="section-title"><div><h2>Analysis</h2><p>最新成功 Decision 与当前进行中任务分开显示</p></div><n-tag :type="statusType(latestAnalysis?.status)" :bordered="false">{{ dashboard.analysis?.analysis_in_progress ? 'RUNNING' : latestAnalysis?.status || 'MISSING' }}</n-tag></div>
            <div v-if="dashboard.analysis?.analysis_in_progress" class="running-box"><CircleAlert :size="18" /><div><strong>分析进行中</strong><span v-for="job in dashboard.analysis.running_jobs || []" :key="job.id">{{ job.mode }} · {{ job.checkpoint || 'trigger' }} · Job #{{ job.id }}</span></div></div>
            <div class="analysis-detail"><span>最近模式 <strong>{{ latestAnalysis?.mode || '—' }}</strong></span><span>完成 {{ fmt(latestAnalysis?.finished_at) }}</span><span>质量 {{ latestAnalysis?.quality || '不可用' }}</span><span>ACTION 候选 {{ latestAnalysis?.candidate_action_count ?? 0 }}</span></div>
            <n-button v-if="latestAnalysis?.analysis_run_id" text type="primary" @click="router.push({ name: 'reports', query: { run: latestAnalysis.analysis_run_id } })">打开分析报告 <ExternalLink :size="14" /></n-button>
          </section>
        </div>

        <section class="panel-card health-panel">
          <div class="section-title"><div><h2>Data Health</h2><p>统一状态：OK / DEGRADED / BLOCKED / UNKNOWN</p></div><n-tag :type="statusType(healthStatus)" :bordered="false">{{ healthStatus }}</n-tag></div>
          <div class="health-grid"><article v-for="item in health?.components || []" :key="item.name" class="health-item"><div class="health-item-title"><strong>{{ item.name }}</strong><n-tag size="small" :type="statusType(item.status)" :bordered="false">{{ item.status }}</n-tag></div><small>{{ item.detail?.reason || item.detail?.freshness || item.detail?.status || (item.mandatory ? 'mandatory' : 'optional') }}</small><small v-if="item.detail?.last_error" class="error-text">{{ item.detail.last_error }}</small></article></div>
        </section>

        <div class="section-grid">
          <section class="panel-card">
            <div class="section-title"><div><h2>Memory / Review</h2><p>迟到 Outcome 会刷新同一 ReviewRun</p></div><n-tag :type="statusType(dashboard.memory?.review?.review_stale ? 'DEGRADED' : dashboard.memory?.review?.status)" :bordered="false">{{ dashboard.memory?.review?.review_stale ? '待刷新' : dashboard.memory?.review?.status || 'MISSING' }}</n-tag></div>
            <div class="review-stat-grid"><div><strong>{{ dashboard.memory?.today_decisions ?? 0 }}</strong><span>今日 Decision</span></div><div><strong>{{ dashboard.memory?.outcomes_matured ?? 0 }}</strong><span>今日成熟 Outcome</span></div><div><strong>{{ dashboard.memory?.review?.actual_execution_count ?? 0 }}</strong><span>实际执行</span></div><div><strong>{{ dashboard.memory?.historical_analogue_count ?? 0 }}</strong><span>历史案例</span></div></div>
            <div class="section-footer"><span>Review {{ fmt(dashboard.memory?.review?.last_refreshed_at || dashboard.memory?.review?.completed_at) }}</span><span>刷新 {{ dashboard.memory?.review?.refresh_count ?? 0 }} 次</span></div>
            <div v-if="dashboard.memory?.outcomes_matured_today?.length" class="outcome-list"><div v-for="item in dashboard.memory.outcomes_matured_today.slice(0, 6)" :key="item.id"><span>H{{ item.horizon_trading_days }} · {{ item.target_key }}</span><n-tag size="small" :type="statusType(item.quality_status || item.status)" :bordered="false">{{ item.status }}</n-tag></div></div>
          </section>

          <section class="panel-card">
            <div class="section-title"><div><h2>Today's Execution</h2><p>Ledger 事实与建议保持分离</p></div><n-tag :bordered="false">{{ dashboard.executions?.today_count ?? 0 }} 笔</n-tag></div>
            <div v-if="dashboard.executions?.items?.length" class="execution-list"><article v-for="item in dashboard.executions.items.slice(0, 10)" :key="item.id" class="execution-row"><div><strong>{{ item.code }}</strong><small>{{ item.side }} {{ item.qty }} @ {{ item.price }} · {{ fmt(item.executed_at) }}</small></div><div><n-tag size="small" :type="statusType(item.execution_alignment)" :bordered="false">{{ item.execution_alignment }}</n-tag><small v-if="item.linked_decision">Decision #{{ item.linked_decision }}</small></div></article></div>
            <n-empty v-else description="今日暂无确认成交" />
          </section>
        </div>

        <section class="panel-card notification-panel">
          <div class="section-title"><div><h2>Operating Notifications</h2><p>INFO 默认只在 Dashboard 展示，重要状态变化才尝试推送</p></div><n-tag :type="unreadNotifications ? 'warning' : 'success'" :bordered="false">未读 {{ unreadNotifications }}</n-tag></div>
          <div v-if="dashboard.notifications?.items?.length" class="notification-list"><article v-for="item in dashboard.notifications.items.slice(0, 12)" :key="item.notification_id" class="notification-row" :class="{ unread: !item.read }"><div><div class="notification-title"><Bell :size="14" /><strong>{{ item.title }}</strong><n-tag size="tiny" :type="notificationType(item)" :bordered="false">{{ item.severity }}</n-tag></div><p>{{ item.summary }}</p><small>{{ item.event_type }} · {{ fmt(item.occurred_at) }}</small></div><n-button v-if="!item.read" quaternary circle aria-label="标记已读" title="标记已读" @click="markRead(item)"><CheckCircle2 :size="16" /></n-button><span v-else class="read-mark">已读</span></article></div>
          <n-empty v-else description="暂无重要运行通知" />
        </section>

        <section class="panel-card holdings-panel">
          <div class="section-title"><div><h2>Holdings</h2><p>最新确认组合快照 · {{ fmt(dashboard.portfolio?.snapshot_time) }}</p></div><n-button secondary size="small" :disabled="!selectedId" @click="router.push({ name: 'upload', query: { portfolio: selectedId } })"><template #icon><Camera :size="14" /></template>上传新快照</n-button></div>
          <div v-if="dashboard.portfolio?.holdings?.length" class="holding-grid"><article v-for="item in dashboard.portfolio.holdings.slice(0, 12)" :key="item.code" class="holding-row"><div><strong>{{ item.name || item.code }}</strong><small>{{ item.code }} · 权重 {{ percentText(item.weight) }}</small></div><div><span>{{ numberText(item.price, 2) }}</span><small>Keep {{ item.keep_score ?? '不可用' }}</small></div><n-tag size="small" :type="actionType(item.holding_action)" :bordered="false">{{ item.holding_action || '未设动作' }}</n-tag></article></div>
          <n-empty v-else description="当前快照没有持仓明细" />
        </section>
      </template>
      <n-empty v-else description="暂无可读取的 Dashboard 数据" />
    </n-spin>

    <n-modal v-model:show="createOpen" preset="card" title="新建持仓组合" style="width: min(460px, 92vw)">
      <n-form label-placement="top"><n-form-item label="组合名称"><n-input v-model:value="newPortfolioName" aria-label="组合名称" placeholder="例如：主账户、ETF 账户" @keyup.enter="createPortfolio" /></n-form-item><n-button type="primary" block :loading="creating" @click="createPortfolio">创建组合</n-button></n-form>
    </n-modal>

    <n-modal v-model:show="analysisOpen" preset="card" title="手动分析最新持仓" style="width: min(480px, 92vw)">
      <n-alert type="info" :show-icon="false">使用 <strong>{{ analysisPortfolio?.name }}</strong> 的最新确认快照 #{{ analysisPortfolio?.latest_snapshot_id }}，不会重新识图或修改持仓。</n-alert>
      <n-form label-placement="top" class="analysis-modal-form">
        <n-form-item label="分析模式">
          <n-radio-group v-model:value="analysisMode">
            <n-radio-button value="fast">快速</n-radio-button>
            <n-radio-button value="standard">标准</n-radio-button>
            <n-radio-button value="deep">深度</n-radio-button>
          </n-radio-group>
        </n-form-item>
        <n-form-item label="检查点">
          <n-select v-model:value="analysisCheckpoint" :options="['09:35', '10:30', '13:05', '14:30', '15:10'].map((value) => ({ label: value, value }))" />
        </n-form-item>
        <n-form-item label="完成后发送通知">
          <n-switch v-model:value="analysisNotify" />
        </n-form-item>
        <n-button type="primary" block size="large" :loading="startingAnalysis" @click="startManualAnalysis">开始分析</n-button>
      </n-form>
    </n-modal>
  </section>
</template>

<style scoped>
.page-stack { display: grid; gap: 18px; }
.page-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; }
.eyebrow { margin: 0 0 5px; color: var(--app-primary); font-size: 11px; font-weight: 900; letter-spacing: .13em; }
h1 { margin: 0; font-size: 36px; }
.page-heading p:not(.eyebrow), .section-title p { margin: 6px 0 0; color: var(--app-text-muted); }
.heading-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
.portfolio-select { min-width: 170px; }
.as-of-line, .section-footer, .inline-meta, .health-summary, .decision-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; color: var(--app-text-muted); font-size: 12px; }
.as-of-line { min-height: 28px; }
.schedule-mark { color: var(--app-success); }
.readiness-badge { cursor: pointer; }
.hero-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.section-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
.wide-first { grid-template-columns: minmax(0, 1.45fr) minmax(320px, .8fr); }
.panel-card { min-width: 0; padding: 20px; }
.section-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.section-title h2 { margin: 0; font-size: 17px; }
.hero-value { margin-top: 4px; font-size: 34px; font-weight: 900; line-height: 1.1; }
.hero-label { margin-top: 4px; color: var(--app-text-muted); font-size: 12px; }
.inline-meta { margin-top: 14px; }
.metric-strip { display: flex; flex-wrap: wrap; gap: 7px 12px; margin: 16px 0 10px; color: var(--app-text-muted); font-size: 11px; }
.metric-strip span { border-bottom: 1px solid var(--app-border-soft); padding-bottom: 3px; }
.risk-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; margin: 16px 0; color: var(--app-text-muted); font-size: 12px; }
.decision-card { border-top: 3px solid var(--app-primary); }
.decision-copy { margin: 12px 0 8px; font-size: 28px; font-weight: 900; }
.decision-message { margin: 0 0 12px; font-size: 15px; font-weight: 700; }
.semantic-note { margin: 14px 0 0; font-size: 11px; line-height: 1.6; }
.health-value { font-size: 29px; font-weight: 900; }
.health-ok, .ok-icon { color: var(--app-success); }
.health-degraded, .health-unknown, .warn-icon { color: var(--app-warning); }
.health-blocked { color: var(--app-danger); }
.health-reason { min-height: 42px; margin: 10px 0; color: var(--app-text-muted); }
.health-reason span { display: block; margin-top: 3px; }
.compact-alert { margin: 10px 0 14px; }
.candidate-list, .trigger-list, .timeline-list, .execution-list, .notification-list, .holding-grid, .outcome-list { display: grid; gap: 8px; }
.candidate-row, .trigger-row, .timeline-row, .execution-row, .notification-row, .holding-row { border-bottom: 1px solid var(--app-border-soft); padding: 10px 0; }
.candidate-row:last-child, .trigger-row:last-child, .timeline-row:last-child, .execution-row:last-child, .notification-row:last-child, .holding-row:last-child { border-bottom: 0; }
.candidate-main, .notification-title, .health-item-title { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.candidate-main > div, .trigger-row > div:first-child, .execution-row > div:first-child, .holding-row > div { display: grid; gap: 3px; }
.candidate-main small, .trigger-row small, .execution-row small, .holding-row small, .health-item small, .notification-row small, .timeline-row small { color: var(--app-text-muted); font-size: 11px; }
.candidate-metrics { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 10px 0 6px; color: var(--app-text-muted); font-size: 11px; }
.stale-list { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 14px; color: var(--app-warning); font-size: 11px; }
.stale-list span { border: 1px solid color-mix(in srgb, var(--app-warning) 35%, transparent); padding: 3px 6px; }
.trigger-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.trigger-row > div:first-child { flex: 1; }
.trigger-row span { color: var(--app-text-muted); font-size: 12px; }
.trigger-status { display: grid; justify-items: end; gap: 4px; }
.timeline-row { display: grid; grid-template-columns: 46px 9px minmax(0, 1fr) auto; align-items: center; gap: 9px; }
.timeline-row.current { background: var(--app-primary-soft); padding: 10px 8px; }
.timeline-time { color: var(--app-primary); font-family: ui-monospace, monospace; font-size: 12px; }
.timeline-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--app-border); }
.timeline-row.current .timeline-dot { background: var(--app-primary); box-shadow: 0 0 0 4px var(--app-primary-soft); }
.timeline-row div { display: grid; gap: 3px; }
.running-box { display: flex; align-items: flex-start; gap: 10px; border-left: 3px solid var(--app-primary); background: var(--app-primary-soft); padding: 10px 12px; color: var(--app-primary); }
.running-box div { display: grid; gap: 3px; }
.running-box span { color: var(--app-text-muted); font-size: 11px; }
.analysis-detail { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 16px 0; color: var(--app-text-muted); font-size: 12px; }
.analysis-detail strong { display: block; margin-top: 2px; color: var(--app-text); }
.health-panel { padding-bottom: 16px; }
.health-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; }
.health-item { display: grid; gap: 7px; border: 1px solid var(--app-border-soft); padding: 11px; }
.health-item-title { align-items: flex-start; }
.error-text { color: var(--app-danger) !important; overflow-wrap: anywhere; }
.review-stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.review-stat-grid div { display: grid; gap: 4px; }
.review-stat-grid strong { color: var(--app-primary); font-size: 27px; line-height: 1; }
.review-stat-grid span { color: var(--app-text-muted); font-size: 11px; }
.outcome-list { margin-top: 16px; }
.outcome-list div { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--app-border-soft); padding: 7px 0; color: var(--app-text-muted); font-size: 12px; }
.execution-row, .holding-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.execution-row > div:last-child { display: grid; justify-items: end; gap: 3px; }
.notification-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.notification-row.unread { border-left: 3px solid var(--app-primary); padding-left: 10px; }
.notification-title { justify-content: flex-start; }
.notification-title svg { color: var(--app-primary); }
.notification-row p { margin: 6px 0; color: var(--app-text-muted); font-size: 12px; line-height: 1.6; }
.read-mark { color: var(--app-text-muted); font-size: 11px; }
.muted { color: var(--app-text-muted); }
.analysis-modal-form { margin-top: 16px; }
@media (max-width: 1250px) { .hero-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .health-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .page-heading { align-items: flex-start; flex-direction: column; } .heading-actions { width: 100%; justify-content: flex-start; } .section-grid, .wide-first { grid-template-columns: 1fr; } }
@media (max-width: 620px) { h1 { font-size: 30px; } .heading-actions > * { flex: 1 1 auto; } .portfolio-select { min-width: 140px; } .hero-grid, .health-grid { grid-template-columns: 1fr; } .panel-card { padding: 16px; } .review-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .timeline-row { grid-template-columns: 42px 8px minmax(0, 1fr); } .timeline-row .n-tag { grid-column: 3; justify-self: start; } .execution-row, .holding-row { align-items: flex-start; flex-wrap: wrap; } }
</style>
