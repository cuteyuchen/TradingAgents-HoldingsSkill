<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
import { ArrowRight, Check, CircleAlert, Image, LoaderCircle, RefreshCw } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type { AnalysisJob, AnalysisRunDetail, AnalysisRunSummary } from '../api/types'
import DecisionHero from '../components/DecisionHero.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import FreshnessLabel from '../components/FreshnessLabel.vue'
import LoadingState from '../components/LoadingState.vue'
import PageHeader from '../components/PageHeader.vue'
import SectionCard from '../components/SectionCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { usePortfolioContext } from '../composables/portfolio'
import { actionLabel, formatNumber, formatPercent, fmtDateTime, unavailableText } from '../utils/ui'

type AnyRecord = Record<string, any>

const route = useRoute()
const router = useRouter()
const message = useMessage()
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const runs = ref<AnalysisRunSummary[]>([])
const detail = ref<AnalysisRunDetail | null>(null)
const selectedRunId = ref<number | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const loadError = ref<unknown>(null)
const detailError = ref<unknown>(null)
const activeTab = ref('summary')
const candidateDetail = ref<AnyRecord | null>(null)
const candidateDrawerOpen = computed({
  get: () => Boolean(candidateDetail.value),
  set: (value: boolean) => { if (!value) candidateDetail.value = null },
})
const screenshotUrl = ref('')
const job = ref<AnalysisJob | null>(null)
let mounted = false
let jobTimer: number | null = null
let jobBusy = false

const { portfolios, selectedPortfolioId: portfolioId, selectedPortfolio, loadPortfolios, setSelectedPortfolio } = usePortfolioContext()
const structured = computed<AnyRecord>(() => detail.value?.structured_result || {})
const result = computed<AnyRecord>(() => structured.value.result || {})
const workflow = computed<AnyRecord>(() => structured.value.workflow || structured.value.analysis_workflow || result.value)
const market = computed<AnyRecord>(() => structured.value.market_snapshot || {})
const decisionGate = computed<AnyRecord>(() => section('decision_gate') || {})
const qualityGate = computed<AnyRecord>(() => section('quality_gate') || {})
const candidates = computed<AnyRecord[]>(() => {
  const value = section('candidates', 'buy_candidates', 'rotation_candidates')
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.items)) return value.items
  return []
})
const holdingActions = computed<AnyRecord[]>(() => {
  const value = section('today_actions', 'holdings', 'holding_actions')
  return Array.isArray(value) ? value : []
})
const candidateVetoes = computed<AnyRecord[]>(() => {
  const value = section('candidate_vetoes', 'candidate_veto', 'candidate_rejections', 'candidate_exclusions')
    || (section('candidates', 'buy_candidates') as AnyRecord)?.vetoes
  if (!value) return []
  return Array.isArray(value) ? value : [value]
})
const finalDecision = computed(() => {
  const quality = String(detail.value?.data_quality_grade || result.value.data_quality_grade || '').toUpperCase()
  if (quality === 'BLOCKED') return 'BLOCKED'
  if (quality === 'DATA_GAP') return 'DATA_GAP'
  return normalizeFinalDecision(decisionGate.value.portfolio_action || result.value.final_action || result.value.final_rating || result.value.portfolio_action || detail.value?.final_rating || 'NO_ACTION')
})
const finalSummary = computed(() => String(result.value.portfolio_conclusion || result.value.summary || detail.value?.summary || '当前没有足够的新信息改变组合决策。'))
const finalReasons = computed(() => {
  const raw = result.value.reason_codes || result.value.reasons || qualityGate.value.blockers || result.value.risk_warnings || []
  const list = Array.isArray(raw) ? raw.map((item) => typeof item === 'string' ? item : item.reason || item.summary || JSON.stringify(item)).filter(Boolean) : []
  if (list.length) return list
  if (finalDecision.value === 'ACTION') return ['组合层已给出调整建议，请核对持仓动作和执行前提。']
  if (['BLOCKED', 'DATA_GAP'].includes(finalDecision.value)) return ['市场或组合数据质量尚未满足可靠行动条件。']
  return ['当前没有足够的新信息改变组合决策。']
})
const analysisState = computed<'idle' | 'running' | 'succeeded' | 'failed'>(() => {
  if (job.value && ['queued', 'running'].includes(String(job.value.status).toLowerCase())) return 'running'
  if (job.value?.status === 'failed') return 'failed'
  if (detail.value) return 'succeeded'
  return 'idle'
})
const progressStages = computed(() => {
  const current = String(job.value?.current_stage || '').toLowerCase()
  const percent = Number(job.value?.progress_percent || 0)
  return [
    { key: 'market', label: '市场环境', done: percent >= 20 || ['quality_gate', 'investment_debate', 'research_verdict', 'trader_proposal', 'risk_revision', 'risk_debate', 'candidate_screening', 'portfolio_synthesis', 'completed'].includes(current) },
    { key: 'portfolio', label: '持仓风险', done: percent >= 40 || ['investment_debate', 'research_verdict', 'trader_proposal', 'risk_revision', 'risk_debate', 'candidate_screening', 'portfolio_synthesis', 'completed'].includes(current) },
    { key: 'candidate', label: '候选机会', done: percent >= 70 || ['portfolio_synthesis', 'completed'].includes(current) },
    { key: 'decision', label: '组合决策', done: percent >= 100 || current === 'completed' },
  ]
})
const renderedMarkdown = computed(() => markdown.render(detail.value?.markdown || ''))
const evidencePack = computed(() => section('evidence_pack', 'evidence') || {})
const advancedEntries = computed(() => Object.entries({
  '数据质量': detail.value?.data_quality_grade || result.value.data_quality_grade,
  '参数版本': structured.value.parameter_set_version || result.value.parameter_set_version,
  '运行时': structured.value.runtime_contract_version || result.value.runtime_contract_version,
  '来源链': evidencePack.value.source_chain || market.value.source_chain,
}).filter(([, value]) => value !== null && value !== undefined && value !== ''))

