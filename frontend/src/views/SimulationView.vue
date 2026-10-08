<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Activity, ArrowDownToLine, ArrowRight, CalendarDays, CheckCircle2, Clock3, Database, Pause, Play, RefreshCw, RotateCcw, ShieldCheck, Target, WalletCards } from 'lucide-vue-next'
import { useDialog, useMessage } from 'naive-ui'

import { api } from '../api'
import type { ShadowAccount, ShadowDailySnapshot, ShadowDecision, ShadowDecisionDetail, ShadowFill, ShadowOrder, ShadowPerformance, ShadowValidation } from '../api/types'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import FreshnessLabel from '../components/FreshnessLabel.vue'
import LoadingState from '../components/LoadingState.vue'
import MetricTile from '../components/MetricTile.vue'
import PageHeader from '../components/PageHeader.vue'
import SectionCard from '../components/SectionCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { usePortfolioContext } from '../composables/portfolio'
import { formatCurrency, formatNumber, formatPercent, fmtDateTime, unavailableText } from '../utils/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const working = ref(false)
const loadError = ref<unknown>(null)
const accounts = ref<ShadowAccount[]>([])
const account = ref<ShadowAccount | null>(null)
const performance = ref<ShadowPerformance | null>(null)
const validation = ref<ShadowValidation | null>(null)
const decisions = ref<ShadowDecision[]>([])
const orders = ref<ShadowOrder[]>([])
const fills = ref<ShadowFill[]>([])
const dailySnapshots = ref<ShadowDailySnapshot[]>([])
const selectedDecision = ref<ShadowDecisionDetail | null>(null)
const selectedAccountId = ref<number | null>(null)
const decisionFilter = ref('ALL')
const createOpen = ref(false)
const accountName = ref('模拟跟随')
let mounted = false

const { portfolios, selectedPortfolioId, selectedPortfolio, loadPortfolios, setSelectedPortfolio } = usePortfolioContext()
const latestSnapshotId = computed(() => selectedPortfolio.value?.latest_snapshot_id || null)
const filteredDecisions = computed(() => decisionFilter.value === 'ALL' ? decisions.value : decisions.value.filter((item) => String(item.final_action).toUpperCase() === decisionFilter.value))
const currentGeneration = computed(() => account.value?.shadow_generation || 0)
const pendingOrders = computed(() => orders.value.filter((item) => ['PENDING', 'PARTIAL'].includes(String(item.status).toUpperCase())))
const blockedOrders = computed(() => orders.value.filter((item) => ['BLOCKED', 'EXPIRED', 'CANCELLED', 'SUPERSEDED'].includes(String(item.status).toUpperCase())))
const filledOrders = computed(() => orders.value.filter((item) => ['FILLED', 'PARTIAL'].includes(String(item.status).toUpperCase())))
const hasConditionalAdd = computed(() => selectedDecision.value?.selected_actions?.some((item) => String(item.action || item.recommended_action || '').toLowerCase() === 'conditional_add') || false)
const sampleDays = computed(() => performance.value?.sample_days ?? validation.value?.live_sample_days ?? null)
const sampleDaysDisplay = computed(() => sampleDays.value == null || sampleDays.value <= 0 ? '—' : sampleDays.value)
const sampleInsufficient = computed(() => sampleDays.value == null || sampleDays.value < 20)
const validationText = computed(() => !validation.value ? '数据不完整' : sampleInsufficient.value ? '样本不足，继续观察' : '持续观察')
const chartLines = computed(() => {
  const rows = [...dailySnapshots.value].reverse()
  if (rows.length < 2) return { equity: '', benchmark: '' }
  const equity = rows.map((item) => item.total_equity == null ? null : Number(item.total_equity))
  const benchmark = [] as Array<number | null>
  let benchmarkIndex = 1
  for (const item of rows) {
    if (item.benchmark_return == null || !Number.isFinite(Number(item.benchmark_return))) {
      benchmark.push(null)
    } else {
      benchmarkIndex *= 1 + Number(item.benchmark_return)
      benchmark.push(benchmarkIndex)
    }
  }
  const knownEquity = equity.filter((value): value is number => value != null && Number.isFinite(value))
  const knownBenchmark = benchmark.filter((value): value is number => value != null && Number.isFinite(value))
  if (knownEquity.length < 2) return { equity: '', benchmark: '' }
  const normalizedEquity = equity.map((value) => value == null ? null : value / knownEquity[0] * 100)
  const normalizedBenchmark = benchmark.map((value) => value == null ? null : value * 100)
  const combined = [...normalizedEquity, ...normalizedBenchmark].filter((value): value is number => value != null && Number.isFinite(value))
  const min = Math.min(...combined)
  const max = Math.max(...combined)
  const span = max - min || 1
  const points = (values: Array<number | null>) => values.map((value, index) => value == null || !Number.isFinite(value) ? null : `${(index / (values.length - 1)) * 100},${96 - ((value - min) / span) * 76}`).filter((value): value is string => Boolean(value)).join(' ')
  return { equity: points(normalizedEquity), benchmark: knownBenchmark.length ? points(normalizedBenchmark) : '' }
})

