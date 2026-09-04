<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Camera, ChevronRight, CircleAlert, RefreshCw } from 'lucide-vue-next'

import { api } from '../api'
import type { FuyaoContribution, FuyaoContributionItem, FuyaoSecurityContext, Holding, PortfolioSnapshot } from '../api/types'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import FreshnessLabel from '../components/FreshnessLabel.vue'
import HoldingsUpdateDrawer from '../components/HoldingsUpdateDrawer.vue'
import LoadingState from '../components/LoadingState.vue'
import MetricTile from '../components/MetricTile.vue'
import PageHeader from '../components/PageHeader.vue'
import SectionCard from '../components/SectionCard.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { usePortfolioContext } from '../composables/portfolio'
import { actionLabel, formatCurrency, formatNumber, formatPercent, pctClass, unavailableText } from '../utils/ui'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const error = ref<unknown>(null)
const snapshot = ref<PortfolioSnapshot | null>(null)
const contribution = ref<FuyaoContribution | null>(null)
const securityContext = ref<FuyaoSecurityContext | null>(null)
const securityContextLoading = ref(false)
const updateOpen = ref(route.query.action === 'update')
const selectedHolding = ref<Holding | null>(null)
const detailOpen = ref(false)
let mounted = false

const { portfolios, selectedPortfolioId, selectedPortfolio, loadPortfolios, setSelectedPortfolio } = usePortfolioContext()
const hasPortfolio = computed(() => portfolios.value.length > 0 && Boolean(selectedPortfolioId.value))
const holdings = computed(() => snapshot.value?.holdings || [])
const snapshotIdentityIncomplete = computed(() => Boolean(snapshot.value && snapshot.value.identity_status && snapshot.value.identity_status !== 'RESOLVED'))
const marketValue = computed(() => snapshot.value?.total_market_value ?? (holdings.value.reduce((sum, item) => sum + Number(item.market_value || 0), 0) || null))
const totalAssets = computed(() => snapshot.value?.total_assets ?? null)
const cash = computed(() => snapshot.value?.broker_available_cash ?? null)
const exposure = computed(() => totalAssets.value && marketValue.value != null ? marketValue.value / totalAssets.value : null)
const freshness = computed(() => snapshot.value?.status === 'confirmed' ? 'FRESH' : 'MISSING')

function holdingAction(holding: Holding): string {
  const extra = holding.extra as Record<string, any> | undefined
  return actionLabel(String(extra?.holding_action || extra?.action || 'Hold'))
}

function holdingRisk(holding: Holding): string {
  const extra = holding.extra as Record<string, any> | undefined
  const flags = extra?.risk_flags
  return Array.isArray(flags) && flags.length ? flags.join('、') : '当前没有单独风险提示'
}

function codeKey(value: unknown): string {
  return String(value || '').trim().toUpperCase().split('.')[0]
}

function liveQuoteFor(holding: Holding | null): FuyaoContributionItem | null {
  if (!holding || holding.resolution_status !== 'RESOLVED' || !contribution.value) return null
  const key = codeKey(holding.code)
  return contribution.value.items.find((item) => codeKey(item.code) === key) || null
}

