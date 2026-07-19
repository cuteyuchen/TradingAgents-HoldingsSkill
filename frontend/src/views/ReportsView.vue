<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
import { GitCompareArrows, Image, RefreshCw } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type { AnalysisRunDetail, AnalysisRunSummary, Portfolio } from '../api/types'

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
const result = computed(() => detail.value?.structured_result?.result || {})
const holdings = computed<any[]>(() => Array.isArray(result.value.holdings) ? result.value.holdings : [])
const market = computed(() => detail.value?.structured_result?.market_snapshot || {})

function fmt(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
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
      <div><p class="eyebrow">DECISION HISTORY</p><h1>分析报告</h1><p>查看每次持仓截图、市场证据、历史一致性与最终组合裁决。</p></div>
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
          <div><strong>{{ run.final_rating || 'watch_only' }}</strong><n-tag size="tiny" :bordered="false" type="info">{{ run.data_quality_grade || '-' }}</n-tag></div>
          <p>{{ run.summary || '暂无摘要' }}</p>
          <span>{{ fmt(run.created_at) }}</span>
        </button>
      </aside>

      <main class="report-detail">
        <section v-if="detailLoading" class="panel-card"><n-skeleton text :repeat="9" /></section>
        <n-empty v-else-if="!detail" class="panel-card empty-report" description="选择一份报告查看详情" />
        <template v-else>
          <section class="panel-card decision-hero">
            <div>
              <p class="eyebrow">PORTFOLIO VERDICT</p>
              <h2>{{ detail.final_rating || 'watch_only' }}</h2>
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
            <div class="section-title"><div><h2>今日持仓操作</h2><p>卖出数量上限来自确认快照的 available_qty</p></div></div>
            <div class="table-wrap">
              <table class="action-table">
                <thead><tr><th>标的</th><th>操作</th><th>触发条件</th><th>数量</th><th>最大可卖</th><th>原因</th><th>风险/失效</th></tr></thead>
                <tbody>
                  <tr v-for="row in holdings" :key="row.code">
                    <td><strong>{{ row.name || row.code }}</strong><span>{{ row.code }}</span></td>
                    <td><n-tag :bordered="false" :type="['sell','reduce'].includes(row.action) ? 'error' : row.action === 'add' ? 'success' : 'info'">{{ row.action }}</n-tag></td>
                    <td>{{ row.trigger || '—' }}</td><td>{{ row.quantity || '—' }}</td><td>{{ row.max_sellable_qty ?? '—' }}</td>
                    <td class="long-cell">{{ row.reason || '—' }}</td><td class="long-cell">{{ row.risk || row.stop_loss || '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <n-tabs type="segment" animated>
            <n-tab-pane name="report" tab="完整报告">
              <section class="panel-card markdown-panel"><article class="markdown-body" v-html="rendered" /></section>
            </n-tab-pane>
            <n-tab-pane name="evidence" tab="结构化证据">
              <section class="panel-card json-panel">
                <div class="evidence-grid">
                  <div><h3>数据源</h3><ul><li v-for="item in market.source_chain || []" :key="item">{{ item }}</li></ul></div>
                  <div><h3>数据缺口</h3><ul><li v-for="item in market.errors || []" :key="item">{{ item }}</li><li v-if="!(market.errors || []).length">无阻断性缺口</li></ul></div>
                  <div><h3>多头证据</h3><ul><li v-for="item in result.bull_case || []" :key="item">{{ item }}</li></ul></div>
                  <div><h3>空头证据</h3><ul><li v-for="item in result.bear_case || []" :key="item">{{ item }}</li></ul></div>
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

    <n-modal v-model:show="comparisonOpen" preset="card" title="与上次分析比较" style="width: min(960px, 94vw)">
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
.eyebrow { margin: 0 0 5px; color: var(--app-primary); font-size: 11px; font-weight: 900; letter-spacing: .13em; }
h1 { margin: 0; font-size: clamp(28px, 4vw, 42px); letter-spacing: -.035em; }
.page-heading p:not(.eyebrow), .section-title p { margin: 6px 0 0; color: var(--app-text-muted); }
.heading-actions { display: flex; gap: 8px; }.portfolio-filter { width: 210px; }
.report-layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 15px; align-items: start; }
.panel-card { padding: 19px; }.report-list { position: sticky; top: 88px; display: grid; max-height: calc(100dvh - 112px); gap: 8px; overflow-y: auto; }
.list-head { display: flex; justify-content: space-between; margin-bottom: 7px; }.list-head span { color: var(--app-text-muted); }
.run-item { display: grid; gap: 7px; width: 100%; border: 1px solid var(--app-border-soft); border-radius: 11px; background: var(--app-surface); padding: 12px; color: inherit; text-align: left; cursor: pointer; }
.run-item:hover, .run-item.active { border-color: color-mix(in srgb, var(--app-primary) 48%, var(--app-border)); background: var(--app-primary-soft); }
.run-item > div { display: flex; align-items: center; justify-content: space-between; }.run-item p { display: -webkit-box; margin: 0; overflow: hidden; color: var(--app-text-muted); font-size: 12px; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }.run-item > span { color: var(--app-text-muted); font-size: 10px; }
.report-detail { display: grid; min-width: 0; gap: 14px; }.empty-report { min-height: 480px; }
.decision-hero { display: grid; grid-template-columns: 1fr auto; gap: 16px; }.decision-hero h2 { margin: 0 0 9px; color: var(--app-primary); font-size: 30px; text-transform: uppercase; }.decision-hero > div:first-child > p:last-child { max-width: 800px; margin: 0; font-size: 15px; line-height: 1.7; }
.hero-stats { display: grid; grid-template-columns: repeat(3, minmax(95px, 1fr)); gap: 8px; }.hero-stats div { display: grid; gap: 5px; border: 1px solid var(--app-border-soft); border-radius: 10px; padding: 11px; }.hero-stats span { color: var(--app-text-muted); font-size: 10px; }.hero-stats strong { font-size: 15px; }
.hero-actions { grid-column: 1 / 3; display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--app-border-soft); padding-top: 12px; color: var(--app-text-muted); font-size: 11px; }
.section-title { margin-bottom: 14px; }.section-title h2 { margin: 0; font-size: 17px; }
.table-wrap { overflow-x: auto; }.action-table { width: 100%; min-width: 1000px; border-collapse: collapse; }.action-table th { border-bottom: 1px solid var(--app-border); padding: 9px; color: var(--app-text-muted); font-size: 10px; text-align: left; }.action-table td { border-bottom: 1px solid var(--app-border-soft); padding: 11px 9px; vertical-align: top; }.action-table td:first-child { display: grid; }.action-table td:first-child span { color: var(--app-text-muted); font-size: 10px; }.long-cell { min-width: 220px; line-height: 1.55; }
.markdown-panel { padding: 25px; }.json-panel { display: grid; gap: 16px; }.evidence-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }.evidence-grid div { border: 1px solid var(--app-border-soft); border-radius: 10px; padding: 14px; }.evidence-grid h3 { margin: 0 0 9px; font-size: 14px; }.evidence-grid ul { margin: 0; padding-left: 18px; color: var(--app-text-muted); line-height: 1.7; }
pre { overflow: auto; border-radius: 9px; background: color-mix(in srgb, var(--app-bg) 70%, black); padding: 13px; font-size: 11px; white-space: pre-wrap; }.screenshot-panel { display: grid; min-height: 420px; place-items: center; }.screenshot-panel img { max-width: 100%; max-height: 75dvh; object-fit: contain; }
.change-card { display: grid; grid-template-columns: 80px 1fr 1fr; gap: 10px; margin-top: 12px; border: 1px solid var(--app-border-soft); border-radius: 10px; padding: 12px; }.change-card div > span { color: var(--app-text-muted); font-size: 11px; }
@media (max-width: 980px) { .report-layout { grid-template-columns: 1fr; }.report-list { position: static; display: flex; max-height: none; overflow-x: auto; }.run-item { min-width: 250px; }.decision-hero { grid-template-columns: 1fr; }.hero-actions { grid-column: auto; }.change-card { grid-template-columns: 1fr; } }
@media (max-width: 650px) { .page-heading { align-items: start; flex-direction: column; }.heading-actions { width: 100%; }.portfolio-filter { flex: 1; width: auto; }.hero-stats, .evidence-grid { grid-template-columns: 1fr; } }
</style>