function missing(value: unknown, formatter: (input: any) => string) {
  return value === null || value === undefined || value === '' ? '—' : formatter(value)
}
function money(value: unknown) { return missing(value, (input) => formatCurrency(input, 2)) }
function percent(value: unknown) { return missing(value, (input) => formatPercent(input)) }
function number(value: unknown, digits = 2) { return missing(value, (input) => formatNumber(input, digits)) }
function shortTime(value?: string | null) { return value ? fmtDateTime(value).replace(/^\d{4}-\d{2}-\d{2} /, '') : '—' }
function statusType(status?: string | null): 'success' | 'warning' | 'error' | 'info' {
  const value = String(status || '').toUpperCase()
  if (['ACTIVE', 'VALID', 'FILLED', 'COMPLETED', 'ALIGNED', 'READY'].includes(value)) return 'success'
  if (['PAUSED', 'PENDING', 'PARTIAL', 'DEGRADED', 'INSUFFICIENT_LIVE_EVIDENCE', 'NO_MATCH'].includes(value)) return 'warning'
  if (['BLOCKED', 'EXPIRED', 'CANCELLED', 'SUPERSEDED', 'ERROR', 'DATA_GAP'].includes(value)) return 'error'
  return 'info'
}
function actionType(action?: string | null): 'success' | 'warning' | 'error' | 'info' {
  const value = String(action || '').toUpperCase()
  if (value === 'ACTION') return 'warning'
  if (['BLOCKED', 'SELL', 'REDUCE', 'EXIT'].includes(value)) return 'error'
  return value === 'NO_ACTION' ? 'info' : 'success'
}
function actionText(action?: string | null) {
  const value = String(action || '').toUpperCase()
  return value === 'NO_ACTION' ? '暂不操作' : value === 'ACTION' ? '需要调整' : value || '—'
}
function accountStatusText(status?: string | null) {
  const value = String(status || '').toUpperCase()
  return value === 'ACTIVE' ? '运行中' : value === 'PAUSED' ? '已暂停' : value === 'CLOSED' ? '已关闭' : value || '未知'
}
function basisText(value?: string | null) { return String(value || '').toUpperCase() === 'DAILY_BAR' ? 'DailyBar' : String(value || '').toUpperCase() === 'LIVE_QUOTE' ? 'Live Quote' : value || '—' }
function validationOutcomeText(item: any) {
  const buckets = item.outcomes_by_target_horizon || []
  return buckets.length ? buckets.slice(0, 2).map((bucket: any) => `${bucket.target_type}/${bucket.target_key} ${bucket.horizon_trading_days}D ${percent(bucket.mean_excess_return)}`).join(' · ') : '暂无已完成的目标结果'
}

async function loadPortfolioData(portfolioId = selectedPortfolioId.value, preferredAccountId = selectedAccountId.value) {
  if (!portfolioId) return
  loading.value = true
  loadError.value = null
  try {
    const [rows, validationRow] = await Promise.all([api.listShadowAccounts(portfolioId), api.getShadowValidation(portfolioId)])
    accounts.value = rows
    validation.value = validationRow
    const next = rows.find((item) => item.id === preferredAccountId) || rows.find((item) => item.status === 'ACTIVE') || rows[0] || null
    selectedAccountId.value = next?.id || null
    await loadAccountData(next?.id || null, portfolioId)
  } catch (reason) {
    loadError.value = reason
  } finally {
    loading.value = false
  }
}

