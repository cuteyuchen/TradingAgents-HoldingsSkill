<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { RefreshCw, ShieldCheck } from 'lucide-vue-next'
import { api } from '../api'
import type { CampaignCoverage, CampaignIntegrity, EvaluationCoverage, EvaluationEpisode, EvaluationSummary, ForwardSummary, ObservationCampaign, PaperObservationStatus, Portfolio } from '../api/types'

const route = useRoute()
const router = useRouter()
const portfolios = ref<Portfolio[]>([])
const portfolioId = ref<number | null>(null)
const summary = ref<EvaluationSummary | null>(null)
const coverage = ref<EvaluationCoverage | null>(null)
const paper = ref<PaperObservationStatus | null>(null)
const episodes = ref<EvaluationEpisode[]>([])
const campaigns = ref<ObservationCampaign[]>([])
const campaignId = ref<string | null>(null)
const campaignCoverage = ref<CampaignCoverage | null>(null)
const campaignIntegrity = ref<CampaignIntegrity | null>(null)
const forward = ref<ForwardSummary | null>(null)
const loading = ref(false)
const error = ref('')

const selectedPortfolio = computed(() => portfolios.value.find(item => item.id === portfolioId.value))
const percent = (value?: number | null) => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const sample = (status?: string) => status === 'INSUFFICIENT_SAMPLE' ? '样本不足' : status === 'EARLY_EVIDENCE' ? '早期证据' : status === 'MATURE_SAMPLE' ? '成熟样本' : status || '—'

async function loadCampaign(id: string | null) {
  campaignId.value = id
  campaignCoverage.value = null; campaignIntegrity.value = null; forward.value = null
  if (!portfolioId.value || !id) return
  try {
    const [c, i, s] = await Promise.all([
      api.getCampaignCoverage(portfolioId.value, id),
      api.getCampaignIntegrity(portfolioId.value, id),
      api.getForwardSummary(portfolioId.value, id),
    ])
    campaignCoverage.value = c; campaignIntegrity.value = i; forward.value = s
  } catch (err) { error.value = err instanceof Error ? err.message : '前瞻 Campaign 加载失败' }
}

async function load() {
  if (!portfolioId.value) return
  loading.value = true; error.value = ''
  try {
    const [s, c, p, e, cs] = await Promise.all([
      api.getEvaluationSummary(portfolioId.value),
      api.getEvaluationCoverage(portfolioId.value),
      api.getPaperObservationStatus(portfolioId.value),
      api.listEvaluationEpisodes(portfolioId.value, 50),
      api.listObservationCampaigns(portfolioId.value),
    ])
    summary.value = s; coverage.value = c; paper.value = p; episodes.value = e; campaigns.value = cs
    await loadCampaign(cs.find(item => item.campaign_id === campaignId.value)?.campaign_id || cs[0]?.campaign_id || null)
  } catch (err) { error.value = err instanceof Error ? err.message : '评估数据加载失败' }
  finally { loading.value = false }
}

async function init() {
  portfolios.value = await api.listPortfolios()
  const requested = Number(route.query.portfolio)
  portfolioId.value = portfolios.value.find(item => item.id === requested)?.id || portfolios.value.find(item => item.is_default)?.id || portfolios.value[0]?.id || null
  await load()
}

function choose(id: number | null) { portfolioId.value = id; void router.replace({ name: 'evaluation', query: id ? { portfolio: id } : {} }); void load() }
onMounted(() => void init())
</script>

