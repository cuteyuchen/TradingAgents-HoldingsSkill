<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
import {
  Activity,
  BarChart3,
  CheckCircle2,
  ClipboardCheck,
  GitCompareArrows,
  Image,
  RefreshCw,
  Scale,
  SearchCheck,
  ShieldCheck,
  Target,
  TrendingUp,
  Users,
} from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type { AnalysisRunDetail, AnalysisRunSummary, Portfolio } from '../api/types'

type AnyRecord = Record<string, any>

const route = useRoute()
const router = useRouter()
const message = useMessage()
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const portfolios = ref<Portfolio[]>([])
const portfolioId = ref<number | null>(null)
const runs = ref<AnalysisRunSummary[]>([])
const selectedId = ref<number | null>(null)
const detail = ref<AnalysisRunDetail | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const screenshotUrl = ref('')
const comparison = ref<Record<string, any> | null>(null)
const comparisonOpen = ref(false)

const rendered = computed(() => markdown.render(detail.value?.markdown || ''))
const structured = computed<AnyRecord>(() => detail.value?.structured_result || {})
const result = computed<AnyRecord>(() => structured.value.result || {})
const workflow = computed<AnyRecord>(() => structured.value.workflow || structured.value.analysis_workflow || {})
const holdings = computed<any[]>(() => Array.isArray(result.value.holdings) ? result.value.holdings : [])
const market = computed<AnyRecord>(() => structured.value.market_snapshot || {})

function section(...keys: string[]): any {
  for (const source of [result.value, workflow.value, structured.value]) {
    for (const key of keys) {
      if (source?.[key] !== undefined && source[key] !== null) return source[key]
    }
  }
  return null
}

const evidencePack = computed(() => section('evidence_pack', 'evidence'))
const qualityGate = computed(() => section('quality_gate'))
const analystReports = computed(() => namedItems(
  section('analyst_reports', 'analyst_evidence', 'holding_evidence')
    || evidencePack.value?.analyst_reports
    || evidencePack.value?.holding_evidence,
))
const investmentDebate = computed(() => section('investment_debate_state', 'investment_debate', 'bull_bear_debate') || {})
const researchVerdict = computed(() => section('research_manager_verdict', 'research_verdict', 'manager_verdict'))
const traderProposal = computed(() => section('trader_proposal'))
const riskDebate = computed(() => section('risk_debate_state', 'risk_debate', 'three_way_risk_debate') || {})
const riskRevision = computed(() => section('risk_revision', 'risk_revision_loop'))
const portfolioFinal = computed(() => section('portfolio_final', 'portfolio_manager_final'))
const candidates = computed<any[]>(() => {
  const value = section('candidates', 'buy_candidates', 'rotation_candidates')
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.items)) return value.items
  return []
})
const candidateBlockReason = computed(() => {
  const value = section('candidate_blocked_reason', 'candidate_block_reason', 'buy_block_reason')
    || (section('candidates', 'buy_candidates') as AnyRecord)?.blocked_reason
  return textValue(value) || '当前没有通过质量与风险门控的买入或轮动候选。'
})

const investmentClaims = computed<AnyRecord[]>(() => {
  const claims = collectClaims(investmentDebate.value)
  if (claims.length) return claims
  return [
    ...listValue(result.value.bull_case).map((claim, index) => ({ claim_id: `INV-B${index + 1}`, speaker: 'bull', claim })),
    ...listValue(result.value.bear_case).map((claim, index) => ({ claim_id: `INV-S${index + 1}`, speaker: 'bear', claim })),
  ]
})
const riskClaims = computed(() => collectClaims(riskDebate.value))
const traderActions = computed(() => {
  const value = traderProposal.value
  if (Array.isArray(value)) return value
  for (const key of ['orders', 'actions', 'holdings', 'proposals', 'plan']) {
    if (Array.isArray(value?.[key])) return value[key]
  }
  return []
})
const completedStages = computed(() => [
  hasContent(evidencePack.value) || hasContent(qualityGate.value),
  analystReports.value,
  investmentClaims.value,
  researchVerdict.value,
  traderProposal.value,
  riskClaims.value,
  riskRevision.value,
  portfolioFinal.value,
].filter(hasContent).length)

const labelMap: Record<string, string> = {
  source: '持仓来源', intent: '分析意图', timestamp: '分析时间', time: '分析时间', checkpoint: '检查点',
  code_assumptions: '代码假设', data_quality_grade: '数据质量', quality_grade: '综合评级', data_gaps: '数据缺口',
  hard_checks: '硬检查', llm_review: '模型复审', missing_fields: '缺失字段', summary: '摘要', conclusion: '结论',
  verdict: '裁决', rating: '评级', winner: '胜出方', rationale: '裁决依据', strategic_action: '战略行动',
  confidence: '置信度', final_rating: '组合评级', cash_target: '现金目标', risk_decision: '风控裁决',
  hard_constraints: '硬性约束', soft_constraints: '建议约束', derisk_triggers: '去风险触发器',
  execution_prerequisites: '执行前提', reason: '原因', revision_reason: '修正原因', status: '状态',
  unresolved_claims: '未解决论点', checkpoint_rule: '检查点规则', manager_verdict: '管理人裁决',
}

