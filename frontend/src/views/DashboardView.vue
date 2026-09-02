<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, CircleAlert, Plus, RefreshCw, Sparkles } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type { DailyDashboard, ModelProfile, ModelProvider, Portfolio } from '../api/types'
import DecisionHero from '../components/DecisionHero.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import FreshnessLabel from '../components/FreshnessLabel.vue'
import LoadingState from '../components/LoadingState.vue'
import MetricTile from '../components/MetricTile.vue'
import PageHeader from '../components/PageHeader.vue'
import SectionCard from '../components/SectionCard.vue'
import StatusIndicator from '../components/StatusIndicator.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { usePortfolioContext } from '../composables/portfolio'
import { formatCurrency, formatNumber, formatPercent } from '../utils/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const dashboard = ref<DailyDashboard | null>(null)
const providers = ref<ModelProvider[]>([])
const profiles = ref<ModelProfile[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const indicatorsOpen = ref(false)
const createOpen = ref(false)
const creating = ref(false)
const newPortfolioName = ref('我的主账户')
let refreshTimer: number | null = null

const { portfolios, selectedPortfolioId, selectedPortfolio, loadPortfolios, setSelectedPortfolio } = usePortfolioContext()
const hasPortfolio = computed(() => portfolios.value.length > 0 && Boolean(selectedPortfolioId.value))
const market = computed<Record<string, any>>(() => dashboard.value?.market || {})
const portfolio = computed<Record<string, any>>(() => dashboard.value?.portfolio || {})
const health = computed<Record<string, any>>(() => dashboard.value?.data_health || {})
const decision = computed<Record<string, any>>(() => dashboard.value?.decisions?.latest || {})
const analysis = computed<Record<string, any>>(() => dashboard.value?.analysis?.latest || {})
const hasTodayDecision = computed(() => Boolean(Object.keys(decision.value).length || Object.keys(analysis.value).length))
const finalAction = computed(() => {
  if (!hasTodayDecision.value) return 'NO_ACTION'
  const grade = String(analysis.value.quality || decision.value.quality || '').toUpperCase()
  if (grade === 'DATA_GAP') return 'DATA_GAP'
  if (grade === 'BLOCKED') return 'BLOCKED'
  return normalizeFinalAction(dashboard.value?.decisions?.final_action || decision.value.conclusion || analysis.value.portfolio_action)
})
const marketStatus = computed(() => String(market.value.health_status || market.value.freshness || market.value.status || 'MISSING').toUpperCase())
const systemStatus = computed(() => {
  const overall = String(health.value.overall || health.value.status || '').toUpperCase()
  if (overall === 'OK') return 'ok'
  if (overall === 'BLOCKED') return 'degraded'
  if (!hasPortfolio.value) return 'setup'
  return 'degraded'
})
const setupSteps = computed(() => [
  { key: 'config', title: '配置行情与模型', description: '让系统能读取市场和运行分析。', done: providers.value.some((item) => item.enabled) && profiles.value.some((item) => item.is_default), action: () => router.push({ name: 'settings' }), actionLabel: '去配置' },
  { key: 'holdings', title: '导入当前持仓', description: '上传券商截图并确认第一份组合快照。', done: hasPortfolio.value && Boolean(selectedPortfolio.value?.latest_snapshot_id), action: () => router.push({ name: 'holdings', query: { action: 'update' } }), actionLabel: '导入持仓' },
  { key: 'analysis', title: '完成第一次分析', description: '基于最近确认快照生成今日建议。', done: hasTodayDecision.value, action: () => router.push({ name: 'analysis' }), actionLabel: '开始分析' },
])
const candidates = computed<any[]>(() => {
  const source = (dashboard.value?.candidates || {}) as Record<string, any>
  return [...(source.action || []), ...(source.ready || []), ...(source.watchlist || [])].slice(0, 3)
})
const reasons = computed(() => {
  const raw = decision.value.reasons || decision.value.top_reasons || decision.value.blocking_reasons || analysis.value.reasons || []
  const list = Array.isArray(raw) ? raw.map((item: any) => typeof item === 'string' ? item : item.reason || item.summary || JSON.stringify(item)).filter(Boolean) : []
  if (list.length) return list
  if (!hasTodayDecision.value) return ['今天还没有完成分析，现有组合数据仍可查看。']
  if (finalAction.value === 'ACTION') return ['组合层已给出调整建议，请进入分析查看具体持仓动作和执行前提。']
  if (['BLOCKED', 'DATA_GAP'].includes(finalAction.value)) return ['市场或组合数据质量尚未满足可靠行动条件。']
  return ['当前没有足够的新信息改变组合决策。']
})
const holdingActions = computed<any[]>(() => {
  const raw = decision.value.holding_actions || analysis.value.holding_actions || []
  return Array.isArray(raw) ? raw : []
})
const marketRegime = computed(() => ({ BULL: '偏强', BEAR: '偏弱', NEUTRAL: '震荡', RANGE: '震荡', RISK_OFF: '风险偏高' }[String(market.value.regime || '').toUpperCase()] || market.value.regime || '状态未知'))

function metricValue(value: unknown, digits = 1) {
  return value == null || value === '' ? '不可用' : formatNumber(value, digits)
}

function ratioOrScore(value: unknown) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '不可用'
  return Math.abs(number) <= 1 ? formatPercent(number) : formatNumber(number, 1)
}