<template>
  <section class="evaluation-page">
    <div class="page-heading"><div><p class="eyebrow">DECISION EVALUATION</p><h1>决策评估</h1><p class="muted">只读证据面板：回放、前瞻结果与 Paper Observation 分开呈现。</p></div><div class="heading-actions"><n-select :value="portfolioId" :options="portfolios.map(item => ({ label: item.name, value: item.id }))" clearable placeholder="选择组合" class="portfolio-select" @update:value="choose"/><n-button quaternary circle aria-label="刷新" title="刷新" :loading="loading" @click="load"><template #icon><RefreshCw :size="17"/></template></n-button></div></div>
    <p v-if="error" class="error-copy">{{ error }}</p>
    <n-empty v-if="!selectedPortfolio" description="暂无组合" />
    <template v-else>
      <div class="status-strip"><div><span>证据状态</span><strong>{{ summary?.status || '—' }}</strong></div><div><span>Evaluation Schema</span><strong>{{ summary?.evaluation_schema_version || '1.0.0' }}</strong></div><div><span>Decision Contract</span><strong>{{ summary?.decision_contract_version || '2.4.0' }}</strong></div><div><span>Paper Observation</span><strong>{{ paper?.status || '—' }}</strong></div></div>
      <n-card v-if="campaigns.length" title="Forward Observation"><div class="campaign-toolbar"><n-select :value="campaignId" :options="campaigns.map(item => ({ label: `${item.status} · ${item.campaign_id}`, value: item.campaign_id }))" class="campaign-select" @update:value="loadCampaign"/><span class="source-label">source: FORWARD_ONLY</span></div><div v-if="forward" class="campaign-metrics"><div><span>Campaign</span><strong>{{ forward.campaign.status }}</strong></div><div><span>覆盖交易日</span><strong>{{ forward.unique_trading_days }}/{{ forward.campaign.expected_trading_days || '—' }}</strong></div><div><span>Episodes</span><strong>{{ forward.episodes }}</strong></div><div><span>T+20 完成</span><strong>{{ forward.completed_t20 }}</strong></div><div><span>NO_ACTION</span><strong>{{ forward.no_action_count }} · {{ percent(forward.no_action_rate) }}</strong></div><div><span>Integrity</span><strong>{{ campaignIntegrity?.status || '—' }}</strong></div></div></n-card>
      <div v-if="forward" class="content-grid"><n-card title="Forward Decision Distribution"><div v-for="(count, decision) in forward.decision_distribution" :key="decision" class="outcome-row"><span>{{ decision }}</span><strong>{{ count }}</strong><small>forward-only</small></div><n-empty v-if="!Object.keys(forward.decision_distribution).length" description="暂无前瞻 Decision" /></n-card><n-card title="Candidate Funnel"><div v-for="(count, stage) in forward.candidate_stage_distribution" :key="stage" class="outcome-row"><span>{{ stage }}</span><strong>{{ count }}</strong><small>冻结时阶段</small></div><div v-if="campaignIntegrity?.audits?.length" class="integrity-line"><ShieldCheck :size="14"/> 完整性失败 {{ campaignIntegrity.audits.filter(item => item.status !== 'PASS').length }} 条</div></n-card></div>
      <div v-if="forward" class="content-grid"><n-card title="Forward Outcomes"><div v-for="(metric, horizon) in forward.horizons" :key="horizon" class="outcome-row"><span>T+{{ horizon }}</span><strong>{{ percent(metric.median) }}</strong><small>N={{ metric.n }} · {{ sample(metric.status) }}</small></div></n-card><n-card title="Trigger Effectiveness"><div v-for="(value, key) in forward.trigger_effectiveness || {}" :key="key" class="outcome-row"><span>{{ key }}</span><strong>{{ value }}</strong><small>descriptive only</small></div><div class="integrity-line">样本成熟度：{{ forward.sample_maturity || '—' }}</div></n-card></div>
      <n-card v-if="campaignCoverage" title="Observation Coverage"><div v-for="day in campaignCoverage.days.slice(-10).reverse()" :key="day.trading_date" class="episode-row"><div><strong>{{ day.trading_date }}</strong><small>{{ day.missing_reasons.join(' · ') || '证据链完整' }}</small></div><n-tag size="small" :type="day.status === 'COMPLETE' ? 'success' : day.status === 'BLOCKED' ? 'error' : 'warning'" :bordered="false">{{ day.status }}</n-tag></div><n-empty v-if="!campaignCoverage.days.length" description="尚无收盘覆盖记录" /></n-card>
      <div class="metric-grid"><n-card><span>Episodes</span><strong>{{ summary?.episodes ?? 0 }}</strong><small>NO_ACTION {{ summary ? percent(summary.no_action_rate) : '—' }}</small></n-card><n-card><span>Outcome 覆盖</span><strong>{{ coverage?.outcomes_complete ?? 0 }}/{{ coverage?.outcomes ?? 0 }}</strong><small>{{ coverage?.status || '—' }}</small></n-card><n-card><span>Trigger</span><strong>{{ summary?.trigger_count ?? 0 }}</strong><small>仅评估重分析效果</small></n-card><n-card><span>Paper</span><strong>{{ coverage?.paper_observations_captured ?? 0 }}</strong><small>缺失 {{ coverage?.paper_observations_missing ?? 0 }}</small></n-card></div>
      <div class="content-grid"><n-card title="Forward Outcome"><div v-for="(metric, horizon) in summary?.horizons || {}" :key="horizon" class="outcome-row"><span>T+{{ horizon }}</span><strong>{{ percent(metric.median) }}</strong><small>N={{ metric.n }} · {{ sample(metric.status) }}</small></div></n-card><n-card title="Risk Outcome"><div class="risk-row"><span>MFE</span><strong>{{ percent(summary?.mfe.median) }}</strong><small>N={{ summary?.mfe.n }} · {{ sample(summary?.mfe.status) }}</small></div><div class="risk-row"><span>MAE</span><strong>{{ percent(summary?.mae.median) }}</strong><small>N={{ summary?.mae.n }} · {{ sample(summary?.mae.status) }}</small></div><div class="risk-row"><span>最大回撤</span><strong>{{ percent(summary?.drawdown.median) }}</strong><small>N={{ summary?.drawdown.n }} · {{ sample(summary?.drawdown.status) }}</small></div></n-card></div>
      <n-card title="Recent Episodes"><div v-if="episodes.length" class="episode-list"><article v-for="episode in episodes" :key="episode.episode_id" class="episode-row"><div><strong>{{ episode.symbol }}</strong><small>{{ episode.trading_date }} · {{ episode.decision_type }}</small></div><div><n-tag size="small" :bordered="false">{{ episode.evidence_status }}</n-tag><small>{{ episode.source_mode }}</small></div></article></div><n-empty v-else description="尚无冻结 Episode" /></n-card>
      <p class="footnote"><ShieldCheck :size="14"/> 评估追加证据，不修改原始 Decision；当前页面不代表稳定盈利结论。</p>
    </template>
  </section>