function section(...keys: string[]): any {
  for (const source of [result.value, workflow.value, structured.value]) {
    for (const key of keys) if (source?.[key] !== undefined && source[key] !== null) return source[key]
  }
  return null
}

function normalizeFinalDecision(value: unknown): 'ACTION' | 'NO_ACTION' | 'BLOCKED' | 'DATA_GAP' {
  const normalized = String(value || '').toUpperCase()
  if (normalized === 'BLOCKED') return 'BLOCKED'
  if (normalized === 'DATA_GAP') return 'DATA_GAP'
  if (['ACTION', 'ADD', 'BUY', 'REDUCE', 'SELL', 'EXIT', 'REBALANCE', 'OVERWEIGHT', 'UNDERWEIGHT'].includes(normalized)) return 'ACTION'
  return 'NO_ACTION'
}

function hasContent(value: any): boolean {
  if (value === null || value === undefined || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'object') return Object.keys(value).length > 0
  return true
}

function textValue(value: any): string {
  if (!hasContent(value)) return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map((item) => textValue(item?.summary || item?.reason || item?.claim || item)).filter(Boolean).join('；')
  if (typeof value === 'object') return textValue(value.summary || value.reason || value.conclusion || value.content || value.report) || JSON.stringify(value)
  return String(value)
}

function candidateStage(row: AnyRecord) {
  const value = String(row.display_stage || row.stage || row.candidate_engine_stage || row.status || 'WATCH').toUpperCase()
  return value === 'WATCHLIST' ? 'WATCH' : value
}

function candidateReason(row: AnyRecord) {
  return textValue(row.reason || row.summary || row.rationale || row.blocking_reasons?.[0]) || '等待更多确认信号'
}

function candidateGate(row: AnyRecord) {
  if (row.buyable === true || row.actionable === true || String(row.portfolio_gate || '').toUpperCase() === 'PASS') return '组合已批准'
  return '组合层未批准'
}