async function loadAccountData(accountId: number | null, portfolioId = selectedPortfolioId.value) {
  selectedAccountId.value = accountId
  account.value = null
  performance.value = null
  selectedDecision.value = null
  orders.value = []
  fills.value = []
  dailySnapshots.value = []
  if (!accountId || !portfolioId) { decisions.value = []; return }
  try {
    const [accountRow, performanceRow, decisionRows, orderRows, fillRows, dailyRows] = await Promise.all([
      api.getShadowAccount(accountId), api.getShadowPerformance(accountId), api.listShadowDecisions({ account_id: accountId, limit: 80 }), api.listShadowOrders({ account_id: accountId, limit: 80 }), api.listShadowFills({ account_id: accountId, limit: 80 }), api.listShadowDailySnapshots({ account_id: accountId, limit: 120 }),
    ])
    account.value = accountRow
    performance.value = performanceRow
    decisions.value = decisionRows
    orders.value = orderRows
    fills.value = fillRows
    dailySnapshots.value = dailyRows
    if (decisionRows[0]) await selectDecision(decisionRows[0].id)
  } catch (reason) {
    loadError.value = reason
  }
}

async function selectDecision(id: number) {
  try { selectedDecision.value = await api.getShadowDecision(id) } catch (reason) { loadError.value = reason }
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    await loadPortfolios()
    const requested = Number(route.query.portfolio)
    const preferred = portfolios.value.find((item) => item.id === requested)?.id || portfolios.value.find((item) => item.id === selectedPortfolioId.value)?.id || portfolios.value.find((item) => item.is_default)?.id || portfolios.value[0]?.id || null
    if (preferred) setSelectedPortfolio(preferred)
    await loadPortfolioData(preferred, Number(route.query.shadow) || null)
  } catch (reason) { loadError.value = reason } finally { loading.value = false }
}

function openCreate() {
  if (!latestSnapshotId.value) { message.warning('当前组合还没有已确认持仓快照，无法初始化模拟账户'); return }
  accountName.value = '模拟跟随'
  createOpen.value = true
}

async function createAccount() {
  if (!selectedPortfolioId.value || !latestSnapshotId.value || working.value) return
  working.value = true
  try {
    const row = await api.createShadowAccount({ portfolio_id: selectedPortfolioId.value, snapshot_id: latestSnapshotId.value, name: accountName.value.trim() || '模拟跟随' })
    createOpen.value = false
    message.success('模拟账户已创建')
    await loadPortfolioData(selectedPortfolioId.value, row.id)
  } catch (reason) { message.error((reason as Error).message) } finally { working.value = false }
}

function toggleAccountStatus() {
  if (!account.value || working.value) return
  const paused = account.value.status === 'PAUSED'
  dialog.warning({ title: paused ? '恢复模拟账户' : '暂停模拟账户', content: paused ? '恢复后，后续合格的生产决策可以继续进入模拟执行链。' : '暂停只停止后续模拟执行，不删除已有记录。', positiveText: paused ? '确认恢复' : '确认暂停', negativeText: '取消', onPositiveClick: async () => { working.value = true; try { account.value = paused ? await api.resumeShadowAccount(account.value!.id) : await api.pauseShadowAccount(account.value!.id); message.success(paused ? '模拟账户已恢复' : '模拟账户已暂停') } catch (reason) { message.error((reason as Error).message) } finally { working.value = false } } })
}

function rebaseAccount() {
  if (!account.value || !latestSnapshotId.value || working.value) return
  dialog.warning({ title: '创建新的模拟 Generation', content: `这会从最近确认快照 #${latestSnapshotId.value} 创建 G${account.value.shadow_generation + 1}，旧历史不会删除。`, positiveText: '确认 Rebase', negativeText: '取消', onPositiveClick: async () => { working.value = true; try { const row = await api.rebaseShadowAccount(account.value!.id, latestSnapshotId.value); message.success(`已切换到模拟 Generation G${row.shadow_generation}`); await loadPortfolioData(selectedPortfolioId.value, row.id) } catch (reason) { message.error((reason as Error).message) } finally { working.value = false } } })
}