function sourcePercent(value: unknown): string {
  if (value === null || value === undefined || value === '') return unavailableText
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${parsed.toFixed(2)}%` : unavailableText
}

function sourcePercentClass(value: unknown): string {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? pctClass(parsed) : ''
}

function quoteQualityLabel(value: unknown): string {
  return ({ VALID: '有效', DEGRADED: '降级', STALE: '过期', INVALID: '无效', CONFLICT: '冲突', MISSING: '缺失' } as Record<string, string>)[String(value || '').toUpperCase()] || '缺失'
}

function holdingIndustry(holding: Holding): string {
  const extra = holding.extra as Record<string, any> | undefined
  return String(extra?.industry || extra?.industry_name || extra?.sector || unavailableText)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    await loadPortfolios()
    const requested = Number(route.query.portfolio)
    if (requested && portfolios.value.some((item) => item.id === requested)) setSelectedPortfolio(requested)
    const portfolio = portfolios.value.find((item) => item.id === selectedPortfolioId.value)
    snapshot.value = portfolio?.latest_snapshot_id ? await api.getSnapshot(portfolio.latest_snapshot_id) : null
    contribution.value = null
    if (snapshot.value && selectedPortfolioId.value) {
      contribution.value = await api.getPortfolioContribution(selectedPortfolioId.value).catch(() => null)
    }
  } catch (reason) {
    error.value = reason
  } finally {
    loading.value = false
  }
}

function openUpdate() {
  updateOpen.value = true
  void router.replace({ name: 'holdings', query: { ...route.query, action: 'update', portfolio: selectedPortfolioId.value || undefined } })
}

function closeUpdate(value: boolean) {
  updateOpen.value = value
  if (!value) void router.replace({ name: 'holdings', query: { portfolio: selectedPortfolioId.value || undefined } })
}

async function openHolding(holding: Holding) {
  selectedHolding.value = holding
  securityContext.value = null
  securityContextLoading.value = true
  detailOpen.value = true
  if (holding.resolution_status === 'RESOLVED' && holding.canonical_code) {
    securityContext.value = await api.getFuyaoSecurityContext(holding.code).catch(() => null)
  }
  securityContextLoading.value = false
}

function openAnalysis() {
  if (snapshotIdentityIncomplete.value) return
  void router.push({ name: 'analysis', query: { portfolio: selectedPortfolioId.value || undefined } })
}

watch(selectedPortfolioId, (id, previous) => { if (mounted && id !== previous) void load() })
watch(() => route.query.action, (value) => { updateOpen.value = value === 'update' })

onMounted(async () => { await load(); mounted = true })
</script>

<template>
  <section class="workbench-page">
    <PageHeader title="我的持仓" description="看清当前组合，并在需要时快速更新最近确认快照。">
      <template #actions>
        <n-button secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
        <n-button type="primary" :disabled="!hasPortfolio" @click="openUpdate"><template #icon><Camera :size="16" /></template>更新持仓</n-button>
      </template>
    </PageHeader>

    <ErrorState v-if="error" :error="error" @retry="load" />
    <LoadingState v-else-if="loading && !snapshot" message="正在读取最近确认快照" />
    <EmptyState v-else-if="!hasPortfolio" title="还没有持仓组合" description="先配置一个组合，再上传券商持仓截图，系统才能结合真实组合进行分析。">
      <template #action><n-button type="primary" @click="router.push({ name: 'settings' })">去配置</n-button></template>
    </EmptyState>
    <template v-else>
      <div class="as-of-strip"><span>截至最近确认快照</span><strong>{{ snapshot?.snapshot_time ? new Date(snapshot.snapshot_time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '尚未确认' }}</strong><FreshnessLabel :freshness="freshness" :at="snapshot?.snapshot_time" /><span class="muted">{{ selectedPortfolio?.name }}</span></div>
      <n-alert v-if="snapshotIdentityIncomplete" type="warning" :show-icon="false">证券身份不完整。该快照保留审计历史，但不会作为新的 Analysis 默认输入，请重新导入并修正。</n-alert>

      <SectionCard title="资产摘要" description="只显示已确认快照中的权威数据。">
        <div class="metric-grid four"><MetricTile label="总资产" :value="formatCurrency(totalAssets, 2)" /><MetricTile label="持仓市值" :value="formatCurrency(marketValue, 2)" /><MetricTile label="可用现金" :value="formatCurrency(cash, 2)" /><MetricTile label="仓位" :value="formatPercent(exposure)" tone="risk" /></div>
      </SectionCard>

      <SectionCard title="持仓列表" :description="`${holdings.length} 个标的 · 点击一行查看详情`">
        <div v-if="holdings.length" class="table-wrap"><table class="holdings-table"><thead><tr><th>标的</th><th>数量</th><th>成本</th><th>实时价格</th><th>今日涨跌</th><th>今日贡献</th><th>快照收益</th><th>仓位</th><th>行业</th><th>身份</th><th>最新建议</th><th aria-label="查看详情" /></tr></thead><tbody><tr v-for="(holding, index) in holdings" :key="holding.code || holding.name || index" class="holding-row clickable" tabindex="0" @click="openHolding(holding)" @keydown.enter="openHolding(holding)"><td><div class="instrument"><strong>{{ holding.name || holding.code || '未命名标的' }}</strong><small>{{ holding.canonical_code || holding.code || '代码待匹配' }}</small></div></td><td class="mono-number">{{ formatNumber(holding.qty, 0) }}</td><td class="mono-number">{{ formatNumber(holding.cost, 3) }}</td><td class="mono-number">{{ formatNumber(liveQuoteFor(holding)?.current_price, 3) }}</td><td :class="sourcePercentClass(liveQuoteFor(holding)?.today_change_pct)">{{ sourcePercent(liveQuoteFor(holding)?.today_change_pct) }}</td><td :class="sourcePercentClass(liveQuoteFor(holding)?.contribution_pct)">{{ sourcePercent(liveQuoteFor(holding)?.contribution_pct) }}</td><td :class="pctClass(holding.pnl)">{{ formatPercent(holding.pnl) }}</td><td class="mono-number">{{ formatPercent(holding.weight) }}</td><td>{{ holdingIndustry(holding) }}</td><td><n-tag size="small" :bordered="false" :type="holding.resolution_status === 'RESOLVED' ? 'success' : 'warning'">{{ holding.resolution_status === 'RESOLVED' ? '已匹配' : '证券身份不完整' }}</n-tag></td><td><n-tag size="small" :bordered="false" type="info">{{ holdingAction(holding) }}</n-tag></td><td><ChevronRight :size="16" class="row-arrow" /></td></tr></tbody></table></div>
        <EmptyState v-else title="最近确认快照里还没有持仓" description="上传一张新的券商持仓截图，确认后这里会出现持仓明细。">
          <template #action><n-button type="primary" @click="openUpdate">导入第一份持仓</n-button></template>
        </EmptyState>
      </SectionCard>

      <div class="holdings-footnote"><CircleAlert :size="15" aria-hidden="true" /><span>数量、成本和快照收益来自最近确认快照；实时价格、今日涨跌和贡献来自 Fuyao 当前行情，缺失报价不会按 0 计算。{{ contribution?.quality_status && contribution.quality_status !== 'VALID' ? `当前实时覆盖 ${Math.round((contribution.coverage || 0) * 100)}%，状态为${contribution.quality_status === 'MISSING' ? '缺失' : '降级'}。` : '' }}</span></div>
    </template>

    <HoldingsUpdateDrawer :show="updateOpen" :portfolio-id="selectedPortfolioId" @update:show="closeUpdate" @confirmed="snapshot = $event" />

    <n-drawer v-model:show="detailOpen" width="min(430px, 100vw)" placement="right">
      <n-drawer-content :title="selectedHolding?.name || selectedHolding?.code || '持仓详情'" closable>
        <div v-if="selectedHolding" class="detail-stack">
          <div class="detail-symbol"><strong>{{ selectedHolding.name || selectedHolding.code }}</strong><code>{{ selectedHolding.code || '—' }}</code></div>
          <section><h3>当前持仓</h3><dl class="detail-grid"><div><dt>数量</dt><dd>{{ formatNumber(selectedHolding.qty, 0) }}</dd></div><div><dt>成本</dt><dd>{{ formatNumber(selectedHolding.cost, 3) }}</dd></div><div><dt>市值</dt><dd>{{ formatCurrency(selectedHolding.market_value, 2) }}</dd></div><div><dt>仓位</dt><dd>{{ formatPercent(selectedHolding.weight) }}</dd></div><div><dt>收益</dt><dd :class="pctClass(selectedHolding.pnl)">{{ formatPercent(selectedHolding.pnl) }}</dd></div></dl></section>
          <section><h3>最新建议</h3><div class="advice-box"><strong>{{ holdingAction(selectedHolding) }}</strong><p>{{ holdingRisk(selectedHolding) }}</p></div></section>
          <section><h3>实时标记</h3><div v-if="securityContextLoading" class="muted">正在读取 Fuyao 当前行情与证据…</div><template v-else><dl class="detail-grid"><div><dt>现价</dt><dd>{{ formatNumber(liveQuoteFor(selectedHolding)?.current_price, 3) }}</dd></div><div><dt>今日涨跌</dt><dd :class="sourcePercentClass(liveQuoteFor(selectedHolding)?.today_change_pct)">{{ sourcePercent(liveQuoteFor(selectedHolding)?.today_change_pct) }}</dd></div><div><dt>今日贡献</dt><dd :class="sourcePercentClass(liveQuoteFor(selectedHolding)?.contribution_pct)">{{ sourcePercent(liveQuoteFor(selectedHolding)?.contribution_pct) }}</dd></div><div><dt>报价质量</dt><dd>{{ quoteQualityLabel(liveQuoteFor(selectedHolding)?.quote_quality) }}</dd></div></dl></template></section>
          <section v-if="securityContext?.fundamental_summary?.status === 'AVAILABLE'"><h3>基本面与估值</h3><dl class="detail-grid"><div><dt>成长</dt><dd>{{ securityContext.fundamental_summary.growth || unavailableText }}</dd></div><div><dt>盈利</dt><dd>{{ securityContext.fundamental_summary.profitability || unavailableText }}</dd></div><div><dt>现金流</dt><dd>{{ securityContext.fundamental_summary.cash_flow || unavailableText }}</dd></div><div><dt>估值</dt><dd>{{ securityContext.fundamental_summary.valuation || unavailableText }}</dd></div></dl><p class="muted">当前分析允许；历史 PIT：{{ securityContext.historical_pit_status || '未证明' }}。</p></section>
          <section><h3>最近分析</h3><p class="muted">分析结论会根据当前组合和市场时点更新。</p><n-button secondary :disabled="snapshotIdentityIncomplete" @click="openAnalysis">查看今日分析</n-button></section>
          <TechnicalDetails title="高级持仓数据"><pre>{{ JSON.stringify(selectedHolding, null, 2) }}</pre></TechnicalDetails>
        </div>
      </n-drawer-content>
    </n-drawer>
  </section>
</template>

<style scoped>
.workbench-page { display: grid; gap: 18px; }.as-of-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 14px; color: var(--text-muted); font-size: 12px; }.as-of-strip strong { color: var(--text); font-weight: 600; }.metric-grid { display: grid; gap: 16px; }.metric-grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }.table-wrap { max-width: 100%; overflow-x: auto; }.holdings-table { width: 100%; min-width: 1100px; border-collapse: collapse; }.holdings-table th { padding: 10px 9px; border-bottom: 1px solid var(--border-strong); color: var(--text-muted); font-size: 12px; font-weight: 600; text-align: left; white-space: nowrap; }.holdings-table td { border-bottom: 1px solid var(--border); padding: 13px 9px; vertical-align: middle; white-space: nowrap; }.holding-row:hover, .holding-row:focus { background: var(--row-hover); outline: none; }.instrument { display: grid; gap: 2px; min-width: 150px; }.instrument small { color: var(--text-muted); font-size: 12px; }.row-arrow { color: var(--text-muted); }.holdings-footnote { display: flex; align-items: flex-start; gap: 8px; color: var(--text-muted); font-size: 12px; line-height: 1.55; }.holdings-footnote svg { flex: none; margin-top: 2px; color: var(--warning); }.detail-stack { display: grid; gap: 22px; }.detail-symbol { display: grid; gap: 4px; border-bottom: 1px solid var(--border); padding-bottom: 14px; }.detail-symbol strong { font-size: 22px; }.detail-symbol code { color: var(--text-muted); }.detail-stack h3 { margin: 0 0 10px; font-size: 15px; }.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 0; }.detail-grid div { display: grid; gap: 4px; border-bottom: 1px solid var(--border); padding-bottom: 9px; }.detail-grid dt { color: var(--text-muted); font-size: 12px; }.detail-grid dd { margin: 0; font-variant-numeric: tabular-nums; }.advice-box { border-left: 3px solid var(--primary); background: var(--primary-soft); padding: 12px; }.advice-box p { margin: 5px 0 0; color: var(--text-muted); font-size: 12px; line-height: 1.6; }.detail-stack pre { max-height: 360px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
@media (max-width: 720px) { .metric-grid.four { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 430px) { .metric-grid.four { grid-template-columns: 1fr; } }
</style>