function candidateMetric(row: AnyRecord, keys: string[], percent = false) {
  const value = keys.map((key) => row[key]).find((item) => item !== undefined && item !== null && item !== '')
  if (value === undefined || value === null || value === '' || (finalDecision.value === 'DATA_GAP' && Number(value) === 0)) return unavailableText
  return percent ? formatPercent(value) : formatNumber(value, 2)
}

function actionType(action: string) {
  const value = String(action || '').toLowerCase()
  return ['sell', 'reduce', 'blocked', 'data_gap'].includes(value) ? 'error' : ['add', 'new_position', 'add_existing'].includes(value) ? 'success' : 'info'
}

function openCandidate(row: AnyRecord) {
  candidateDetail.value = row
}

async function loadRuns() {
  loading.value = true
  loadError.value = null
  try {
    runs.value = await api.listRuns(portfolioId.value || undefined)
    const requested = Number(route.query.run)
    const next = runs.value.find((item) => item.id === requested)?.id || selectedRunId.value || runs.value[0]?.id || null
    if (next) await selectRun(next)
    else detail.value = null
  } catch (reason) {
    loadError.value = reason
  } finally {
    loading.value = false
  }
}

async function selectRun(id: number) {
  selectedRunId.value = id
  detailLoading.value = true
  detailError.value = null
  try {
    detail.value = await api.getRun(id)
    await router.replace({ name: 'analysis', query: { ...(portfolioId.value ? { portfolio: portfolioId.value } : {}), run: id } })
    if (activeTab.value === 'screenshot') await loadScreenshot()
  } catch (reason) {
    detailError.value = reason
  } finally {
    detailLoading.value = false
  }
}

async function loadScreenshot() {
  if (!detail.value || screenshotUrl.value) return
  try {
    const snapshot = await api.getSnapshot(detail.value.portfolio_snapshot_id)
    if (snapshot.upload_id) screenshotUrl.value = URL.createObjectURL(await api.getUploadImage(snapshot.upload_id))
  } catch (reason) {
    message.error((reason as Error).message)
  }
}

async function loadJob(jobId: number) {
  try {
    job.value = await api.getAnalysisJob(jobId)
    if (!['queued', 'running'].includes(String(job.value.status).toLowerCase())) {
      if (job.value.run_id) await selectRun(job.value.run_id)
      return
    }
    startJobPolling()
  } catch (reason) {
    loadError.value = reason
  }
}

function startJobPolling() {
  if (jobTimer !== null) window.clearInterval(jobTimer)
  jobTimer = window.setInterval(async () => {
    if (!job.value || jobBusy) return
    jobBusy = true
    try {
      job.value = await api.getAnalysisJob(job.value.id)
      if (!['queued', 'running'].includes(String(job.value.status).toLowerCase())) {
        if (jobTimer !== null) window.clearInterval(jobTimer)
        jobTimer = null
        if (job.value.status === 'succeeded' && job.value.run_id) await selectRun(job.value.run_id)
      }
    } catch (reason) {
      loadError.value = reason
    } finally {
      jobBusy = false
    }
  }, 1400)
}

function startNewAnalysis() {
  void router.push({ name: 'holdings', query: { portfolio: portfolioId.value || undefined, action: 'update', focus: 'analysis' } })
}

function openSettings() {
  void router.push({ name: 'settings', query: { section: 'system' } })
}

watch(portfolioId, () => { if (mounted) void loadRuns() })
watch(activeTab, (tab) => { if (tab === 'screenshot') void loadScreenshot() })
onMounted(async () => {
  const requestedJob = Number(route.query.job)
  try {
    await loadPortfolios()
    const requestedPortfolio = Number(route.query.portfolio)
    if (requestedPortfolio && portfolios.value.some((item) => item.id === requestedPortfolio)) setSelectedPortfolio(requestedPortfolio)
    await loadRuns()
    if (requestedJob) await loadJob(requestedJob)
  } catch (reason) {
    loadError.value = reason
  }
  mounted = true
})
onUnmounted(() => {
  if (jobTimer !== null) window.clearInterval(jobTimer)
  if (screenshotUrl.value) URL.revokeObjectURL(screenshotUrl.value)
})
</script>