</template>

<style scoped>
.evaluation-page { display: grid; gap: 18px; }.page-heading { display: flex; justify-content: space-between; gap: 18px; align-items: end; }.eyebrow { margin: 0 0 5px; color: var(--app-primary); font-size: 11px; font-weight: 800; letter-spacing: .12em; }.page-heading h1 { margin: 0; font-size: 30px; }.muted, .footnote, small { color: var(--app-text-muted); }.heading-actions { display: flex; gap: 8px; align-items: center; }.portfolio-select, .campaign-select { width: 210px; }.status-strip, .metric-grid, .content-grid, .campaign-metrics { display: grid; gap: 10px; }.status-strip { grid-template-columns: repeat(4, 1fr); }.status-strip > div { padding: 13px 15px; border: 1px solid var(--app-border); background: var(--app-surface); }.status-strip span, .status-strip strong, .metric-grid span, .metric-grid strong, .metric-grid small, .campaign-metrics span, .campaign-metrics strong { display: block; }.status-strip span, .metric-grid span, .campaign-metrics span { color: var(--app-text-muted); font-size: 12px; }.status-strip strong { margin-top: 4px; font-size: 14px; }.metric-grid { grid-template-columns: repeat(4, 1fr); }.metric-grid n-card { display: grid; gap: 6px; }.metric-grid strong { font-size: 28px; }.content-grid { grid-template-columns: 1fr 1fr; }.campaign-toolbar { display: flex; justify-content: space-between; gap: 10px; align-items: center; }.campaign-metrics { grid-template-columns: repeat(6, 1fr); margin-top: 14px; }.campaign-metrics > div { padding: 10px; border: 1px solid var(--app-border-soft); }.source-label, .integrity-line { color: var(--app-text-muted); font-size: 12px; }.outcome-row, .risk-row, .episode-row { display: grid; grid-template-columns: 60px 1fr auto; gap: 10px; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--app-border-soft); }.outcome-row:last-child, .risk-row:last-child, .episode-row:last-child { border-bottom: 0; }.outcome-row strong, .risk-row strong { text-align: right; }.episode-row { grid-template-columns: 1fr auto; }.episode-row div { display: grid; gap: 3px; }.integrity-line { display: flex; gap: 6px; align-items: center; margin-top: 10px; }.footnote { display: flex; gap: 6px; align-items: center; margin: 0; font-size: 12px; }@media (max-width: 900px) { .campaign-metrics { grid-template-columns: repeat(3, 1fr); }}@media (max-width: 760px) { .page-heading { align-items: start; flex-direction: column; }.heading-actions, .portfolio-select, .campaign-select { width: 100%; }.campaign-toolbar { align-items: stretch; flex-direction: column; }.status-strip, .metric-grid, .content-grid { grid-template-columns: 1fr 1fr; }}@media (max-width: 520px) { .status-strip, .metric-grid, .content-grid, .campaign-metrics { grid-template-columns: 1fr; }.outcome-row, .risk-row { grid-template-columns: 55px 1fr auto; }}
</style>