function actionLabel(action?: string | null) {
  return ({ ACTION: '需要调整', NO_ACTION: '暂不操作', BLOCKED: '暂不可形成可靠行动', DATA_GAP: '数据不完整' }[String(action || '').toUpperCase()] || String(action || '观察'))
}

function actionType(action?: string | null) {
  const value = String(action || '').toUpperCase()
  return ['BLOCKED', 'DATA_GAP'].includes(value) ? 'error' : value === 'ACTION' ? 'warning' : 'info'
}

function normalizeFinalAction(action: unknown): 'ACTION' | 'NO_ACTION' | 'BLOCKED' | 'DATA_GAP' {
  const value = String(action || '').toUpperCase()
  if (value === 'BLOCKED') return 'BLOCKED'
  if (value === 'DATA_GAP') return 'DATA_GAP'
  if (['ACTION', 'ADD', 'BUY', 'REDUCE', 'SELL', 'EXIT', 'REBALANCE'].includes(value)) return 'ACTION'
  return 'NO_ACTION'
}

function candidateStage(item: any) {
  const value = String(item.display_stage || item.stage || item.candidate_engine_stage || 'WATCH').toUpperCase()
  return value === 'WATCHLIST' ? 'WATCH' : value
}

function candidateReason(item: any) {
  const raw = item.reason || item.summary || item.rationale || item.blocking_reasons?.[0] || item.blocking_reasons_json?.[0]
  return raw ? String(raw) : '等待更多确认信号'
}

function candidateGate(item: any) {
  if (item.buyable === true || item.actionable === true || String(item.portfolio_gate || '').toUpperCase() === 'PASS') return '组合已通过'
  return '组合层未批准'
}

function candidateRisk(item: any) {
  const value = item.risk || item.risk_flags || item.risk_level
  if (Array.isArray(value)) return value.join('、') || '不可用'
  return value ? String(value) : '不可用'
}

function openAnalysis() {
  void router.push({ name: 'analysis', query: { portfolio: selectedPortfolioId.value || undefined, run: analysis.value.analysis_run_id || decision.value.analysis_run_id || undefined } })
}

async function loadDashboard(silent = false) {
  if (!selectedPortfolioId.value) {
    dashboard.value = null
    return
  }
  if (!silent) loading.value = true
  error.value = null
  try {
    dashboard.value = await api.getDashboardToday(selectedPortfolioId.value)
  } catch (reason) {
    error.value = reason
  } finally {
    if (!silent) loading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    await loadPortfolios()
    const requested = Number(route.query.portfolio)
    if (requested && portfolios.value.some((item) => item.id === requested)) setSelectedPortfolio(requested)
    const [providerRows, profileRows] = await Promise.all([api.listProviders(), api.listProfiles()])
    providers.value = providerRows
    profiles.value = profileRows
    await loadDashboard(true)
  } catch (reason) {
    error.value = reason
  } finally {
    loading.value = false
  }
}

