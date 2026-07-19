<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, Bot, CalendarClock, Camera, Play, Plus, RefreshCw, ShieldAlert } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type { AnalysisMode, AnalysisRunSummary, ModelProfile, Portfolio, Schedule } from '../api/types'

const router = useRouter()
const message = useMessage()
const loading = ref(false)
const portfolios = ref<Portfolio[]>([])
const runs = ref<AnalysisRunSummary[]>([])
const profiles = ref<ModelProfile[]>([])
const schedules = ref<Schedule[]>([])
const createOpen = ref(false)
const newPortfolioName = ref('我的持仓')
const creating = ref(false)
const analysisOpen = ref(false)
const analysisPortfolio = ref<Portfolio | null>(null)
const analysisMode = ref<AnalysisMode>('deep')
const analysisCheckpoint = ref('10:00')
const analysisNotify = ref(true)
const startingAnalysis = ref(false)

const defaultPortfolio = computed(() => portfolios.value.find((item) => item.is_default) || portfolios.value[0])
const latestRun = computed(() => runs.value[0])
const modelReady = computed(() => ({
  vision: profiles.value.some((item) => item.purpose === 'vision' && item.is_default),
  analysis: profiles.value.some((item) => ['analysis', 'deep_analysis'].includes(item.purpose) && item.is_default),
}))
const readiness = computed(() => [
  { label: '持仓组合', ok: Boolean(defaultPortfolio.value), note: defaultPortfolio.value ? defaultPortfolio.value.name : '创建一个组合' },
  { label: '识图模型', ok: modelReady.value.vision, note: modelReady.value.vision ? '已配置默认模型' : '需要在设置中配置' },
  { label: '分析模型', ok: modelReady.value.analysis, note: modelReady.value.analysis ? '已配置默认模型' : '需要在设置中配置' },
  { label: '自动分析', ok: schedules.value.some((item) => item.enabled), note: schedules.value.some((item) => item.enabled) ? '计划已启用' : '可选配置' },
])