async function alignActual() {
  if (!selectedDecision.value || selectedDecision.value.final_action !== 'ACTION' || working.value) return
  working.value = true
  try { await api.alignShadowDecision(selectedDecision.value.id); await selectDecision(selectedDecision.value.id); message.success('已按 Trade Ledger 事实刷新对齐结果') } catch (reason) { message.error((reason as Error).message) } finally { working.value = false }
}

watch(selectedPortfolioId, (value, previous) => { if (mounted && value !== previous) { selectedAccountId.value = null; void router.replace({ name: 'simulation', query: { ...route.query, portfolio: value ? String(value) : undefined, shadow: undefined } }); void loadPortfolioData(value, null) } })
onMounted(async () => { await load(); mounted = true })
</script>

<template>
  <section class="workbench-page">
    <PageHeader title="模拟跟随" description="看看如果一直按照系统最终建议操作，会发生什么。">
      <template #actions>
        <n-button secondary :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
        <n-button v-if="account" secondary :loading="working" @click="toggleAccountStatus"><template #icon><Play v-if="account.status === 'PAUSED'" :size="15" /><Pause v-else :size="15" /></template>{{ account.status === 'PAUSED' ? '恢复' : '暂停' }}</n-button>
        <n-button v-if="account" secondary :loading="working" @click="rebaseAccount"><template #icon><RotateCcw :size="15" /></template>更新起点</n-button>
        <n-button v-if="!account && selectedPortfolio" type="primary" :disabled="!latestSnapshotId" @click="openCreate"><template #icon><Target :size="16" /></template>创建模拟账户</n-button>
      </template>
    </PageHeader>

    <div class="simulation-banner"><div><span class="tech-badge">SHADOW</span><strong>不会发送真实订单</strong><p>模拟账户独立于真实持仓，只记录 Decision → Execution → Outcome。</p></div><FreshnessLabel :freshness="performance?.status || (account ? 'FRESH' : 'MISSING')" :at="dailySnapshots[0]?.trade_date" /></div>
    <ErrorState v-if="loadError" :error="loadError" @retry="load" />
    <LoadingState v-else-if="loading && !account && portfolios.length" message="正在读取模拟跟随数据" />
    <EmptyState v-else-if="!portfolios.length" title="还没有生产组合" description="先导入并确认一份持仓，系统才能从真实快照创建独立的模拟账户。">
      <template #action><n-button type="primary" @click="router.push({ name: 'holdings', query: { action: 'update' } })">先导入持仓</n-button></template>
    </EmptyState>
    <template v-else-if="selectedPortfolio && !account">
      <EmptyState title="还没有模拟记录" description="创建模拟账户后，系统会记录如果按最终建议执行会发生什么。">
        <template #action><n-button type="primary" :disabled="!latestSnapshotId" @click="openCreate">创建模拟账户</n-button></template>
      </EmptyState>
    </template>

    <template v-if="account">
      <SectionCard :title="account.name" :description="`${selectedPortfolio?.name || '当前组合'} · G${currentGeneration} · 独立纸面账户`">
        <template #actions><StatusBadge :status="account.status" :label="accountStatusText(account.status)" /><code class="paper-badge">paper-only</code></template>
        <div class="metric-grid six"><MetricTile label="模拟资产" :value="money(performance?.current_equity)" /><MetricTile label="累计收益" :value="percent(performance?.cumulative_return)" tone="positive" /><MetricTile label="同期基准" :value="percent(performance?.benchmark_return)" /><MetricTile label="超额收益" :value="percent(performance?.excess_return)" tone="positive" /><MetricTile label="最大回撤" :value="percent(performance?.max_drawdown)" tone="risk" /><MetricTile label="样本天数" :value="sampleDaysDisplay" /></div>
        <div class="performance-note"><span>当前现金 {{ money(performance?.current_cash ?? account.current_cash) }}</span><span>成交 {{ fills.length }} 笔</span><span>性能质量 {{ performance?.performance_quality || '—' }}</span></div>
      </SectionCard>

      <div v-if="sampleInsufficient" class="sample-warning"><ShieldCheck :size="17" /><span>样本不足，暂不能判断策略效果。</span><small>当前样本：{{ sampleDaysDisplay }} 个交易日</small></div>
      <div v-if="(performance?.snapshots?.length || 0) > 1 && chartLines.equity" class="chart-card panel-card"><div class="chart-header"><div><h2>模拟净值</h2><p>只使用真实 Shadow daily snapshots；没有时序数据时不绘制假图。起始值归一为 100。</p></div><span>Shadow Equity vs Benchmark</span></div><svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="模拟净值与基准曲线"><line x1="0" y1="96" x2="100" y2="96" /><polyline :points="chartLines.equity" fill="none" stroke="currentColor" stroke-width="1.8" vector-effect="non-scaling-stroke" /><polyline v-if="chartLines.benchmark" :points="chartLines.benchmark" fill="none" stroke="var(--text-muted)" stroke-width="1.2" stroke-dasharray="3 2" vector-effect="non-scaling-stroke" /></svg><div class="chart-legend"><span><i class="legend-equity" />模拟净值</span><span v-if="chartLines.benchmark"><i class="legend-benchmark" />基准</span></div></div>

      <div class="simulation-grid">
        <SectionCard title="最近记录" description="Decision → Execution → Outcome" class="timeline-panel">
          <div v-if="selectedDecision" class="timeline-focus"><span>{{ selectedDecision.trade_date }} · {{ shortTime(selectedDecision.decision_finalized_at) }}</span><strong>{{ actionText(selectedDecision.final_action) }}</strong><p v-if="selectedDecision.reason_codes?.length">{{ selectedDecision.reason_codes.join('、') }}</p><div class="timeline-track"><div class="timeline-step done"><span>1</span><strong>Decision</strong><small>{{ selectedDecision.quality_status }}</small></div><div class="timeline-line" /><div class="timeline-step" :class="{ done: selectedDecision.execution?.intents?.length }"><span>2</span><strong>Execution</strong><small>{{ selectedDecision.execution?.intents?.length ? '已生成 Intent' : '未产生模拟订单' }}</small></div><div class="timeline-line" /><div class="timeline-step" :class="{ done: selectedDecision.outcomes?.length }"><span>3</span><strong>Outcome</strong><small>{{ selectedDecision.outcomes?.length ? '已有结果' : '尚未到期' }}</small></div></div><n-alert v-if="hasConditionalAdd" type="warning" :show-icon="false">条件加仓仅记录建议，V1 暂不模拟条件触发成交。</n-alert></div>
          <EmptyState v-else title="还没有模拟记录" description="下一次完成的组合决策会出现在这里。" />
          <div v-if="decisions.length" class="decision-list"><div class="subheading"><Target :size="14" />Decision</div><button v-for="item in filteredDecisions.slice(0, 8)" :key="item.id" class="decision-row" :class="{ selected: selectedDecision?.id === item.id }" @click="selectDecision(item.id)"><span><strong>{{ item.trade_date }}</strong><small>{{ item.decision_checkpoint || item.decision_kind }} · {{ shortTime(item.decision_finalized_at) }}</small></span><span><StatusBadge :status="item.final_action" :label="item.final_action" /><small>{{ item.quality_status }}</small></span></button></div>
        </SectionCard>

        <SectionCard title="执行与结果" description="Intent、Fill、Outcome 分开记录。" class="execution-panel">
          <div class="execution-summary"><div><span>待处理</span><strong>{{ pendingOrders.length }}</strong></div><div><span>已成交</span><strong>{{ filledOrders.length }}</strong></div><div><span>未成交/阻断</span><strong>{{ blockedOrders.length }}</strong></div></div>
          <div class="subheading"><Clock3 :size="14" />Execution</div><div v-if="orders.length" class="fact-list"><div v-for="item in orders.slice(0, 7)" :key="item.id" class="fact-row"><div><strong>{{ item.side }} {{ item.code }}</strong><small>最早 {{ fmtDateTime(item.earliest_executable_at) }}</small></div><StatusBadge :status="item.status" :label="item.status" /></div></div><p v-else class="empty-line">没有模拟订单 Intent</p>
          <div class="subheading"><CheckCircle2 :size="14" />Outcome / Fill</div><div v-if="fills.length" class="fact-list"><div v-for="item in fills.slice(0, 5)" :key="item.id" class="fact-row"><div><strong>{{ item.side }} {{ item.code }} · {{ number(item.quantity, 0) }} 股</strong><small>{{ fmtDateTime(item.fill_at) }} · {{ basisText(item.price_basis) }}</small></div><strong>{{ money(item.price) }}</strong></div></div><p v-else class="empty-line">还没有 Paper Fill</p>
        </SectionCard>
      </div>

      <div class="simulation-grid">
        <SectionCard title="证据状态" :description="validationText"><div class="metric-grid four"><MetricTile label="Live sample days" :value="validation?.live_sample_days ?? '—'" /><MetricTile label="Decision count" :value="validation?.decision_count ?? '—'" /><MetricTile label="Action rate" :value="validation?.decision_count ? percent(validation.cohorts.reduce((sum, item) => sum + item.action_count, 0) / validation.decision_count) : '—'" /><MetricTile label="Backtest 混入" value="否" /></div><div v-if="validation?.cohorts?.length" class="cohort-list"><div v-for="item in validation.cohorts.slice(0, 6)" :key="JSON.stringify(item.cohort)" class="cohort-row"><div><strong>{{ item.cohort.parameter_set_hash ? String(item.cohort.parameter_set_hash).slice(0, 12) : 'UNKNOWN' }}</strong><small>G{{ item.cohort.shadow_generation || '—' }} · {{ item.sample_days }} days · N={{ item.decision_count }}</small></div><div><StatusBadge :status="item.evidence_status" :label="item.evidence_status" /><small>{{ validationOutcomeText(item) }}</small></div></div></div><p v-else class="empty-line">尚未积累足够的 Live Evidence，继续观察即可。</p></SectionCard>
        <SectionCard title="Daily Shadow Snapshot" :description="`${dailySnapshots?.length || 0} 天记录`"><div v-if="dailySnapshots.length" class="daily-list"><div v-for="item in dailySnapshots.slice(0, 7)" :key="item.id" class="daily-row"><div><strong>{{ item.trade_date }}</strong><small>{{ item.position_count }} 个持仓 · {{ item.price_basis || '价格基础未知' }}</small></div><div><strong>{{ money(item.total_equity) }}</strong><span>{{ percent(item.daily_return) }}</span></div></div></div><p v-else class="empty-line">尚未有收盘估值快照。</p><TechnicalDetails title="模拟账户技术详情"><pre>{{ JSON.stringify({ account, performance }, null, 2) }}</pre></TechnicalDetails></SectionCard>
      </div>
    </template>

    <n-modal v-model:show="createOpen" preset="card" title="创建模拟账户" style="width: min(520px, calc(100vw - 32px))"><div class="modal-copy"><div class="modal-warning"><ShieldCheck :size="17" /><span>模拟账户只复制一次真实组合快照，之后独立演化。</span></div><p>组合：<strong>{{ selectedPortfolio?.name }}</strong> · 初始化快照 #{{ latestSnapshotId }}</p><n-form-item label="账户名称"><n-input v-model:value="accountName" maxlength="128" /></n-form-item></div><template #footer><div class="modal-actions"><n-button @click="createOpen = false">取消</n-button><n-button type="primary" :loading="working" @click="createAccount">确认创建</n-button></div></template></n-modal>
  </section>