const speakerLabels: Record<string, string> = {
  bull: '多头', bear: '空头', aggressive: '激进', neutral: '中立', conservative: '保守',
}

const actionLabels: Record<string, string> = {
  add: '加仓', hold: '持有', reduce: '减仓', sell: '卖出', watch: '观察', watch_only: '仅观察',
  rotate: '轮动', new_position: '新开仓', add_existing: '加仓现有持仓', rotation_watch: '轮动观察', conditional_add: '条件加仓', conditional_buy: '条件买入',
}

function fmt(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

function hasContent(value: any): boolean {
  if (value === null || value === undefined || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'object') return Object.keys(value).length > 0
  return true
}

function listValue(value: any): any[] {
  if (!hasContent(value)) return []
  return Array.isArray(value) ? value : [value]
}

function namedItems(value: any): AnyRecord[] {
  if (Array.isArray(value)) return value.map((item, index) => typeof item === 'object' ? item : { title: `分析师 ${index + 1}`, summary: item })
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([key, item]) => typeof item === 'object' && item !== null
      ? { __key: key, ...item as AnyRecord }
      : { __key: key, summary: item })
  }
  return []
}

function collectClaims(value: any): AnyRecord[] {
  if (!value) return []
  if (Array.isArray(value)) return value.flatMap((item) => collectClaims(item))
  if (Array.isArray(value.claims)) return value.claims
  const groupedClaims = [
    ...listValue(value.bull_claims),
    ...listValue(value.bear_claims),
    ...listValue(value.aggressive_claims),
    ...listValue(value.neutral_claims),
    ...listValue(value.conservative_claims),
  ]
  if (groupedClaims.length) return groupedClaims.flatMap((item) => collectClaims(item).length ? collectClaims(item) : [item])
  if (Array.isArray(value.rounds)) {
    return value.rounds.flatMap((round: any, index: number) => {
      const claims = Array.isArray(round) ? round : (round.claims || round.responses || [])
      return listValue(claims).map((claim) => typeof claim === 'object' ? { __round: round.round || round.name || index + 1, ...claim } : { __round: index + 1, claim })
    })
  }
  return []
}

function sectionEntries(value: any): Array<[string, any]> {
  if (!hasContent(value)) return []
  if (typeof value !== 'object' || Array.isArray(value)) return [['summary', value]]
  const ignored = new Set([
    'claims', 'rounds', 'orders', 'actions', 'holdings', 'proposals', 'plan', 'items',
    'bull_claims', 'bear_claims', 'aggressive_claims', 'neutral_claims', 'conservative_claims',
  ])
  return Object.entries(value).filter(([key, item]) => !ignored.has(key) && hasContent(item))
}

function labelFor(key: string) {
  return labelMap[key] || key.replaceAll('_', ' ')
}

function textValue(value: any): string {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    return value.map((item) => typeof item === 'object' ? textValue(item.claim || item.summary || item.name || item) : String(item)).filter(Boolean).join('；')
  }
  if (typeof value === 'object') {
    const preferred = value.summary || value.conclusion || value.verdict || value.reason || value.content || value.report
    return preferred ? textValue(preferred) : JSON.stringify(value, null, 2)
  }
  return String(value)
}

function reportTitle(report: AnyRecord, index: number) {
  return report.role_label || report.analyst_name || report.title || report.role || report.__key || `分析师 ${index + 1}`
}

function reportBody(report: AnyRecord) {
  return textValue(report.summary || report.conclusion || report.analysis || report.report || report.content || report.view)
}

function reportEvidence(report: AnyRecord) {
  return listValue(report.evidence || report.key_evidence || report.findings).map(textValue).filter(Boolean)
}

function claimType(speaker: string) {
  return ['bear', 'conservative'].includes(speaker) ? 'error' : ['bull', 'aggressive'].includes(speaker) ? 'success' : 'info'
}

function actionType(action: string) {
  return ['sell', 'reduce'].includes(action) ? 'error' : ['add', 'new_position', 'add_existing', 'conditional_buy'].includes(action) ? 'success' : 'info'
}

function actionText(action: any) {
  const key = String(action || 'watch').toLowerCase()
  return actionLabels[key] || key
}

function field(row: AnyRecord, ...keys: string[]) {
  for (const key of keys) if (hasContent(row?.[key])) return row[key]
  return null
}