function fmt(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

async function load() {
  loading.value = true
  try {
    const [portfolioRows, runRows, profileRows, scheduleRows] = await Promise.all([
      api.listPortfolios(), api.listRuns(), api.listProfiles(), api.listSchedules(),
    ])
    portfolios.value = portfolioRows
    runs.value = runRows
    profiles.value = profileRows
    schedules.value = scheduleRows
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

async function createPortfolio() {
  if (!newPortfolioName.value.trim()) return
  creating.value = true
  try {
    await api.createPortfolio({ name: newPortfolioName.value.trim(), is_default: portfolios.value.length === 0 })
    createOpen.value = false
    message.success('组合已创建')
    await load()
  } catch (error) {
    message.error((error as Error).message)
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
  if (!modelReady.value.analysis) {
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
    await router.push({
      name: 'upload',
      query: { portfolio: portfolio.id, job: job.id, focus: 'analysis' },
    })
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    startingAnalysis.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page-stack">
    <div class="page-heading">
      <div>
        <p class="eyebrow">PORTFOLIO INTELLIGENCE</p>
        <h1>今日决策总览</h1>
        <p>从真实持仓出发，跟踪数据质量、历史建议与下一次自动分析。</p>
      </div>
      <div class="heading-actions">
        <n-button secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
        <n-button type="primary" @click="router.push({ name: 'upload' })"><template #icon><Camera :size="17" /></template>上传今日持仓</n-button>
      </div>
    </div>

    <div class="metric-grid">
      <section class="metric-card panel-card">
        <span>持仓组合</span><strong>{{ portfolios.length }}</strong><small>{{ defaultPortfolio?.name || '尚未创建' }}</small>
      </section>
      <section class="metric-card panel-card">
        <span>历史报告</span><strong>{{ runs.length }}</strong><small>最近：{{ fmt(latestRun?.created_at) }}</small>
      </section>
      <section class="metric-card panel-card">
        <span>最近结论</span><strong class="metric-word">{{ latestRun?.final_rating || '暂无' }}</strong><small>{{ latestRun?.data_quality_grade ? `质量 ${latestRun.data_quality_grade}` : '等待首次分析' }}</small>
      </section>
      <section class="metric-card panel-card">
        <span>下次自动分析</span><strong class="metric-word">{{ schedules.find(s => s.enabled)?.checkpoint || '未配置' }}</strong><small>{{ fmt(schedules.find(s => s.enabled)?.next_run_at) }}</small>
      </section>
    </div>

    <div class="dashboard-grid">
      <section class="panel-card main-decision">
        <div class="section-title"><div><h2>最近一次组合结论</h2><p>系统保存的结构化最终裁决</p></div><n-tag v-if="latestRun?.data_quality_grade" :bordered="false" type="info">质量 {{ latestRun.data_quality_grade }}</n-tag></div>
        <template v-if="latestRun">
          <div class="decision-rating">{{ latestRun.final_rating || 'watch_only' }}</div>
          <p class="decision-summary">{{ latestRun.summary || '该报告没有摘要。' }}</p>
          <div class="decision-meta"><span>现金目标：{{ latestRun.cash_target || '—' }}</span><span>置信度：{{ latestRun.confidence || '—' }}</span><span>{{ fmt(latestRun.created_at) }}</span></div>
          <n-button text type="primary" @click="router.push({ name: 'reports', query: { run: latestRun.id } })">查看完整报告 <ArrowRight :size="15" /></n-button>
        </template>
        <n-empty v-else description="确认持仓并完成首次分析后，这里会显示组合结论。">
          <template #extra><n-button type="primary" @click="router.push({ name: 'upload' })">开始上传</n-button></template>
        </n-empty>
      </section>

      <section class="panel-card readiness-panel">
        <div class="section-title"><div><h2>系统准备状态</h2><p>完成前 3 项即可开始分析</p></div><ShieldAlert :size="21" /></div>
        <div class="readiness-list">
          <button v-for="item in readiness" :key="item.label" type="button" :class="['readiness-item', { ready: item.ok }]" @click="!item.ok && router.push({ name: item.label === '持仓组合' ? 'dashboard' : 'settings' })">
            <span class="status-dot" /><div><strong>{{ item.label }}</strong><small>{{ item.note }}</small></div><span>{{ item.ok ? '就绪' : '待配置' }}</span>
          </button>
        </div>
      </section>
    </div>

    <section class="panel-card">
      <div class="section-title">
        <div><h2>持仓组合</h2><p>每个组合独立保存截图、快照和分析历史；已确认持仓可随时手动分析</p></div>
        <n-button secondary @click="createOpen = true"><template #icon><Plus :size="16" /></template>新建组合</n-button>
      </div>
      <div v-if="portfolios.length" class="portfolio-grid">
        <article v-for="portfolio in portfolios" :key="portfolio.id" class="portfolio-card">
          <div class="portfolio-icon"><Bot :size="20" /></div>
          <div><strong>{{ portfolio.name }}</strong><p>{{ portfolio.market }} · {{ portfolio.currency }}</p></div>
          <n-tag v-if="portfolio.is_default" size="small" :bordered="false" type="success">默认</n-tag>
          <div class="portfolio-time"><CalendarClock :size="14" />{{ fmt(portfolio.latest_snapshot_time) }}</div>
          <div class="portfolio-actions">
            <n-button secondary size="small" @click="router.push({ name: 'upload', query: { portfolio: portfolio.id } })"><template #icon><Camera :size="14" /></template>更新持仓</n-button>
            <n-button type="primary" size="small" :disabled="!portfolio.latest_snapshot_id" @click="openManualAnalysis(portfolio)"><template #icon><Play :size="14" /></template>手动分析</n-button>
          </div>
        </article>
      </div>
      <n-empty v-else description="先创建一个持仓组合。" />
    </section>

    <n-modal v-model:show="createOpen" preset="card" title="新建持仓组合" style="width: min(460px, 92vw)">
      <n-form label-placement="top">
        <n-form-item label="组合名称"><n-input v-model:value="newPortfolioName" placeholder="例如：主账户、ETF 账户" @keyup.enter="createPortfolio" /></n-form-item>
        <n-button type="primary" block :loading="creating" @click="createPortfolio">创建组合</n-button>
      </n-form>
    </n-modal>

    <n-modal v-model:show="analysisOpen" preset="card" title="手动分析最新持仓" style="width: min(480px, 92vw)">
      <n-alert type="info" :show-icon="false">
        将使用 <strong>{{ analysisPortfolio?.name }}</strong> 的最新确认快照 #{{ analysisPortfolio?.latest_snapshot_id }}，不会重新识图或修改持仓。
      </n-alert>
      <n-form label-placement="top" class="analysis-modal-form">
        <n-form-item label="分析模式">
          <n-radio-group v-model:value="analysisMode"><n-radio-button value="quick">快速</n-radio-button><n-radio-button value="deep">深度</n-radio-button></n-radio-group>
        </n-form-item>
        <n-form-item label="检查点"><n-select v-model:value="analysisCheckpoint" :options="['09:35','10:00','12:00','14:30'].map(v => ({ label: v, value: v }))" /></n-form-item>
        <n-form-item label="完成后发送通知"><n-switch v-model:value="analysisNotify" /></n-form-item>
        <n-button type="primary" block size="large" :loading="startingAnalysis" @click="startManualAnalysis"><template #icon><Play :size="17" /></template>开始分析</n-button>
      </n-form>
    </n-modal>
  </section>
</template>

<style scoped>
.page-stack { display: grid; gap: 18px; }
.page-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; }
.eyebrow { margin: 0 0 5px; color: var(--app-primary); font-size: 11px; font-weight: 900; letter-spacing: .13em; }
h1 { margin: 0; font-size: clamp(28px, 4vw, 42px); letter-spacing: -.035em; }
.page-heading p:not(.eyebrow), .section-title p { margin: 6px 0 0; color: var(--app-text-muted); }
.heading-actions { display: flex; gap: 9px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.metric-card { display: grid; gap: 7px; padding: 18px; }
.metric-card span { color: var(--app-text-muted); font-size: 12px; font-weight: 700; }
.metric-card strong { font-size: 32px; line-height: 1; }
.metric-card .metric-word { overflow: hidden; font-size: 24px; text-overflow: ellipsis; white-space: nowrap; }
.metric-card small { color: var(--app-text-muted); }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(310px, .8fr); gap: 16px; }
.main-decision, .readiness-panel, section.panel-card { padding: 20px; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
.section-title h2 { margin: 0; font-size: 17px; }
.section-title p { font-size: 12px; }
.decision-rating { display: inline-flex; border-radius: 9px; background: var(--app-primary-soft); padding: 8px 12px; color: var(--app-primary); font-size: 13px; font-weight: 900; text-transform: uppercase; }
.decision-summary { max-width: 850px; margin: 18px 0; font-size: 17px; line-height: 1.75; }
.decision-meta { display: flex; flex-wrap: wrap; gap: 12px 22px; margin-bottom: 18px; color: var(--app-text-muted); font-size: 12px; }
.readiness-list { display: grid; gap: 8px; }
.readiness-item { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; width: 100%; border: 1px solid var(--app-border-soft); border-radius: 10px; background: var(--app-surface); padding: 11px; color: inherit; text-align: left; cursor: pointer; }
.readiness-item div { display: grid; }
.readiness-item small, .readiness-item > span:last-child { color: var(--app-text-muted); font-size: 11px; }
.status-dot { width: 9px; height: 9px; border-radius: 50%; background: #f59e0b; box-shadow: 0 0 0 4px color-mix(in srgb, #f59e0b 18%, transparent); }
.readiness-item.ready .status-dot { background: #22c55e; box-shadow: 0 0 0 4px color-mix(in srgb, #22c55e 18%, transparent); }
.portfolio-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; }
.portfolio-card { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 11px; border: 1px solid var(--app-border-soft); border-radius: 12px; padding: 15px; }
.portfolio-icon { display: grid; width: 40px; height: 40px; place-items: center; border-radius: 10px; background: var(--app-primary-soft); color: var(--app-primary); }
.portfolio-card p { margin: 3px 0 0; color: var(--app-text-muted); font-size: 11px; }
.portfolio-time { grid-column: 2 / 4; display: flex; align-items: center; gap: 5px; color: var(--app-text-muted); font-size: 11px; }
.portfolio-actions { grid-column: 2 / 4; display: flex; flex-wrap: wrap; gap: 8px; }
.analysis-modal-form { margin-top: 16px; }
@media (max-width: 1050px) { .metric-grid { grid-template-columns: repeat(2, 1fr); } .dashboard-grid { grid-template-columns: 1fr; } .portfolio-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 650px) { .page-heading { align-items: start; flex-direction: column; } .heading-actions { width: 100%; } .heading-actions .n-button { flex: 1; } .metric-grid, .portfolio-grid { grid-template-columns: 1fr; } }
</style>