<template>
  <section class="workbench-page">
    <PageHeader title="今日分析" description="先看最终组合结论，再按需查看原因、动作和详细证据。">
      <template #actions>
        <n-button secondary :loading="loading" @click="loadRuns"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
        <n-button type="primary" @click="startNewAnalysis"><template #icon><LoaderCircle :size="16" /></template>重新分析</n-button>
      </template>
    </PageHeader>

    <ErrorState v-if="loadError" :error="loadError" @retry="loadRuns" />
    <LoadingState v-else-if="loading && !detail" message="正在读取最近一次分析" />

    <section v-if="analysisState === 'running'" class="progress-panel panel-card">
      <div class="progress-title"><div><p class="page-eyebrow">ANALYSIS RUNNING</p><h2>正在分析你的组合</h2><p>分析完成后会回到最终组合结论。</p></div><strong>{{ job?.progress_percent || 0 }}%</strong></div>
      <div class="progress-steps"><div v-for="step in progressStages" :key="step.key" :class="{ done: step.done, current: !step.done && progressStages.findIndex((item) => !item.done) === progressStages.indexOf(step) }"><span class="step-icon"><Check v-if="step.done" :size="14" /><span v-else>{{ progressStages.indexOf(step) + 1 }}</span></span><strong>{{ step.label }}</strong><small>{{ step.done ? '已完成' : step === progressStages.find((item) => !item.done) ? '进行中' : '等待' }}</small></div></div>
      <n-progress type="line" :percentage="job?.progress_percent || 0" processing :show-indicator="false" />
      <TechnicalDetails title="技术详情"><pre>{{ JSON.stringify(job, null, 2) }}</pre></TechnicalDetails>
    </section>

    <section v-else-if="analysisState === 'failed'" class="failed-panel panel-card"><CircleAlert :size="24" /><div><h2>分析没有完成</h2><p>组合决策阶段出现错误，前面已完成的数据不会被冒充为最终建议。</p><n-button type="primary" @click="startNewAnalysis">重试分析</n-button></div><TechnicalDetails title="查看错误详情"><pre>{{ JSON.stringify(job, null, 2) }}</pre></TechnicalDetails></section>

    <EmptyState v-else-if="!detail" title="还没有可查看的分析" description="完成一次持仓确认并开始分析后，这里会显示最终组合决策和原因。">
      <template #action><n-button type="primary" @click="startNewAnalysis">开始分析</n-button></template>
    </EmptyState>

    <template v-if="detail">
      <ErrorState v-if="detailError" :error="detailError" @retry="selectRun(selectedRunId!)" />
      <LoadingState v-else-if="detailLoading" message="正在读取分析详情" />
      <template v-else>
        <DecisionHero class="decision-hero" :action="finalDecision" :summary="finalSummary" :reasons="finalReasons" :checkpoint="result.checkpoint || structured.checkpoint || '—'" :finalized-at="structured.finished_at || detail.created_at" :quality="detail.data_quality_grade" :freshness="market.freshness">
          <template #actions><span class="muted">最终组合决策优先于候选机会。</span><n-button type="primary" @click="activeTab = 'workflow'">查看完整分析<ArrowRight :size="14" /></n-button></template>
        </DecisionHero>

        <SectionCard title="为什么" description="以下内容来自后端已有的结构化原因与证据，不在前端重新生成解释。">
          <div class="why-grid"><article><span>市场</span><strong>{{ market.regime || '市场状态待确认' }}</strong><p>{{ textValue(market.summary || market.market_read || result.market_reason) || '市场原因会随本次分析的结构化证据展示。' }}</p></article><article><span>组合</span><strong>{{ textValue(result.portfolio_reason || result.portfolio_risk || result.cash_target) || '组合风险待确认' }}</strong><p>{{ textValue(result.portfolio_conclusion || qualityGate.blockers?.[0]) || '当前组合约束已纳入最终决策。' }}</p></article><article><span>机会</span><strong>{{ candidates.length ? `${candidates.length} 个候选` : '没有强机会' }}</strong><p>{{ textValue(result.candidate_blocked_reason || candidateVetoes[0]?.reason) || (candidates.length ? '候选仍需经过组合层确认。' : '当前没有明显优于保持现状的新机会。') }}</p></article></div>
        </SectionCard>

        <SectionCard title="持仓动作" description="最终建议中的持仓动作；没有动作时保持现状。" class="action-table-panel">
          <div v-if="holdingActions.length" class="table-wrap"><table class="analysis-table"><thead><tr><th>标的</th><th>当前仓位</th><th>建议</th><th>原因</th><th>条件</th></tr></thead><tbody><tr v-for="(row, index) in holdingActions" :key="row.code || index"><td><strong>{{ row.name || row.code || '组合' }}</strong><small>{{ row.code || '—' }}</small></td><td>{{ row.weight != null ? formatPercent(row.weight) : row.position || '—' }}</td><td><n-tag size="small" :bordered="false" :type="actionType(row.action)">{{ actionLabel(row.action) }}</n-tag></td><td>{{ textValue(row.reason) || '—' }}</td><td>{{ textValue(row.condition || row.trigger || row.entry_condition) || '—' }}</td></tr></tbody></table></div>
          <EmptyState v-else title="当前持仓均无需立即调整" description="没有需要在这个检查点执行的持仓动作。" />
        </SectionCard>

        <SectionCard title="候选机会" description="候选达到标准后，仍需通过 Portfolio Gate 才会成为最终行动。" class="candidate-panel">
          <div v-if="candidates.length" class="candidate-list"><article v-for="(row, index) in candidates" :key="row.code || index" class="candidate-row"><div class="candidate-top"><div><strong>{{ row.name || row.code || '未命名标的' }}</strong><small>{{ row.code || '代码待匹配' }}</small></div><n-tag size="small" :bordered="false" :type="candidateStage(row) === 'ACTION' ? 'warning' : candidateStage(row) === 'READY' ? 'info' : 'default'">{{ candidateStage(row) === 'ACTION' ? '需要调整' : candidateStage(row) === 'READY' ? '准备' : '观察' }}</n-tag></div><p>{{ candidateReason(row) }}</p><div class="candidate-foot"><span>风险：{{ textValue(row.risk || row.risk_flags) || '—' }}</span><span>Portfolio Gate：{{ candidateGate(row) }}</span><n-button text type="primary" @click="openCandidate(row)">查看指标<ArrowRight :size="13" /></n-button></div></article></div>
          <EmptyState v-else title="当前没有明显的新机会" description="当前没有明显优于保持现状的机会。">
            <template #action><n-button secondary @click="activeTab = 'evidence'">查看数据质量</n-button></template>
          </EmptyState>
          <div v-if="candidateVetoes.length" class="candidate-veto"><div><strong>Candidate Veto</strong><StatusBadge status="VETO" label="组合层未批准" /></div><p>候选达到行动标准，但组合层未批准。</p><ul><li v-for="(veto, index) in candidateVetoes" :key="veto.code || index">{{ veto.code || '候选' }}：{{ textValue(veto.reason || veto.message) || '组合约束未通过' }}</li></ul></div>
        </SectionCard>

        <TechnicalDetails title="查看详细证据与量化指标" name="analysis-technical">
          <div class="advanced-list"><div v-for="([key, value]) in advancedEntries" :key="key"><span>{{ key }}</span><strong>{{ typeof value === 'object' ? JSON.stringify(value) : value }}</strong></div></div>
          <pre>{{ JSON.stringify({ result, market_snapshot: market, quality_gate: qualityGate }, null, 2) }}</pre>
        </TechnicalDetails>

        <n-tabs v-model:value="activeTab" type="line" class="analysis-tabs">
          <n-tab-pane name="summary" tab="结论摘要" />
          <n-tab-pane name="workflow" tab="完整分析流程">
            <section class="workflow-panel panel-card"><div class="workflow-heading"><div><p class="page-eyebrow">TECHNICAL EVIDENCE</p><h2>分析与辩论记录</h2></div><span class="muted">底层 Agent、运行时和详细论点按需查看</span></div><div class="flow-rail"><div>市场环境</div><div>持仓风险</div><div>候选机会</div><div>风控</div><div>组合决策</div></div><section class="workflow-stage"><div class="stage-number">01</div><div><h3>市场与质量门控</h3><p>{{ textValue(evidencePack.market_read || qualityGate.summary || market.market_read) || '已读取本次分析的市场证据。' }}</p></div></section><section class="workflow-stage"><div class="stage-number">02</div><div><h3>候选与持仓证据</h3><p>{{ textValue(result.candidate_blocked_reason || result.portfolio_reason) || '候选和持仓风险已进入组合层判断。' }}</p></div></section><section class="workflow-stage final-stage"><div class="stage-number">03</div><div><div class="stage-title"><h3>组合经理最终决策</h3><n-tag :bordered="false" :type="finalDecision === 'ACTION' ? 'warning' : ['BLOCKED', 'DATA_GAP'].includes(finalDecision) ? 'error' : 'info'">{{ finalDecision }}</n-tag></div><p>{{ finalSummary }}</p></div></section><TechnicalDetails title="查看底层分析证据" name="workflow-detail"><pre>{{ JSON.stringify(workflow, null, 2) }}</pre></TechnicalDetails></section>
          </n-tab-pane>
          <n-tab-pane name="report" tab="完整报告"><section class="panel-card markdown-panel"><article class="markdown-body" v-html="renderedMarkdown" /></section></n-tab-pane>
          <n-tab-pane name="evidence" tab="结构化证据"><section class="panel-card json-panel"><div class="evidence-grid"><div><h3>数据源</h3><ul><li v-for="item in evidencePack.source_chain || market.source_chain || []" :key="item">{{ item }}</li><li v-if="!(evidencePack.source_chain || market.source_chain || []).length">未记录</li></ul></div><div><h3>数据缺口</h3><ul><li v-for="item in market.errors || market.missing_fields || evidencePack.data_gaps || []" :key="item">{{ item }}</li><li v-if="!(market.errors || market.missing_fields || evidencePack.data_gaps || []).length">无阻断性缺口</li></ul></div><div><h3>风险警示</h3><ul><li v-for="item in result.risk_warnings || []" :key="textValue(item)">{{ textValue(item) }}</li><li v-if="!(result.risk_warnings || []).length">无</li></ul></div><div><h3>未解决论点</h3><ul><li v-for="item in result.unresolved_claims || []" :key="textValue(item)">{{ textValue(item) }}</li><li v-if="!(result.unresolved_claims || []).length">无</li></ul></div></div><n-collapse><n-collapse-item title="原始结构化 JSON" name="analysis-json"><pre>{{ JSON.stringify(detail.structured_result, null, 2) }}</pre></n-collapse-item></n-collapse></section></n-tab-pane>
          <n-tab-pane name="screenshot" tab="原始持仓截图"><section class="panel-card screenshot-panel"><img v-if="screenshotUrl" :src="screenshotUrl" alt="原始持仓截图" /><n-empty v-else description="该分析没有可用截图"><template #icon><Image :size="24" /></template></n-empty></section></n-tab-pane>
        </n-tabs>
      </template>
    </template>

    <n-drawer v-model:show="candidateDrawerOpen" width="min(460px, 100vw)" placement="right"><n-drawer-content title="候选指标" closable><div v-if="candidateDetail" class="candidate-detail"><h2>{{ candidateDetail.name || candidateDetail.code }}</h2><p class="muted">{{ candidateDetail.code || '—' }} · {{ candidateStage(candidateDetail) }}</p><div class="candidate-metric-grid"><div><span>Opportunity</span><strong>{{ candidateMetric(candidateDetail, ['opportunity_score', 'opportunity']) }}</strong></div><div><span>Entry</span><strong>{{ candidateMetric(candidateDetail, ['entry_score', 'entry']) }}</strong></div><div><span>R/R</span><strong>{{ candidateMetric(candidateDetail, ['risk_reward_ratio', 'rr']) }}</strong></div><div><span>Fit</span><strong>{{ candidateMetric(candidateDetail, ['portfolio_fit_score', 'fit']) }}</strong></div><div><span>Decision Edge</span><strong>{{ candidateMetric(candidateDetail, ['decision_edge', 'edge_vs_no_action']) }}</strong></div><div><span>Coverage</span><strong>{{ candidateMetric(candidateDetail, ['coverage'], true) }}</strong></div></div><p>{{ candidateReason(candidateDetail) }}</p><TechnicalDetails title="参数与来源"><pre>{{ JSON.stringify(candidateDetail, null, 2) }}</pre></TechnicalDetails></div></n-drawer-content></n-drawer>
  </section>