async function loadRuns() {
  loading.value = true
  try {
    runs.value = await api.listRuns(portfolioId.value || undefined)
    const requested = Number(route.query.run)
    const next = runs.value.find((item) => item.id === requested)?.id || selectedId.value || runs.value[0]?.id || null
    if (next) await selectRun(next)
    else detail.value = null
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function selectRun(id: number) {
  selectedId.value = id
  detailLoading.value = true
  if (screenshotUrl.value) {
    URL.revokeObjectURL(screenshotUrl.value)
    screenshotUrl.value = ''
  }
  try {
    detail.value = await api.getRun(id)
    await router.replace({ name: 'reports', query: { ...(portfolioId.value ? { portfolio: portfolioId.value } : {}), run: id } })
    const snapshot = await api.getSnapshot(detail.value.portfolio_snapshot_id)
    if (snapshot.upload_id) {
      const blob = await api.getUploadImage(snapshot.upload_id)
      screenshotUrl.value = URL.createObjectURL(blob)
    }
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    detailLoading.value = false
  }
}

async function showComparison() {
  if (!selectedId.value) return
  try {
    comparison.value = await api.compareRun(selectedId.value)
    comparisonOpen.value = true
  } catch (e) {
    message.error((e as Error).message)
  }
}

onMounted(async () => {
  try {
    portfolios.value = await api.listPortfolios()
    const requestedPortfolio = Number(route.query.portfolio)
    portfolioId.value = portfolios.value.some(p => p.id === requestedPortfolio) ? requestedPortfolio : null
    await loadRuns()
  } catch (e) { message.error((e as Error).message) }
})
onUnmounted(() => { if (screenshotUrl.value) URL.revokeObjectURL(screenshotUrl.value) })
watch(portfolioId, () => void loadRuns())
</script>

<template>
  <section class="page-stack">
    <div class="page-heading">
      <div><p class="eyebrow">DECISION HISTORY</p><h1>分析报告</h1><p>查看完整分析流程、组合决策与可执行候选。</p></div>
      <div class="heading-actions">
        <n-select v-model:value="portfolioId" clearable placeholder="全部组合" :options="portfolios.map(p => ({ label: p.name, value: p.id }))" class="portfolio-filter" />
        <n-button secondary :loading="loading" @click="loadRuns"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
      </div>
    </div>

    <div class="report-layout">
      <aside class="panel-card report-list">
        <div class="list-head"><strong>历史记录</strong><span>{{ runs.length }} 条</span></div>
        <n-empty v-if="!runs.length && !loading" description="暂无分析报告" />
        <button v-for="run in runs" :key="run.id" type="button" :class="['run-item', { active: run.id === selectedId }]" @click="selectRun(run.id)">
          <div><strong>{{ actionText(run.final_rating) }}</strong><n-tag size="tiny" :bordered="false" type="info">{{ run.data_quality_grade || '-' }}</n-tag></div>
          <p>{{ run.summary || '暂无摘要' }}</p>
          <span>{{ fmt(run.created_at) }}</span>
        </button>
      </aside>

      <main class="report-detail">
        <section v-if="detailLoading" class="panel-card loading-report"><n-skeleton text :repeat="9" /></section>
        <n-empty v-else-if="!detail" class="panel-card empty-report" description="选择一份报告查看详情" />
        <template v-else>
          <section class="panel-card decision-hero">
            <div class="verdict-copy">
              <p class="eyebrow">PORTFOLIO VERDICT</p>
              <div class="verdict-line"><h2>{{ actionText(detail.final_rating) }}</h2><n-tag :bordered="false" :type="actionType(detail.final_rating || '')">{{ detail.final_rating || 'watch_only' }}</n-tag></div>
              <p>{{ detail.summary || '暂无摘要' }}</p>
            </div>
            <div class="hero-stats">
              <div><span>数据质量</span><strong>{{ detail.data_quality_grade || '—' }}</strong></div>
              <div><span>现金目标</span><strong>{{ detail.cash_target || '—' }}</strong></div>
              <div><span>置信度</span><strong>{{ detail.confidence || '—' }}</strong></div>
            </div>
            <div class="hero-actions"><span>{{ fmt(detail.created_at) }}</span><n-button secondary @click="showComparison"><template #icon><GitCompareArrows :size="16" /></template>与上次比较</n-button></div>
          </section>

          <section class="panel-card action-table-panel">
            <div class="section-title"><div><p class="section-kicker">HOLDINGS PLAN</p><h2>今日持仓操作</h2><p>卖出数量严格受确认快照的可用数量约束</p></div><ClipboardCheck :size="21" /></div>
            <div class="table-wrap">
              <table class="action-table">
                <thead><tr><th>标的</th><th>操作</th><th>触发条件</th><th>数量</th><th>最大可卖</th><th>原因</th><th>风险 / 失效</th></tr></thead>
                <tbody>
                  <tr v-for="row in holdings" :key="row.code">
                    <td><strong>{{ row.name || row.code }}</strong><span>{{ row.code }}</span></td>
                    <td><n-tag :bordered="false" :type="actionType(row.action)">{{ actionText(row.action) }}</n-tag></td>
                    <td>{{ row.trigger || '—' }}</td><td>{{ row.quantity || '—' }}</td><td>{{ row.max_sellable_qty ?? '—' }}</td>
                    <td class="long-cell">{{ row.reason || '—' }}</td><td class="long-cell">{{ row.risk || row.stop_loss || '—' }}</td>
                  </tr>
                  <tr v-if="!holdings.length"><td colspan="7" class="empty-cell">本次报告未返回持仓动作</td></tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="panel-card candidate-panel">
            <div class="section-title">
              <div><p class="section-kicker">BUY / ROTATION</p><h2>今日买入与轮动候选</h2><p>候选同时核验消息催化、资金面、板块位置与组合约束</p></div>
              <TrendingUp :size="21" />
            </div>
            <div v-if="candidates.length" class="candidate-list">
              <article v-for="(row, index) in candidates" :key="row.code || index" class="candidate-row">
                <div class="candidate-identity">
                  <span class="candidate-index">{{ String(index + 1).padStart(2, '0') }}</span>
                  <div><strong>{{ row.name || row.code || `候选 ${index + 1}` }}</strong><span>{{ row.code || '代码待确认' }}</span></div>
                  <n-tag :bordered="false" :type="actionType(field(row, 'action', 'type', 'candidate_type'))">{{ actionText(field(row, 'action', 'type', 'candidate_type')) }}</n-tag>
                  <strong v-if="field(row, 'score', 'total_score')" class="candidate-score">{{ field(row, 'score', 'total_score') }}<small>/10</small></strong>
                </div>
                <p class="candidate-reason">{{ field(row, 'reason', 'recommendation_reason', 'thesis') || '—' }}</p>
                <div class="candidate-evidence">
                  <div><span>消息 / 催化</span><p>{{ field(row, 'news_catalyst', 'catalyst', 'news') || field(row.reason_detail || {}, 'catalyst') || '—' }}</p></div>
                  <div><span>资金面</span><p>{{ field(row, 'capital_flow', 'fund_flow', 'money_flow') || field(row.reason_detail || {}, 'capital_flow') || '—' }}</p></div>
                  <div><span>板块位置</span><p>{{ field(row, 'sector_position', 'sector_stage', 'rotation_stage') || field(row.reason_detail || {}, 'sector_position') || '—' }}</p></div>
                </div>
                <div class="candidate-plan">
                  <div><span>入场条件</span><strong>{{ field(row, 'trigger', 'entry_condition', 'entry_trigger') || '—' }}</strong></div>
                  <div><span>初始仓位</span><strong>{{ field(row, 'initial_size', 'position', 'size') || '—' }}</strong></div>
                  <div><span>止盈</span><strong>{{ [field(row, 'take_profit_1', 'take_profit1', 'take_profit'), field(row, 'take_profit_2', 'take_profit2')].filter(Boolean).join(' / ') || '—' }}</strong></div>
                  <div><span>止损 / 取消</span><strong>{{ [field(row, 'stop_loss'), field(row, 'cancel_condition', 'invalidation')].filter(Boolean).join(' / ') || '—' }}</strong></div>
                </div>
              </article>
            </div>
            <n-alert v-else type="info" :show-icon="false">{{ candidateBlockReason }}</n-alert>
          </section>

          <n-tabs type="line" animated class="report-tabs">
            <n-tab-pane name="workflow" tab="完整分析流程">
              <section class="panel-card workflow-panel">
                <div class="workflow-heading">
                  <div><p class="section-kicker">MULTI-AGENT WORKFLOW</p><h2>分析与辩论记录</h2></div>
                  <span>{{ completedStages }} / 8 分析阶段有记录</span>
                </div>
                <div class="flow-rail" aria-label="分析流程">
                  <div><SearchCheck :size="17" /><span>证据 / 质检</span></div><div><Users :size="17" /><span>分析师</span></div>
                  <div><Scale :size="17" /><span>多空辩论</span></div><div><CheckCircle2 :size="17" /><span>研究裁决</span></div>
                  <div><BarChart3 :size="17" /><span>交易方案</span></div><div><Activity :size="17" /><span>风控修正</span></div>
                  <div><ShieldCheck :size="17" /><span>风控辩论</span></div><div><Target :size="17" /><span>组合决策</span></div>
                </div>

                <section class="workflow-stage">
                  <div class="stage-number">01</div><div class="stage-content"><div class="stage-title"><h3>证据包与质量门控</h3><n-tag :bordered="false" type="info">{{ detail.data_quality_grade || '—' }}</n-tag></div>
                    <div class="split-details">
                      <div><h4>证据包</h4><dl v-if="sectionEntries(evidencePack).length" class="detail-list"><div v-for="([key, value]) in sectionEntries(evidencePack)" :key="key"><dt>{{ labelFor(key) }}</dt><dd>{{ textValue(value) }}</dd></div></dl><p v-else class="empty-copy">未返回结构化证据包</p></div>
                      <div><h4>质量门控</h4><dl v-if="sectionEntries(qualityGate).length" class="detail-list"><div v-for="([key, value]) in sectionEntries(qualityGate)" :key="key"><dt>{{ labelFor(key) }}</dt><dd>{{ textValue(value) }}</dd></div></dl><p v-else class="empty-copy">未返回结构化质量门控</p></div>
                    </div>
                  </div>
                </section>

                <section class="workflow-stage">
                  <div class="stage-number">02</div><div class="stage-content"><div class="stage-title"><h3>多维分析师报告</h3><span>{{ analystReports.length }} 份</span></div>
                    <div v-if="analystReports.length" class="analyst-list">
                      <article v-for="(report, index) in analystReports" :key="report.__key || index" class="analyst-item">
                        <div><strong>{{ reportTitle(report, index) }}</strong><n-tag v-if="report.grade || report.confidence" size="small" :bordered="false">{{ report.grade || report.confidence }}</n-tag></div>
                        <p>{{ reportBody(report) || '—' }}</p><ul v-if="reportEvidence(report).length"><li v-for="item in reportEvidence(report)" :key="item">{{ item }}</li></ul>
                      </article>
                    </div><p v-else class="empty-copy">未返回独立分析师报告</p>
                  </div>
                </section>

                <section class="workflow-stage">
                  <div class="stage-number">03</div><div class="stage-content"><div class="stage-title"><h3>多空观点辩论</h3><span>{{ investmentClaims.length }} 条 Claim</span></div>
                    <div v-if="investmentClaims.length" class="debate-wrap"><table class="debate-table"><thead><tr><th>Claim</th><th>轮次</th><th>立场</th><th>论点与证据</th><th>置信度</th><th>状态</th></tr></thead><tbody>
                      <tr v-for="(claim, index) in investmentClaims" :key="claim.claim_id || index"><td><code>{{ claim.claim_id || `INV-${index + 1}` }}</code></td><td>{{ claim.__round || claim.round || '—' }}</td><td><n-tag size="small" :bordered="false" :type="claimType(claim.speaker)">{{ speakerLabels[claim.speaker] || claim.speaker || '—' }}</n-tag></td><td><strong>{{ claim.claim || claim.argument || claim.content || '—' }}</strong><span v-if="hasContent(claim.evidence)">{{ textValue(claim.evidence) }}</span><small v-if="hasContent(claim.target_claim_ids)">回应 {{ textValue(claim.target_claim_ids) }}</small></td><td>{{ claim.confidence ?? '—' }}</td><td>{{ claim.status || '—' }}</td></tr>
                    </tbody></table></div><p v-else class="empty-copy">未返回多空 Claim 记录</p>
                    <dl v-if="sectionEntries(investmentDebate).length" class="stage-summary"><div v-for="([key, value]) in sectionEntries(investmentDebate)" :key="key"><dt>{{ labelFor(key) }}</dt><dd>{{ textValue(value) }}</dd></div></dl>
                  </div>
                </section>

                <section class="workflow-stage compact-stage">
                  <div class="stage-number">04</div><div class="stage-content"><div class="stage-title"><h3>研究总监裁决</h3></div><dl v-if="sectionEntries(researchVerdict).length" class="stage-summary"><div v-for="([key, value]) in sectionEntries(researchVerdict)" :key="key"><dt>{{ labelFor(key) }}</dt><dd>{{ textValue(value) }}</dd></div></dl><p v-else class="empty-copy">未返回研究总监裁决</p></div>
                </section>

                <section class="workflow-stage">
                  <div class="stage-number">05</div><div class="stage-content"><div class="stage-title"><h3>交易员方案</h3><span>{{ traderActions.length }} 项动作</span></div>
                    <div v-if="traderActions.length" class="debate-wrap"><table class="debate-table action-proposal-table"><thead><tr><th>标的</th><th>动作</th><th>触发</th><th>数量 / 仓位</th><th>止盈</th><th>止损 / 失效</th></tr></thead><tbody><tr v-for="(row, index) in traderActions" :key="row.code || index"><td><strong>{{ row.name || row.code || '组合' }}</strong><span>{{ row.code }}</span></td><td><n-tag size="small" :bordered="false" :type="actionType(row.action)">{{ actionText(row.action) }}</n-tag></td><td>{{ field(row, 'trigger', 'entry_condition') || '—' }}</td><td>{{ field(row, 'quantity', 'position', 'size') || '—' }}</td><td>{{ field(row, 'take_profit', 'take_profit_1') || '—' }}</td><td>{{ field(row, 'stop_loss', 'invalidation', 'risk') || '—' }}</td></tr></tbody></table></div>
                    <dl v-if="sectionEntries(traderProposal).length" class="stage-summary"><div v-for="([key, value]) in sectionEntries(traderProposal)" :key="key"><dt>{{ labelFor(key) }}</dt><dd>{{ textValue(value) }}</dd></div></dl><p v-if="!traderActions.length && !sectionEntries(traderProposal).length" class="empty-copy">未返回交易员方案</p>
                  </div>
                </section>

                <section class="workflow-stage compact-stage">
                  <div class="stage-number">06</div><div class="stage-content"><div class="stage-title"><h3>风控修正</h3></div><dl v-if="sectionEntries(riskRevision).length" class="stage-summary"><div v-for="([key, value]) in sectionEntries(riskRevision)" :key="key"><dt>{{ labelFor(key) }}</dt><dd>{{ textValue(value) }}</dd></div></dl><p v-else class="empty-copy">本次没有独立风控修正记录</p></div>
                </section>

                <section class="workflow-stage">
                  <div class="stage-number">07</div><div class="stage-content"><div class="stage-title"><h3>三方风控辩论</h3><span>{{ riskClaims.length }} 条 Claim</span></div>
                    <div v-if="riskClaims.length" class="risk-claims"><article v-for="(claim, index) in riskClaims" :key="claim.claim_id || index"><div><code>{{ claim.claim_id || `RISK-${index + 1}` }}</code><n-tag size="small" :bordered="false" :type="claimType(claim.speaker)">{{ speakerLabels[claim.speaker] || claim.speaker || '风控' }}</n-tag><span>{{ claim.status || '—' }}</span></div><strong>{{ claim.claim || claim.argument || claim.content || '—' }}</strong><p v-if="hasContent(claim.evidence)">{{ textValue(claim.evidence) }}</p></article></div><p v-else class="empty-copy">未返回三方风控 Claim 记录</p>
                    <dl v-if="sectionEntries(riskDebate).length" class="stage-summary"><div v-for="([key, value]) in sectionEntries(riskDebate)" :key="key"><dt>{{ labelFor(key) }}</dt><dd>{{ textValue(value) }}</dd></div></dl>
                  </div>
                </section>

                <section class="workflow-stage compact-stage final-stage">
                  <div class="stage-number">08</div><div class="stage-content"><div class="stage-title"><h3>组合经理最终决策</h3><n-tag :bordered="false" :type="actionType(detail.final_rating || '')">{{ actionText(detail.final_rating) }}</n-tag></div><dl v-if="sectionEntries(portfolioFinal).length" class="stage-summary"><div v-for="([key, value]) in sectionEntries(portfolioFinal)" :key="key"><dt>{{ labelFor(key) }}</dt><dd>{{ textValue(value) }}</dd></div></dl><p v-else class="empty-copy">{{ detail.summary || '未返回独立组合经理决策' }}</p></div>
                </section>
              </section>
            </n-tab-pane>
            <n-tab-pane name="report" tab="完整报告">
              <section class="panel-card markdown-panel"><article class="markdown-body" v-html="rendered" /></section>
            </n-tab-pane>
            <n-tab-pane name="evidence" tab="结构化证据">
              <section class="panel-card json-panel">
                <div class="evidence-grid">
                  <div><h3>数据源</h3><ul><li v-for="item in market.source_chain || []" :key="item">{{ item }}</li><li v-if="!(market.source_chain || []).length">未记录</li></ul></div>
                  <div><h3>数据缺口</h3><ul><li v-for="item in market.errors || market.missing_fields || []" :key="item">{{ item }}</li><li v-if="!(market.errors || market.missing_fields || []).length">无阻断性缺口</li></ul></div>
                  <div><h3>未解决论点</h3><ul><li v-for="item in result.unresolved_claims || []" :key="textValue(item)">{{ textValue(item) }}</li><li v-if="!(result.unresolved_claims || []).length">无</li></ul></div>
                  <div><h3>风险警示</h3><ul><li v-for="item in result.risk_warnings || []" :key="textValue(item)">{{ textValue(item) }}</li><li v-if="!(result.risk_warnings || []).length">无</li></ul></div>
                </div>
                <n-collapse><n-collapse-item title="原始结构化 JSON" name="json"><pre>{{ JSON.stringify(detail.structured_result, null, 2) }}</pre></n-collapse-item></n-collapse>
              </section>
            </n-tab-pane>
            <n-tab-pane name="screenshot" tab="原始持仓截图">
              <section class="panel-card screenshot-panel"><img v-if="screenshotUrl" :src="screenshotUrl" alt="原始持仓截图" /><n-empty v-else description="该报告没有可用截图"><template #icon><Image /></template></n-empty></section>
            </n-tab-pane>
          </n-tabs>
        </template>
      </main>
    </div>

    <n-modal v-model:show="comparisonOpen" preset="card" title="与上次分析比较" style="width: 960px; max-width: calc(100vw - 32px)">
      <template v-if="comparison?.previous">
        <n-alert type="info" :show-icon="false">共 {{ comparison.changes?.length || 0 }} 个标的或建议字段发生变化。</n-alert>
        <div v-for="change in comparison.changes || []" :key="change.code" class="change-card">
          <strong>{{ change.code }}</strong>
          <div><span>上次</span><pre>{{ JSON.stringify(change.before, null, 2) }}</pre></div>
          <div><span>本次</span><pre>{{ JSON.stringify(change.after, null, 2) }}</pre></div>
        </div>
      </template>
      <n-empty v-else description="没有更早的报告可比较" />
    </n-modal>
  </section>
</template>

<style scoped>
.page-stack { display: grid; gap: 18px; }
.page-heading { display: flex; align-items: end; justify-content: space-between; gap: 16px; }
.eyebrow, .section-kicker { margin: 0 0 5px; color: var(--app-primary); font-size: 10px; font-weight: 900; letter-spacing: .12em; }
h1 { margin: 0; font-size: clamp(28px, 4vw, 40px); letter-spacing: 0; }
.page-heading p:not(.eyebrow), .section-title p { margin: 6px 0 0; color: var(--app-text-muted); }
.heading-actions { display: flex; gap: 8px; }.portfolio-filter { width: 210px; }
.report-layout { display: grid; grid-template-columns: 278px minmax(0, 1fr); gap: 16px; align-items: start; }
.panel-card { padding: 18px; }.report-list { position: sticky; top: 88px; display: grid; max-height: calc(100dvh - 112px); gap: 8px; overflow-y: auto; }
.list-head { display: flex; justify-content: space-between; margin-bottom: 7px; }.list-head span { color: var(--app-text-muted); }
.run-item { display: grid; gap: 7px; width: 100%; border: 1px solid var(--app-border-soft); border-radius: 8px; background: var(--app-surface); padding: 12px; color: inherit; text-align: left; cursor: pointer; }
.run-item:hover, .run-item.active { border-color: color-mix(in srgb, var(--app-primary) 48%, var(--app-border)); background: var(--app-primary-soft); }
.run-item > div { display: flex; align-items: center; justify-content: space-between; }.run-item p { display: -webkit-box; margin: 0; overflow: hidden; color: var(--app-text-muted); font-size: 12px; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }.run-item > span { color: var(--app-text-muted); font-size: 10px; }
.report-detail { display: grid; min-width: 0; gap: 14px; }.report-detail > *, .report-tabs, .report-tabs :deep(.n-tabs-pane-wrapper), .report-tabs :deep(.n-tab-pane) { min-width: 0; }.empty-report, .loading-report { min-height: 480px; }
.decision-hero { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; }.verdict-line { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }.decision-hero h2 { margin: 0; color: var(--app-primary); font-size: 27px; }.verdict-copy > p:last-child { max-width: 760px; margin: 0; font-size: 14px; line-height: 1.7; }
.hero-stats { display: grid; grid-template-columns: repeat(3, minmax(105px, 1fr)); gap: 8px; }.hero-stats div { display: grid; align-content: center; gap: 5px; border-left: 2px solid var(--app-border); padding: 7px 12px; }.hero-stats span { color: var(--app-text-muted); font-size: 10px; }.hero-stats strong { font-size: 14px; }
.hero-actions { grid-column: 1 / 3; display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--app-border-soft); padding-top: 12px; color: var(--app-text-muted); font-size: 11px; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }.section-title h2 { margin: 0; font-size: 17px; }.section-title > svg { color: var(--app-primary); }
.table-wrap, .debate-wrap { max-width: 100%; min-width: 0; overflow-x: auto; }.action-table, .debate-table { width: 100%; min-width: 980px; border-collapse: collapse; }.action-table th, .debate-table th { border-bottom: 1px solid var(--app-border); padding: 9px; color: var(--app-text-muted); font-size: 10px; text-align: left; }.action-table td, .debate-table td { border-bottom: 1px solid var(--app-border-soft); padding: 11px 9px; vertical-align: top; }.action-table td:first-child, .action-proposal-table td:first-child { display: grid; }.action-table td:first-child span, .action-proposal-table td:first-child span { color: var(--app-text-muted); font-size: 10px; }.long-cell { min-width: 210px; line-height: 1.55; }.empty-cell { color: var(--app-text-muted); text-align: center; }
.candidate-list { display: grid; }.candidate-row { display: grid; gap: 13px; border-top: 1px solid var(--app-border-soft); padding: 17px 0; }.candidate-row:first-child { border-top: 0; padding-top: 0; }.candidate-row:last-child { padding-bottom: 0; }.candidate-identity { display: flex; align-items: center; gap: 9px; }.candidate-index { display: grid; width: 30px; height: 30px; place-items: center; background: var(--app-primary-soft); color: var(--app-primary); font-weight: 800; }.candidate-identity > div { display: grid; min-width: 0; }.candidate-identity > div > span { color: var(--app-text-muted); font-size: 10px; }.candidate-score { margin-left: auto; color: var(--app-primary); font-size: 20px; }.candidate-score small { color: var(--app-text-muted); font-size: 10px; }.candidate-reason { margin: 0; line-height: 1.7; }.candidate-evidence, .candidate-plan { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }.candidate-evidence div { border-left: 2px solid var(--app-border); padding-left: 10px; }.candidate-evidence span, .candidate-plan span { color: var(--app-text-muted); font-size: 10px; }.candidate-evidence p { margin: 4px 0 0; line-height: 1.6; }.candidate-plan { grid-template-columns: repeat(4, 1fr); background: var(--app-surface-muted); padding: 11px 12px; }.candidate-plan div { display: grid; gap: 4px; }.candidate-plan strong { font-size: 12px; line-height: 1.5; }
.report-tabs { margin-top: 2px; }.workflow-heading { display: flex; align-items: end; justify-content: space-between; gap: 12px; }.workflow-heading h2 { margin: 0; font-size: 19px; }.workflow-heading > span { color: var(--app-text-muted); font-size: 11px; }.flow-rail { display: grid; grid-template-columns: repeat(8, 1fr); margin: 18px 0 4px; border: 1px solid var(--app-border-soft); background: var(--app-surface-muted); }.flow-rail div { position: relative; display: grid; min-width: 0; place-items: center; gap: 5px; padding: 10px 4px; color: var(--app-text-muted); font-size: 10px; }.flow-rail div:not(:last-child)::after { position: absolute; top: 50%; right: -5px; width: 9px; height: 9px; border-top: 1px solid var(--app-border); border-right: 1px solid var(--app-border); background: var(--app-surface-muted); content: ''; transform: translateY(-50%) rotate(45deg); }.flow-rail svg { color: var(--app-primary); }
.workflow-stage { display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 15px; border-top: 1px solid var(--app-border-soft); padding: 22px 0; }.workflow-stage:last-child { padding-bottom: 3px; }.stage-number { display: grid; width: 36px; height: 36px; place-items: center; background: var(--app-primary-soft); color: var(--app-primary); font-size: 11px; font-weight: 900; }.stage-content { min-width: 0; }.stage-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 13px; }.stage-title h3 { margin: 0; font-size: 16px; }.stage-title > span { color: var(--app-text-muted); font-size: 11px; }.split-details { display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; }.split-details h4 { margin: 0 0 9px; font-size: 12px; }.detail-list, .stage-summary { display: grid; margin: 0; }.detail-list > div, .stage-summary > div { display: grid; grid-template-columns: minmax(95px, .3fr) minmax(0, 1fr); gap: 10px; border-top: 1px solid var(--app-border-soft); padding: 9px 0; }.detail-list dt, .stage-summary dt { color: var(--app-text-muted); font-size: 11px; }.detail-list dd, .stage-summary dd { margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }.empty-copy { margin: 0; color: var(--app-text-muted); font-size: 12px; }
.analyst-list { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px 18px; }.analyst-item { border-top: 1px solid var(--app-border-soft); padding: 12px 0; }.analyst-item > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.analyst-item p { margin: 7px 0 0; line-height: 1.65; }.analyst-item ul { margin: 8px 0 0; padding-left: 17px; color: var(--app-text-muted); font-size: 11px; line-height: 1.6; }
.debate-table td:nth-child(4) { min-width: 330px; }.debate-table td:nth-child(4) { display: grid; gap: 5px; }.debate-table td:nth-child(4) span { color: var(--app-text-muted); font-size: 11px; }.debate-table td:nth-child(4) small { color: var(--app-primary); }.stage-summary { margin-top: 12px; border-top: 1px solid var(--app-border-soft); }.risk-claims { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }.risk-claims article { display: grid; align-content: start; gap: 8px; border-left: 2px solid var(--app-border); padding-left: 11px; }.risk-claims article > div { display: flex; align-items: center; gap: 7px; }.risk-claims article > div > span { margin-left: auto; color: var(--app-text-muted); font-size: 10px; }.risk-claims article p { margin: 0; color: var(--app-text-muted); font-size: 11px; line-height: 1.6; }.final-stage .stage-number { background: var(--app-primary); color: white; }
.markdown-panel { padding: 24px; }.json-panel { display: grid; gap: 16px; }.evidence-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px; }.evidence-grid div { border-left: 2px solid var(--app-border); padding-left: 12px; }.evidence-grid h3 { margin: 0 0 9px; font-size: 14px; }.evidence-grid ul { margin: 0; padding-left: 18px; color: var(--app-text-muted); line-height: 1.7; }
pre { overflow: auto; border-radius: 7px; background: color-mix(in srgb, var(--app-bg) 70%, black); padding: 13px; font-size: 11px; white-space: pre-wrap; }.screenshot-panel { display: grid; min-height: 420px; place-items: center; }.screenshot-panel img { max-width: 100%; max-height: 75dvh; object-fit: contain; }
.change-card { display: grid; grid-template-columns: 80px 1fr 1fr; gap: 10px; margin-top: 12px; border-top: 1px solid var(--app-border-soft); padding: 12px 0; }.change-card div > span { color: var(--app-text-muted); font-size: 11px; }
@media (max-width: 1100px) { .report-layout { grid-template-columns: 230px minmax(0, 1fr); }.decision-hero { grid-template-columns: 1fr; }.hero-actions { grid-column: auto; }.candidate-evidence, .candidate-plan { grid-template-columns: repeat(2, 1fr); }.flow-rail { grid-template-columns: repeat(4, 1fr); }.analyst-list, .risk-claims { grid-template-columns: 1fr; } }
@media (max-width: 860px) { .report-layout { grid-template-columns: 1fr; }.report-list { position: static; display: flex; max-height: none; overflow-x: auto; }.list-head { min-width: 90px; align-content: start; flex-direction: column; }.run-item { min-width: 245px; }.split-details { grid-template-columns: 1fr; }.change-card { grid-template-columns: 1fr; } }
@media (max-width: 650px) { .page-heading { align-items: start; flex-direction: column; }.heading-actions { width: 100%; }.portfolio-filter { flex: 1; width: auto; }.hero-stats { grid-template-columns: 1fr; }.hero-stats div { border-top: 1px solid var(--app-border-soft); border-left: 0; padding: 9px 0; }.candidate-evidence, .candidate-plan, .evidence-grid { grid-template-columns: 1fr; }.flow-rail { grid-template-columns: repeat(2, 1fr); }.workflow-stage { grid-template-columns: 1fr; }.stage-number { width: 30px; height: 30px; }.candidate-identity { flex-wrap: wrap; }.candidate-score { margin-left: 0; }.detail-list > div, .stage-summary > div { grid-template-columns: 1fr; gap: 3px; }.panel-card { padding: 15px; } }
</style>