async function createPortfolio() {
  const name = newPortfolioName.value.trim()
  if (!name || creating.value) return
  creating.value = true
  try {
    const created = await api.createPortfolio({ name, is_default: portfolios.value.length === 0 })
    portfolios.value.push(created)
    setSelectedPortfolio(created.id)
    createOpen.value = false
    message.success('组合已创建')
    await loadDashboard()
  } catch (reason) {
    message.error((reason as Error).message)
  } finally {
    creating.value = false
  }
}

function startRefresh() {
  if (refreshTimer !== null) window.clearInterval(refreshTimer)
  refreshTimer = window.setInterval(() => void loadDashboard(true), 30_000)
}

watch(selectedPortfolioId, (id, previous) => {
  if (id && id !== previous) void loadDashboard()
})
onMounted(async () => { await load(); startRefresh() })
onUnmounted(() => { if (refreshTimer !== null) window.clearInterval(refreshTimer) })
</script>

<template>
  <section class="workbench-page">
    <PageHeader :title="dashboard?.trade_date ? `今天 · ${dashboard.trade_date}` : '今天的投资驾驶舱'" description="先看市场，再看组合和今日建议。">
      <template #actions>
        <n-button secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
        <n-button v-if="hasPortfolio" secondary @click="router.push({ name: 'holdings', query: { action: 'update' } })">更新持仓</n-button>
        <n-button v-if="hasPortfolio" type="primary" @click="openAnalysis"><template #icon><Sparkles :size="16" /></template>查看今日分析</n-button>
        <n-button v-else secondary @click="createOpen = true"><template #icon><Plus :size="16" /></template>新建组合</n-button>
      </template>
    </PageHeader>

    <ErrorState v-if="error" :error="error" @retry="load" />
    <LoadingState v-else-if="loading && !dashboard && hasPortfolio" message="正在读取今天的市场与组合" />

    <section v-if="!hasPortfolio && !loading && !error" class="setup-card panel-card">
      <div class="setup-title"><div><p class="page-eyebrow">FIRST RUN</p><h2>开始使用</h2><p>完成下面三步，就可以每天快速看清市场、组合和建议。</p></div><Sparkles :size="25" /></div>
      <ol class="setup-list"><li v-for="(step, index) in setupSteps" :key="step.key" :class="{ done: step.done }"><div class="step-index">{{ step.done ? '✓' : index + 1 }}</div><div class="step-copy"><strong>{{ step.title }}</strong><span>{{ step.done ? '已完成' : step.description }}</span></div><n-button v-if="!step.done" secondary size="small" @click="step.action">{{ step.actionLabel }}<ArrowRight :size="14" /></n-button><span v-else class="step-done">已完成</span></li></ol>
      <p class="setup-note">技术状态会在需要时显示，现在只关注下一步。</p>
    </section>

    <template v-if="dashboard">
      <div v-if="marketStatus === 'BLOCKED' || marketStatus === 'DATA_GAP' || marketStatus === 'MISSING'" class="quality-banner"><CircleAlert :size="17" /><div><strong>市场数据暂不完整</strong><p>当前不生成激进风险建议，已有持仓信息仍可查看。</p></div><n-button text @click="indicatorsOpen = true">查看原因</n-button></div>

      <SectionCard title="今日市场" :description="dashboard.market_open ? '市场数据来自最近可用的生产快照。' : 'A 股今日休市，下面展示最近一个交易日的市场状态。'">
        <template #actions><FreshnessLabel :freshness="market.freshness" :at="market.captured_at" /><n-button text type="primary" @click="indicatorsOpen = !indicatorsOpen">{{ indicatorsOpen ? '收起指标' : '查看指标' }}<ArrowRight :size="14" /></n-button></template>
        <div class="market-head"><div><strong class="market-score mono-number">{{ metricValue(market.score, 1) }}</strong><span>Market Score</span></div><div><strong>{{ marketRegime }}</strong><span>{{ market.regime || 'Regime 不可用' }}</span></div><StatusIndicator :status="marketStatus === 'VALID' || marketStatus === 'FRESH' ? 'ok' : 'degraded'" :label="market.quality_status === 'VALID' ? '质量正常' : '需要关注'" /></div>
        <div class="market-metrics"><MetricTile label="全 A 中位数" :value="metricValue(market.all_a_median?.index_value, 2)" helper="最近可用交易日" /><MetricTile label="成交集中度" :value="formatPercent(market.components?.top5_turnover_concentration)" /><MetricTile label="市场广度" :value="ratioOrScore(market.advance_ratio ?? market.breadth_ratio ?? market.components?.breadth)" /><MetricTile label="覆盖率" :value="formatPercent(market.coverage ?? market.metrics?.coverage)" /></div>
        <p class="market-summary">{{ market.summary || (market.components?.breadth != null ? `市场广度指标为 ${metricValue(market.components.breadth, 1)}，建议结合组合暴露决定是否增加风险。` : '当前市场数据已加载，可在详情中查看量化指标。') }}</p>
        <TechnicalDetails v-if="indicatorsOpen" title="市场指标与数据质量" name="market-details" :default-open="true"><div class="detail-grid"><div><span>Trend</span><strong>{{ metricValue(market.components?.trend) }}</strong></div><div><span>Liquidity</span><strong>{{ metricValue(market.components?.liquidity) }}</strong></div><div><span>Profitability</span><strong>{{ metricValue(market.components?.profitability) }}</strong></div><div><span>Diffusion</span><strong>{{ metricValue(market.components?.diffusion) }}</strong></div><div><span>Crowding</span><strong>{{ metricValue(market.components?.crowding) }}</strong></div><div><span>Tail Risk</span><strong>{{ metricValue(market.components?.tail_risk) }}</strong></div><div><span>Coverage</span><strong>{{ formatPercent(market.coverage ?? market.metrics?.coverage) }}</strong></div><div><span>Source</span><strong>{{ market.market_score_source || '—' }}</strong></div></div><pre>{{ JSON.stringify({ market, data_health: dashboard.data_health }, null, 2) }}</pre></TechnicalDetails>
      </SectionCard>

      <div class="decision-grid">
        <DecisionHero class="decision-card" :action="finalAction" :summary="hasTodayDecision ? (decision.portfolio_conclusion || analysis.summary || '当前组合建议已生成，请根据持仓动作决定是否执行。') : '今天尚未完成分析，现有组合数据仍可查看。'" :reasons="reasons" :checkpoint="decision.checkpoint || analysis.checkpoint" :finalized-at="decision.decision_at || analysis.finished_at" :quality="decision.quality || analysis.quality" :freshness="market.freshness">
          <template #actions><n-button type="primary" @click="openAnalysis">{{ hasTodayDecision ? '查看完整分析' : '完成第一次分析' }}<ArrowRight :size="14" /></n-button></template>
        </DecisionHero>

        <SectionCard title="我的组合" description="截至最近确认快照">
          <template #actions><n-button text type="primary" @click="router.push({ name: 'holdings' })">查看持仓<ArrowRight :size="14" /></n-button></template>
          <div class="portfolio-metrics"><MetricTile label="总资产" :value="formatCurrency(portfolio.total_assets, 2)" /><MetricTile label="持仓市值" :value="formatCurrency(portfolio.market_value, 2)" /><MetricTile label="可用现金" :value="formatCurrency(portfolio.spendable_cash, 2)" /><MetricTile label="仓位" :value="formatPercent(portfolio.gross_exposure)" tone="risk" /></div>
          <div class="portfolio-meta"><span>持仓 {{ portfolio.position_count ?? '不可用' }} 个</span><span>确认时间 {{ portfolio.snapshot_time ? new Date(portfolio.snapshot_time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—' }}</span></div>
          <p v-if="portfolio.risk_flags?.length" class="risk-note">主要风险：{{ portfolio.risk_flags.slice(0, 2).join('、') }}</p>
        </SectionCard>
      </div>

      <SectionCard title="关注机会" description="最多展示三个值得继续观察的候选，候选不是最终交易指令。">
        <template #actions><n-button text type="primary" @click="router.push({ name: 'analysis' })">查看全部<ArrowRight :size="14" /></n-button></template>
        <div v-if="candidates.length" class="opportunity-list"><article v-for="item in candidates" :key="item.code || item.name" class="opportunity-row"><div class="opportunity-identity"><strong>{{ item.name || item.code || '未命名标的' }}</strong><small>{{ item.code || '代码待匹配' }}</small></div><n-tag size="small" :bordered="false" :type="candidateStage(item) === 'ACTION' ? 'warning' : candidateStage(item) === 'READY' ? 'info' : 'default'">{{ candidateStage(item) === 'ACTION' ? '需要调整' : candidateStage(item) === 'READY' ? '准备' : '观察' }}</n-tag><p>{{ candidateReason(item) }}</p><span class="opportunity-meta">风险：{{ candidateRisk(item) }}</span><span class="opportunity-gate">{{ candidateGate(item) }}</span></article></div>
        <EmptyState v-else title="当前没有明显的新机会" description="当前没有明显优于保持现状的机会。系统仍会在下一次可靠扫描后更新候选。" />
      </SectionCard>

      <div class="home-system-line"><StatusIndicator :status="systemStatus" /><span>数据状态 · {{ dashboard.as_of ? new Date(dashboard.as_of).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—' }}</span><button type="button" @click="router.push({ name: 'settings', query: { section: 'system' } })">查看系统状态<ArrowRight :size="13" /></button></div>
      <p class="disclaimer">系统提供研究与决策辅助，不保证收益。</p>
    </template>

    <n-modal v-model:show="createOpen" preset="card" title="新建持仓组合" style="width: min(460px, calc(100vw - 32px))"><n-form label-placement="top"><n-form-item label="组合名称"><n-input v-model:value="newPortfolioName" aria-label="组合名称" placeholder="例如：主账户、ETF 账户" @keyup.enter="createPortfolio" /></n-form-item><n-button type="primary" block :loading="creating" @click="createPortfolio">创建组合</n-button></n-form></n-modal>
  </section>
</template>

<style scoped>
.workbench-page { display: grid; gap: 18px; }.setup-card { padding: 26px; }.setup-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--border); padding-bottom: 18px; }.setup-title h2 { margin: 0; font-size: 25px; }.setup-title p:not(.page-eyebrow) { margin: 7px 0 0; color: var(--text-muted); }.setup-title > svg { color: var(--primary); }.page-eyebrow { margin: 0 0 5px; color: var(--primary); font-size: 10px; font-weight: 800; letter-spacing: .08em; }.setup-list { display: grid; gap: 0; margin: 20px 0 0; padding: 0; list-style: none; }.setup-list li { display: flex; align-items: center; gap: 12px; border-bottom: 1px solid var(--border); padding: 15px 0; }.setup-list li:last-child { border-bottom: 0; }.step-index { display: grid; width: 30px; height: 30px; flex: none; place-items: center; border-radius: 50%; background: var(--primary-soft); color: var(--primary); font-weight: 800; }.setup-list li.done .step-index { background: color-mix(in srgb, var(--negative) 14%, transparent); color: var(--negative); }.step-copy { display: grid; flex: 1; min-width: 0; gap: 3px; }.step-copy span { color: var(--text-muted); font-size: 12px; }.step-done { color: var(--negative); font-size: 12px; }.setup-note { margin: 18px 0 0; color: var(--text-muted); font-size: 12px; }.quality-banner { display: flex; align-items: flex-start; gap: 10px; border: 1px solid color-mix(in srgb, var(--warning) 35%, var(--border)); border-radius: 8px; background: color-mix(in srgb, var(--warning) 9%, var(--surface)); padding: 12px 14px; }.quality-banner > svg { flex: none; color: var(--warning); margin-top: 2px; }.quality-banner div { flex: 1; min-width: 0; }.quality-banner p { margin: 4px 0 0; color: var(--text-muted); font-size: 12px; }.market-head { display: grid; grid-template-columns: 1.4fr 1fr auto; align-items: center; gap: 22px; border-bottom: 1px solid var(--border); padding: 4px 0 18px; }.market-head > div { display: grid; gap: 4px; }.market-head span { color: var(--text-muted); font-size: 12px; }.market-head strong { font-size: 19px; }.market-head .market-score { font-size: 36px; line-height: 1; color: var(--primary); }.market-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; padding: 18px 0 2px; }.market-summary { max-width: 760px; margin: 16px 0 0; color: var(--text-muted); line-height: 1.65; }.detail-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }.detail-grid div { display: grid; gap: 4px; border-left: 2px solid var(--border); padding-left: 10px; }.detail-grid span { color: var(--text-muted); font-size: 11px; }.detail-grid strong { color: var(--text); font-size: 13px; }.detail-grid + pre { max-height: 280px; overflow: auto; white-space: pre-wrap; word-break: break-word; }.decision-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr); gap: 18px; align-items: stretch; }.decision-card { height: 100%; }.portfolio-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 14px; }.portfolio-meta { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 22px; color: var(--text-muted); font-size: 12px; }.risk-note { margin: 14px 0 0; border-left: 3px solid var(--risk-high); background: color-mix(in srgb, var(--risk-high) 9%, transparent); padding: 8px 10px; color: var(--text-muted); font-size: 12px; }.opportunity-list { display: grid; }.opportunity-row { display: grid; grid-template-columns: minmax(150px, .7fr) auto minmax(0, 1.8fr) auto auto; align-items: center; gap: 12px; border-top: 1px solid var(--border); padding: 14px 0; }.opportunity-row:first-child { border-top: 0; padding-top: 0; }.opportunity-row:last-child { padding-bottom: 0; }.opportunity-identity { display: grid; gap: 3px; }.opportunity-identity small { color: var(--text-muted); font-size: 11px; }.opportunity-row p { margin: 0; color: var(--text-muted); line-height: 1.5; }.opportunity-meta, .opportunity-gate { color: var(--text-muted); font-size: 11px; white-space: nowrap; }.home-system-line { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; color: var(--text-muted); font-size: 12px; }.home-system-line button { display: inline-flex; align-items: center; gap: 4px; border: 0; background: none; padding: 0; color: var(--primary); cursor: pointer; }.disclaimer { margin: -7px 0 0; color: var(--text-muted); font-size: 11px; text-align: center; }
@media (max-width: 850px) { .decision-grid { grid-template-columns: 1fr; }.market-head { grid-template-columns: 1fr 1fr; }.market-head .status-indicator { grid-column: 1 / -1; }.opportunity-row { grid-template-columns: minmax(120px, .7fr) auto; }.opportunity-row p, .opportunity-meta, .opportunity-gate { grid-column: 1 / -1; } }
@media (max-width: 640px) { .market-metrics, .detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.portfolio-metrics { grid-template-columns: 1fr; }.setup-card { padding: 18px; }.setup-list li { align-items: flex-start; }.setup-list li .n-button { align-self: center; }.quality-banner { flex-wrap: wrap; }.quality-banner > .n-button { margin-left: 27px; } }
@media (max-width: 430px) { .market-head { grid-template-columns: 1fr; }.market-head .status-indicator { grid-column: auto; }.market-metrics, .detail-grid { grid-template-columns: 1fr; }.setup-list li { flex-wrap: wrap; }.setup-list li .n-button { margin-left: 42px; } }
</style>