</template>

<style scoped>
.workbench-page { display: grid; gap: 18px; }.simulation-banner { display: flex; align-items: center; justify-content: space-between; gap: 16px; border: 1px solid var(--border); border-left: 4px solid var(--primary); border-radius: 8px; background: var(--surface); padding: 14px 16px; }.simulation-banner > div { min-width: 0; }.tech-badge, .paper-badge { display: inline-flex; align-items: center; border: 1px solid var(--border); border-radius: 4px; background: var(--surface-muted); padding: 3px 6px; color: var(--primary); font-size: 10px; font-weight: 800; letter-spacing: .08em; }.simulation-banner strong { margin-left: 8px; font-size: 14px; }.simulation-banner p { margin: 5px 0 0; color: var(--text-muted); font-size: 12px; }.metric-grid { display: grid; gap: 16px; }.metric-grid.six { grid-template-columns: repeat(6, minmax(0, 1fr)); }.metric-grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }.performance-note { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 20px; color: var(--text-muted); font-size: 12px; }.sample-warning { display: flex; align-items: center; gap: 9px; border: 1px solid color-mix(in srgb, var(--warning) 38%, var(--border)); border-radius: 8px; background: color-mix(in srgb, var(--warning) 8%, var(--surface)); padding: 11px 14px; color: var(--warning); }.sample-warning small { margin-left: auto; color: var(--text-muted); }.chart-card { display: grid; gap: 14px; padding: 18px 20px; }.chart-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.chart-header h2 { margin: 0; font-size: 17px; }.chart-header p { margin: 5px 0 0; color: var(--text-muted); font-size: 12px; }.chart-header > span { color: var(--text-muted); font-size: 11px; }.chart-card svg { width: 100%; height: 160px; overflow: visible; border-bottom: 1px solid var(--border); background: linear-gradient(to bottom, transparent 24%, var(--border) 25%, transparent 26%, transparent 49%, var(--border) 50%, transparent 51%, transparent 74%, var(--border) 75%, transparent 76%); color: var(--primary); }.chart-card line { stroke: var(--border-strong); stroke-width: .5; }.simulation-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(340px, .75fr); gap: 18px; }.timeline-focus { display: grid; gap: 8px; border-left: 3px solid var(--primary); background: var(--primary-soft); padding: 12px 14px; }.timeline-focus > span, .timeline-focus p { color: var(--text-muted); font-size: 12px; }.timeline-focus p { margin: 0; }.timeline-focus > strong { font-size: 22px; }.timeline-track { display: flex; align-items: center; gap: 7px; margin: 10px 0 4px; }.timeline-step { display: grid; min-width: 0; gap: 3px; }.timeline-step span { display: grid; width: 24px; height: 24px; place-items: center; border-radius: 50%; background: var(--surface); color: var(--text-muted); font-size: 11px; }.timeline-step.done span { background: var(--primary); color: white; }.timeline-step strong { font-size: 12px; }.timeline-step small { color: var(--text-muted); font-size: 10px; }.timeline-line { height: 1px; flex: 1; background: var(--border-strong); }.decision-list { display: grid; margin-top: 15px; border-top: 1px solid var(--border); }.subheading { display: flex; align-items: center; gap: 6px; margin: 15px 0 8px; color: var(--text-muted); font-size: 12px; font-weight: 700; }.decision-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; border: 0; border-bottom: 1px solid var(--border); background: none; padding: 10px 0; color: inherit; text-align: left; cursor: pointer; }.decision-row:hover, .decision-row.selected { background: var(--row-hover); }.decision-row > span { display: grid; gap: 3px; min-width: 0; }.decision-row small, .fact-row small, .cohort-row small, .daily-row small { color: var(--text-muted); font-size: 11px; }.fact-list, .cohort-list, .daily-list { display: grid; }.fact-row, .cohort-row, .daily-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--border); padding: 10px 0; }.fact-row > div, .cohort-row > div, .daily-row > div { display: grid; gap: 3px; min-width: 0; }.execution-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }.execution-summary div { display: grid; gap: 4px; border-left: 2px solid var(--border-strong); padding-left: 9px; }.execution-summary span { color: var(--text-muted); font-size: 11px; }.execution-summary strong { font-size: 18px; }.empty-line { margin: 9px 0; color: var(--text-muted); font-size: 12px; }.daily-row > div:last-child { text-align: right; }.daily-row > div:last-child span { color: var(--text-muted); font-size: 11px; }.modal-copy { display: grid; gap: 14px; }.modal-copy p { margin: 0; color: var(--text-muted); }.modal-warning { display: flex; align-items: flex-start; gap: 8px; color: var(--warning); }.modal-warning span { color: var(--text); }.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 900px) { .metric-grid.six { grid-template-columns: repeat(3, minmax(0, 1fr)); }.simulation-grid { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .simulation-banner, .chart-header { align-items: flex-start; flex-direction: column; }.metric-grid.six, .metric-grid.four { grid-template-columns: repeat(2, minmax(0, 1fr)); }.sample-warning { align-items: flex-start; flex-wrap: wrap; }.sample-warning small { margin-left: 26px; }.timeline-track { align-items: flex-start; flex-direction: column; }.timeline-line { width: 1px; height: 12px; flex: none; margin-left: 11px; }.execution-summary { grid-template-columns: 1fr; } }
@media (max-width: 420px) { .metric-grid.six, .metric-grid.four { grid-template-columns: 1fr; } }
</style>