</template>

<style scoped>
.workbench-page { display: grid; gap: 18px; }.page-eyebrow { margin: 0 0 5px; color: var(--primary); font-size: 10px; font-weight: 800; letter-spacing: .08em; }.progress-panel, .failed-panel { display: grid; gap: 18px; padding: 22px; }.progress-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }.progress-title h2, .failed-panel h2 { margin: 0; font-size: 24px; }.progress-title p:not(.page-eyebrow), .failed-panel p { margin: 6px 0 0; color: var(--text-muted); }.progress-title > strong { color: var(--primary); font-size: 28px; }.progress-steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }.progress-steps > div { display: grid; gap: 7px; border-top: 3px solid var(--border); padding: 12px 8px 0; }.progress-steps > div.done { border-top-color: var(--negative); }.progress-steps > div.current { border-top-color: var(--primary); }.step-icon { display: grid; width: 26px; height: 26px; place-items: center; border-radius: 50%; background: var(--surface-muted); color: var(--text-muted); font-size: 12px; }.done .step-icon { background: color-mix(in srgb, var(--negative) 15%, transparent); color: var(--negative); }.current .step-icon { background: var(--primary-soft); color: var(--primary); }.progress-steps small { color: var(--text-muted); font-size: 11px; }.failed-panel { grid-template-columns: auto minmax(0, 1fr); align-items: start; border-left: 4px solid var(--danger); }.failed-panel > svg { color: var(--danger); }.failed-panel .technical-details { grid-column: 1 / -1; }.why-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0 22px; }.why-grid article { border-left: 2px solid var(--border-strong); padding-left: 12px; }.why-grid article > span { color: var(--text-muted); font-size: 12px; }.why-grid strong { display: block; margin-top: 6px; font-size: 15px; }.why-grid p { margin: 8px 0 0; color: var(--text-muted); line-height: 1.65; }.table-wrap { max-width: 100%; overflow-x: auto; }.analysis-table { width: 100%; min-width: 760px; border-collapse: collapse; }.analysis-table th { padding: 10px 9px; border-bottom: 1px solid var(--border-strong); color: var(--text-muted); font-size: 12px; font-weight: 600; text-align: left; }.analysis-table td { border-bottom: 1px solid var(--border); padding: 12px 9px; vertical-align: top; }.analysis-table td:first-child { display: grid; gap: 3px; }.analysis-table td:first-child small { color: var(--text-muted); font-size: 11px; }.candidate-list { display: grid; }.candidate-row { border-top: 1px solid var(--border); padding: 15px 0; }.candidate-row:first-child { border-top: 0; padding-top: 0; }.candidate-row:last-child { padding-bottom: 0; }.candidate-top, .candidate-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.candidate-top > div { display: grid; gap: 3px; }.candidate-top small { color: var(--text-muted); font-size: 11px; }.candidate-row p { margin: 9px 0; color: var(--text-muted); line-height: 1.6; }.candidate-foot { color: var(--text-muted); font-size: 12px; }.candidate-foot .n-button { margin-left: auto; }.candidate-veto { display: grid; gap: 7px; margin-top: 16px; border-left: 3px solid var(--warning); background: color-mix(in srgb, var(--warning) 8%, transparent); padding: 11px 13px; }.candidate-veto > div { display: flex; align-items: center; gap: 9px; }.candidate-veto p { margin: 0; }.candidate-veto ul { margin: 0; padding-left: 19px; color: var(--text-muted); font-size: 12px; }.advanced-list { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }.advanced-list div { display: grid; gap: 4px; border-left: 2px solid var(--border); padding-left: 9px; }.advanced-list span { color: var(--text-muted); font-size: 11px; }.advanced-list strong { overflow-wrap: anywhere; font-size: 12px; }.technical-details :deep(pre), .json-panel pre { max-height: 420px; overflow: auto; white-space: pre-wrap; word-break: break-word; }.analysis-tabs { margin-top: -3px; }.workflow-panel { display: grid; gap: 16px; padding: 20px; }.workflow-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }.workflow-heading h2 { margin: 0; font-size: 19px; }.flow-rail { display: grid; grid-template-columns: repeat(5, 1fr); border: 1px solid var(--border); background: var(--surface-muted); }.flow-rail div { padding: 10px 6px; color: var(--text-muted); font-size: 11px; text-align: center; }.workflow-stage { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; border-top: 1px solid var(--border); padding-top: 16px; }.stage-number { display: grid; width: 30px; height: 30px; place-items: center; background: var(--primary-soft); color: var(--primary); font-size: 11px; font-weight: 800; }.workflow-stage h3 { margin: 2px 0 7px; font-size: 15px; }.workflow-stage p { margin: 0; color: var(--text-muted); line-height: 1.6; }.final-stage .stage-number { background: var(--primary); color: white; }.stage-title { display: flex; align-items: center; justify-content: space-between; gap: 10px; }.markdown-panel { padding: 22px; }.json-panel { display: grid; gap: 16px; padding: 20px; }.evidence-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }.evidence-grid > div { border-left: 2px solid var(--border); padding-left: 11px; }.evidence-grid h3 { margin: 0 0 7px; font-size: 14px; }.evidence-grid ul { margin: 0; padding-left: 18px; color: var(--text-muted); line-height: 1.65; }.screenshot-panel { display: grid; min-height: 320px; place-items: center; padding: 18px; }.screenshot-panel img { max-width: 100%; max-height: 72dvh; object-fit: contain; }.candidate-detail { display: grid; gap: 16px; }.candidate-detail h2 { margin: 0; font-size: 22px; }.candidate-detail p { margin: 0; line-height: 1.65; }.candidate-metric-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }.candidate-metric-grid div { display: grid; gap: 4px; border-bottom: 1px solid var(--border); padding-bottom: 9px; }.candidate-metric-grid span { color: var(--text-muted); font-size: 11px; }.candidate-metric-grid strong { font-size: 17px; }
@media (max-width: 760px) { .why-grid { grid-template-columns: 1fr; gap: 16px; }.progress-steps { grid-template-columns: repeat(2, 1fr); }.advanced-list { grid-template-columns: repeat(2, 1fr); }.workflow-heading { align-items: flex-start; flex-direction: column; }.flow-rail { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 520px) { .progress-title { align-items: flex-start; flex-direction: column; }.progress-steps, .advanced-list, .evidence-grid { grid-template-columns: 1fr; }.candidate-top, .candidate-foot { align-items: flex-start; flex-direction: column; }.candidate-foot .n-button { margin-left: 0; }.failed-panel { grid-template-columns: 1fr; }.failed-panel .technical-details { grid-column: auto; } }
</style>
