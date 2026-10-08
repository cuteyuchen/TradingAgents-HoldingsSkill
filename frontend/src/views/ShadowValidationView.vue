<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Database,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Target,
  WalletCards,
} from 'lucide-vue-next'
import { useDialog, useMessage } from 'naive-ui'

import { api } from '../api'
import ErrorState from '../components/ErrorState.vue'
import EmptyState from '../components/EmptyState.vue'
import { usePortfolioContext } from '../composables/portfolio'
import { fmtDateTime, formatCurrency, formatNumber, formatPercent, unavailableText } from '../utils/ui'
import type {
  ShadowAccount,
  ShadowDailySnapshot,
  ShadowDecision,
  ShadowDecisionDetail,
  ShadowFill,
  ShadowOrder,
  ShadowPerformance,
  ShadowValidation,
} from '../api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const working = ref(false)
const error = ref('')
const loadError = ref<unknown>(null)
const accounts = ref<ShadowAccount[]>([])
const selectedAccountId = ref<number | null>(null)
const account = ref<ShadowAccount | null>(null)
const performance = ref<ShadowPerformance | null>(null)
const validation = ref<ShadowValidation | null>(null)
const decisions = ref<ShadowDecision[]>([])
const orders = ref<ShadowOrder[]>([])
const fills = ref<ShadowFill[]>([])
const dailySnapshots = ref<ShadowDailySnapshot[]>([])
const selectedDecision = ref<ShadowDecisionDetail | null>(null)
const decisionFilter = ref('ALL')
const createOpen = ref(false)
const accountName = ref('影子验证')

const {
  portfolios,
  selectedPortfolioId,
  selectedPortfolio,
  loadPortfolios,
  setSelectedPortfolio,
} = usePortfolioContext()
const portfolioOptions = computed(() => portfolios.value.map((item) => ({ label: `${item.name} · #${item.id}`, value: item.id })))
const accountOptions = computed(() => accounts.value.map((item) => ({
  label: `${item.name} · G${item.shadow_generation} · ${accountStatusText(item.status)}`,
  value: item.id,
})))
const latestSnapshotId = computed(() => selectedPortfolio.value?.latest_snapshot_id || null)
const latestSnapshot = computed(() => dailySnapshots.value[0] || null)
const filteredDecisions = computed(() => decisionFilter.value === 'ALL'
  ? decisions.value
  : decisions.value.filter((item) => String(item.final_action).toUpperCase() === decisionFilter.value))
const selectedDecisionId = computed(() => selectedDecision.value?.id || null)
const currentGeneration = computed(() => account.value?.shadow_generation || 0)
const hasConditionalAdd = computed(() => selectedDecision.value?.selected_actions?.some((item) => String(item.action || item.recommended_action || '').toLowerCase() === 'conditional_add') || false)
const pendingOrders = computed(() => orders.value.filter((item) => ['PENDING', 'PARTIAL'].includes(item.status)))
const blockedOrders = computed(() => orders.value.filter((item) => ['BLOCKED', 'EXPIRED', 'CANCELLED', 'SUPERSEDED'].includes(item.status)))
const filledOrders = computed(() => orders.value.filter((item) => ['FILLED', 'PARTIAL'].includes(item.status)))
let mounted = false

function fmt(value?: string | null) {
  return fmtDateTime(value)
}

function shortTime(value?: string | null) {
  return value ? fmtDateTime(value).replace(/^\d{4}-\d{2}-\d{2} /, '') : '—'
}

function numberText(value: unknown, digits = 2) {
  return formatNumber(value, digits)
}

function money(value: unknown) {
  return formatCurrency(value)
}

function percent(value: unknown, digits = 1) {
  return formatPercent(value, digits)
}

function percentClass(value: unknown) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed === 0) return 'neutral'
  return parsed > 0 ? 'positive' : 'negative'
}

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
  if (['BLOCKED', 'DECISION_BLOCKED', 'SELL', 'REDUCE', 'EXIT'].includes(value)) return 'error'
  if (value === 'NO_ACTION') return 'info'
  return 'success'
}

function accountStatusText(status?: string | null) {
  const value = String(status || '').toUpperCase()
  return value === 'ACTIVE' ? '运行中' : value === 'PAUSED' ? '已暂停' : value === 'CLOSED' ? '已关闭' : value || '未知'
}

function actionText(action?: string | null) {
  const value = String(action || '').toUpperCase()
  if (['ACTION', 'NO_ACTION', 'BLOCKED', 'DATA_GAP'].includes(value)) return value
  return value || '—'
}

function basisText(value?: string | null) {
  const normalized = String(value || '').toUpperCase()
  if (normalized === 'DAILY_BAR') return 'DailyBar'
  if (normalized === 'LIVE_QUOTE') return 'Live Quote'
  return value || '—'
}

function validationStatus() {
  if (!validation.value) return 'DATA_GAP'
  if (validation.value.live_sample_days < 20) return 'INSUFFICIENT_LIVE_EVIDENCE'
  return 'OBSERVING'
}

function validationStatusText(value: string) {
  return value === 'DATA_GAP' ? 'DATA_GAP' : value === 'INSUFFICIENT_LIVE_EVIDENCE' ? '样本不足，继续观察' : '持续观察'
}

function validationOutcomeText(item: ShadowValidation['cohorts'][number]) {
  const buckets = item.outcomes_by_target_horizon || []
  if (!buckets.length) return '暂无已完成 target/horizon'
  return buckets.slice(0, 2)
    .map((bucket) => `${bucket.target_type}/${bucket.target_key} ${bucket.horizon_trading_days}D ${percent(bucket.mean_excess_return)}`)
    .join(' · ')
}

async function loadPortfolioData(portfolioId = selectedPortfolioId.value, preferredAccountId = selectedAccountId.value) {
  if (!portfolioId) return
  loading.value = true
  error.value = ''
  loadError.value = null
  try {
    const [accountRows, validationRow] = await Promise.all([
      api.listShadowAccounts(portfolioId),
      api.getShadowValidation(portfolioId),
    ])
    accounts.value = accountRows
    validation.value = validationRow
    const nextAccount = accountRows.find((item) => item.id === preferredAccountId) || accountRows.find((item) => item.status === 'ACTIVE') || accountRows[0] || null
    selectedAccountId.value = nextAccount?.id || null
    await loadAccountData(nextAccount?.id || null, portfolioId)
  } catch (err) {
    error.value = (err as Error).message
    loadError.value = err
    message.error(error.value)
  } finally {
    loading.value = false
  }
}

async function loadAccountData(accountId = selectedAccountId.value, portfolioId = selectedPortfolioId.value) {
  selectedAccountId.value = accountId
  account.value = null
  performance.value = null
  selectedDecision.value = null
  orders.value = []
  fills.value = []
  dailySnapshots.value = []
  if (!accountId || !portfolioId) {
    decisions.value = []
    return
  }
  try {
    const [accountRow, performanceRow, decisionRows, orderRows, fillRows, dailyRows] = await Promise.all([
      api.getShadowAccount(accountId),
      api.getShadowPerformance(accountId),
      api.listShadowDecisions({ account_id: accountId, limit: 80 }),
      api.listShadowOrders({ account_id: accountId, limit: 80 }),
      api.listShadowFills({ account_id: accountId, limit: 80 }),
      api.listShadowDailySnapshots({ account_id: accountId, limit: 120 }),
    ])
    account.value = accountRow
    performance.value = performanceRow
    decisions.value = decisionRows
    orders.value = orderRows
    fills.value = fillRows
    dailySnapshots.value = dailyRows
    if (decisionRows[0]) await selectDecision(decisionRows[0].id)
  } catch (err) {
    error.value = (err as Error).message
    loadError.value = err
    message.error(error.value)
  }
}

async function selectDecision(id: number) {
  try {
    selectedDecision.value = await api.getShadowDecision(id)
  } catch (err) {
    loadError.value = err
    message.error((err as Error).message)
  }
}

async function load() {
  loading.value = true
  error.value = ''
  loadError.value = null
  try {
    await loadPortfolios(true)
    const requestedPortfolioId = Number(route.query.portfolio)
    const preferredId = portfolios.value.find((item) => item.id === requestedPortfolioId)?.id
      || portfolios.value.find((item) => item.id === selectedPortfolioId.value)?.id
      || portfolios.value.find((item) => item.is_default)?.id
      || portfolios.value[0]?.id
      || null
    if (preferredId) setSelectedPortfolio(preferredId)
    await loadPortfolioData(preferredId, Number(route.query.shadow) || null)
  } catch (err) {
    error.value = (err as Error).message
    loadError.value = err
    message.error(error.value)
  } finally {
    loading.value = false
  }
}

async function changePortfolio(value: number | null) {
  setSelectedPortfolio(value)
  selectedAccountId.value = null
  await router.replace({ query: { ...route.query, portfolio: value ? String(value) : undefined, shadow: undefined } })
}

async function changeAccount(value: number | null) {
  await router.replace({ query: { ...route.query, shadow: value ? String(value) : undefined } })
  await loadAccountData(value)
}

function openCreate() {
  if (!selectedPortfolio.value) return
  if (!latestSnapshotId.value) {
    message.warning('该组合没有已确认持仓快照，无法初始化 Shadow Account')
    return
  }
  accountName.value = '影子验证'
  createOpen.value = true
}

async function createAccount() {
  if (!selectedPortfolioId.value || !latestSnapshotId.value || working.value) return
  working.value = true
  try {
    const row = await api.createShadowAccount({
      portfolio_id: selectedPortfolioId.value,
      snapshot_id: latestSnapshotId.value,
      name: accountName.value.trim() || '影子验证',
    })
    createOpen.value = false
    message.success(`Shadow Account #${row.id} 已创建`)
    await loadPortfolioData(selectedPortfolioId.value, row.id)
  } catch (err) {
    message.error((err as Error).message)
  } finally {
    working.value = false
  }
}

function toggleAccountStatus() {
  const target = account.value
  if (!target || working.value) return
  const isPaused = target.status === 'PAUSED'
  dialog.warning({
    title: isPaused ? '恢复 Shadow Account' : '暂停 Shadow Account',
    content: isPaused
      ? '恢复后，后续合格的生产决策可以继续进入 Shadow 执行链。'
      : '暂停只停止该 Shadow Account 的后续执行，不删除历史 observation、intent、fill 或 ledger。',
    positiveText: isPaused ? '确认恢复' : '确认暂停',
    negativeText: '取消',
    onPositiveClick: async () => {
      working.value = true
      try {
        account.value = isPaused
          ? await api.resumeShadowAccount(target.id)
          : await api.pauseShadowAccount(target.id)
        accounts.value = accounts.value.map((item) => item.id === target.id ? account.value! : item)
        message.success(isPaused ? 'Shadow Account 已恢复' : 'Shadow Account 已暂停')
      } catch (err) {
        message.error((err as Error).message)
      } finally {
        working.value = false
      }
    },
  })
}

function rebaseAccount() {
  if (!account.value || working.value) return
  if (!latestSnapshotId.value) {
    message.warning('当前组合没有新的已确认持仓快照')
    return
  }
  dialog.warning({
    title: '创建新的 Shadow Generation',
    content: `这会从真实组合的快照 #${latestSnapshotId.value} 创建 G${account.value.shadow_generation + 1}。旧 generation 的历史不会删除，也不会把真实账户持续同步到 Shadow。`,
    positiveText: '确认 Rebase',
    negativeText: '取消',
    onPositiveClick: async () => {
      working.value = true
      try {
        const row = await api.rebaseShadowAccount(account.value!.id, latestSnapshotId.value)
        message.success(`已切换到 Shadow Generation G${row.shadow_generation}`)
        await loadPortfolioData(selectedPortfolioId.value, row.id)
      } catch (err) {
        message.error((err as Error).message)
      } finally {
        working.value = false
      }
    },
  })
}

async function alignActual() {
  if (!selectedDecision.value || selectedDecision.value.final_action !== 'ACTION' || working.value) return
  working.value = true
  try {
    await api.alignShadowDecision(selectedDecision.value.id)
    await selectDecision(selectedDecision.value.id)
    message.success('已按 Trade Ledger 事实刷新对齐结果')
  } catch (err) {
    message.error((err as Error).message)
  } finally {
    working.value = false
  }
}

async function refresh() {
  await loadPortfolioData(selectedPortfolioId.value, selectedAccountId.value)
}

onMounted(async () => { await load(); mounted = true })
onUnmounted(() => { mounted = false })
watch(selectedPortfolioId, (value, previous) => {
  if (!mounted || value === previous) return
  selectedAccountId.value = null
  void router.replace({ query: { ...route.query, portfolio: value ? String(value) : undefined, shadow: undefined } })
  void loadPortfolioData(value, null)
})
</script>

<template>
  <section class="shadow-page">
    <header class="shadow-header">
      <div>
        <div class="shadow-eyebrow"><ShieldCheck :size="15" />LIVE DECISION VALIDATION</div>
        <h1>Shadow 验证</h1>
        <p>记录生产决策、未来可执行价格和后续结果，真实账户与 Shadow 完全隔离。</p>
      </div>
      <div class="shadow-safety" aria-label="模拟模式，不会真实下单">
        <span class="safety-kicker">SHADOW / 模拟验证</span>
        <strong>不会发送真实订单</strong>
        <small>只记录 Decision → Execution → Outcome</small>
      </div>
    </header>

    <div class="shadow-toolbar">
      <label class="toolbar-field">
        <span>生产组合</span>
        <n-select :value="selectedPortfolioId" :options="portfolioOptions" placeholder="选择组合" :disabled="loading" @update:value="changePortfolio" />
      </label>
      <label v-if="accounts.length" class="toolbar-field account-field">
        <span>Shadow Account</span>
        <n-select :value="selectedAccountId" :options="accountOptions" placeholder="选择 Shadow Account" @update:value="changeAccount" />
      </label>
      <div class="toolbar-actions">
        <n-button v-if="account" quaternary :loading="working" @click="toggleAccountStatus">
          <template #icon><Play v-if="account.status === 'PAUSED'" :size="15" /><Pause v-else :size="15" /></template>
          {{ account.status === 'PAUSED' ? '恢复' : '暂停' }}
        </n-button>
        <n-button v-if="account" quaternary :loading="working" @click="rebaseAccount">
          <template #icon><RotateCcw :size="15" /></template>
          Rebase
        </n-button>
        <n-button v-if="!account && selectedPortfolio" type="primary" :disabled="!latestSnapshotId" @click="openCreate">
          <template #icon><Target :size="15" /></template>
          创建 Shadow Account
        </n-button>
        <n-button quaternary circle aria-label="刷新 Shadow 数据" :loading="loading" @click="refresh">
          <template #icon><RefreshCw :size="16" /></template>
        </n-button>
      </div>
    </div>

    <ErrorState v-if="loadError" :error="loadError" @retry="refresh" />
    <n-alert type="info" :show-icon="true">
      Shadow 只使用决策完成后持久化的未来 Live Quote；不使用决策参考价、旧收盘价或真实账户资金模拟成交。模拟结果不代表未来收益。
    </n-alert>

    <EmptyState v-if="!loading && !portfolios.length" class="panel-card empty-panel" description="暂无生产组合，请先创建组合并确认持仓。">
      <template #action><n-button secondary size="small" @click="router.push({ name: 'upload' })">先导入持仓</n-button></template>
    </EmptyState>
    <template v-else-if="selectedPortfolio">
      <n-alert v-if="!latestSnapshotId" type="warning" :show-icon="true">
        当前组合还没有已确认持仓快照，Shadow Account 必须从一次明确的真实组合快照初始化。
      </n-alert>
      <EmptyState v-if="!loading && !account" class="panel-card empty-panel" description="当前组合还没有 Shadow Account">
        <template #action><n-button type="primary" size="small" :disabled="!latestSnapshotId" @click="openCreate">创建 Shadow Account</n-button></template>
      </EmptyState>

      <n-card v-if="account" class="panel-card account-card" :bordered="false">
        <template #header>
          <div class="card-heading"><WalletCards :size="17" /><span>{{ account.name }}</span><n-tag size="small" :type="statusType(account.status)">{{ accountStatusText(account.status) }}</n-tag><small>paper-only</small></div>
        </template>
        <div class="account-meta">
          <span>组合 {{ selectedPortfolio.name }}</span>
          <span>Generation <strong>G{{ currentGeneration }}</strong></span>
          <span>执行契约 <code>{{ account.execution_contract_version }}</code></span>
          <span>初始化快照 #{{ account.initialized_from_snapshot_id || '—' }}</span>
        </div>
        <div class="metric-grid">
          <div class="metric-cell"><span>当前现金</span><strong>{{ money(account.current_cash) }}</strong><small>Shadow cash</small></div>
          <div class="metric-cell"><span>当前净值</span><strong>{{ money(performance?.current_equity) }}</strong><small>{{ performance?.sample_days ?? unavailableText }} 个交易日</small></div>
          <div class="metric-cell"><span>累计收益</span><strong :class="percentClass(performance?.cumulative_return)">{{ percent(performance?.cumulative_return) }}</strong><small>仅基于 Shadow snapshot</small></div>
          <div class="metric-cell"><span>最大回撤</span><strong class="negative">{{ percent(performance?.max_drawdown) }}</strong><small>generation 内</small></div>
          <div class="metric-cell"><span>Benchmark</span><strong :class="percentClass(performance?.benchmark_return)">{{ percent(performance?.benchmark_return) }}</strong><small>All-A Median</small></div>
          <div class="metric-cell"><span>相对基准</span><strong :class="percentClass(performance?.excess_return)">{{ percent(performance?.excess_return) }}</strong><small>Shadow - benchmark</small></div>
          <div class="metric-cell"><span>待处理意图</span><strong>{{ pendingOrders.length }}</strong><small>{{ account.pending_intent_count }} pending / partial</small></div>
          <div class="metric-cell"><span>成交笔数</span><strong>{{ fills.length }}</strong><small>成本 {{ money(performance?.transaction_cost) }}</small></div>
          <div class="metric-cell"><span>性能质量</span><strong>{{ performance?.performance_quality || 'DATA_GAP' }}</strong><small>样本不足不会判定策略有效</small></div>
        </div>
      </n-card>

      <div v-if="account" class="shadow-grid">
        <n-card class="panel-card decision-panel" :bordered="false">
          <template #header>
            <div class="card-heading"><Target :size="17" /><span>Decision</span><small>{{ decisions.length }} observations</small></div>
          </template>
          <div class="panel-filter">
            <n-radio-group v-model:value="decisionFilter" size="small">
              <n-radio-button value="ALL">全部</n-radio-button>
              <n-radio-button value="ACTION">ACTION</n-radio-button>
              <n-radio-button value="NO_ACTION">NO_ACTION</n-radio-button>
            </n-radio-group>
          </div>
          <div v-if="filteredDecisions.length" class="decision-list">
            <button v-for="item in filteredDecisions" :key="item.id" class="decision-row" :class="{ selected: selectedDecisionId === item.id }" @click="selectDecision(item.id)">
              <span class="decision-time"><strong>{{ item.trade_date }}</strong><small>{{ item.decision_checkpoint || item.decision_kind }} · {{ shortTime(item.decision_finalized_at) }}</small></span>
              <span class="decision-main"><n-tag size="small" :type="actionType(item.final_action)">{{ actionText(item.final_action) }}</n-tag><small>{{ item.market_regime || 'regime —' }} · {{ item.quality_status }}</small></span>
            </button>
          </div>
          <n-empty v-else description="还没有可展示的 Live Decision Observation" />
          <div v-if="selectedDecision" class="decision-detail">
            <div class="detail-heading"><strong>Observation #{{ selectedDecision.id }}</strong><n-tag size="small" :type="statusType(selectedDecision.live_evidence_eligibility)">{{ selectedDecision.live_evidence_eligibility }}</n-tag></div>
            <div class="detail-grid">
              <span><small>最终动作</small><strong>{{ actionText(selectedDecision.final_action) }}</strong></span>
              <span><small>完成时间</small><strong>{{ fmt(selectedDecision.decision_finalized_at) }}</strong></span>
              <span><small>市场质量</small><strong>{{ selectedDecision.market_quality || '—' }}</strong></span>
              <span><small>组合质量</small><strong>{{ selectedDecision.portfolio_quality || '—' }}</strong></span>
              <span><small>参数版本</small><strong>{{ selectedDecision.parameter_set_version || '—' }}</strong></span>
              <span><small>模型</small><strong>{{ selectedDecision.model_name || '—' }}</strong></span>
            </div>
            <div class="lineage-line"><Database :size="14" /><span>hash</span><code>{{ selectedDecision.observation_hash }}</code></div>
            <div v-if="selectedDecision.reason_codes?.length" class="reason-list">
              <span v-for="reason in selectedDecision.reason_codes" :key="reason">{{ reason }}</span>
            </div>
            <div class="detail-actions">
              <n-button v-if="selectedDecision.final_action === 'ACTION'" quaternary size="small" :loading="working" @click="alignActual">
                <template #icon><ArrowUpFromLine :size="14" /></template>
                刷新实际动作对齐
              </n-button>
            </div>
            <n-alert v-if="hasConditionalAdd" type="warning" :show-icon="false">
              条件加仓仅记录建议，V1 暂不模拟条件触发成交。
            </n-alert>
          </div>
        </n-card>

        <n-card class="panel-card execution-panel" :bordered="false">
          <template #header>
            <div class="card-heading"><Activity :size="17" /><span>Execution</span><small>Intent / Fill 分离</small></div>
          </template>
          <div class="execution-summary">
            <div><span>待处理</span><strong>{{ pendingOrders.length }}</strong></div>
            <div><span>已成交</span><strong>{{ filledOrders.length }}</strong></div>
            <div><span>未成交/阻断</span><strong>{{ blockedOrders.length }}</strong></div>
          </div>
          <div class="subheading"><Clock3 :size="14" /><span>Order Intent</span></div>
          <div v-if="orders.length" class="fact-list">
            <div v-for="item in orders.slice(0, 8)" :key="item.id" class="fact-row">
              <div><strong>{{ item.side }} {{ item.code }}</strong><small>G{{ item.shadow_generation }} · {{ fmt(item.created_at) }}</small></div>
              <div class="fact-right"><n-tag size="small" :type="statusType(item.status)">{{ item.status }}</n-tag><small>最早 {{ fmt(item.earliest_executable_at) }}</small></div>
            </div>
          </div>
          <n-empty v-else description="没有 Shadow Order Intent" />
          <div class="subheading"><CheckCircle2 :size="14" /><span>Paper Fill</span><small>slippage not modeled</small></div>
          <div v-if="fills.length" class="fact-list">
            <div v-for="item in fills.slice(0, 6)" :key="item.id" class="fact-row">
              <div><strong>{{ item.side }} {{ item.code }} · {{ numberText(item.quantity, 0) }} 股</strong><small>{{ fmt(item.fill_at) }} · {{ basisText(item.price_basis) }}</small></div>
              <div class="fact-right"><strong>{{ money(item.price) }}</strong><small>延迟 {{ numberText(item.execution_delay_seconds, 0) }}s</small></div>
            </div>
          </div>
          <n-empty v-else description="还没有 Paper Fill" />
        </n-card>

        <n-card class="panel-card outcome-panel" :bordered="false">
          <template #header>
            <div class="card-heading"><ArrowDownToLine :size="17" /><span>Outcome</span><small>1 / 5 / 10 / 20 / 60D</small></div>
          </template>
          <div v-if="selectedDecision?.outcomes?.length" class="outcome-list">
            <div v-for="item in selectedDecision.outcomes" :key="`${item.target_type}-${item.target_key}-${item.horizon_trading_days}`" class="outcome-row">
              <div><strong>{{ item.target_type }} · {{ item.target_key }}</strong><small>{{ item.horizon_trading_days }}D · {{ item.status }} · {{ item.quality_status }}</small></div>
              <div class="outcome-values"><strong :class="percentClass(item.forward_return)">{{ percent(item.forward_return) }}</strong><span :class="percentClass(item.excess_return)">超额 {{ percent(item.excess_return) }}</span></div>
            </div>
          </div>
          <n-empty v-else description="选择一条 observation 查看 Outcome" />
          <div v-if="selectedDecision" class="outcome-foot">
            <span>执行资格 <n-tag size="small" :type="selectedDecision.execution?.intents?.length ? 'success' : 'info'">{{ selectedDecision.execution?.intents?.length ? '已生成 Intent' : 'NO_ACTION / 未生成' }}
            </n-tag></span>
            <span>实际对齐 {{ selectedDecision.actual_alignment?.length || 0 }} 条</span>
          </div>
        </n-card>
      </div>

      <div v-if="account" class="shadow-grid shadow-grid-bottom">
        <n-card class="panel-card validation-panel" :bordered="false">
          <template #header>
            <div class="card-heading"><ShieldCheck :size="17" /><span>Validation Cohorts</span><small>{{ validationStatusText(validationStatus()) }}</small></div>
          </template>
          <div class="validation-kpis">
            <div><span>Live sample days</span><strong>{{ validation?.live_sample_days ?? unavailableText }}</strong></div>
            <div><span>Decision count</span><strong>{{ validation?.decision_count ?? unavailableText }}</strong></div>
            <div><span>Action rate</span><strong>{{ validation?.decision_count ? percent((validation.cohorts.reduce((sum, item) => sum + item.action_count, 0)) / validation.decision_count) : '—' }}</strong></div>
            <div><span>Backtest 混入</span><strong>否</strong></div>
          </div>
          <div v-if="validation?.cohorts?.length" class="cohort-list">
            <div v-for="item in validation.cohorts.slice(0, 6)" :key="JSON.stringify(item.cohort)" class="cohort-row">
              <div><strong>{{ item.cohort.parameter_set_hash ? String(item.cohort.parameter_set_hash).slice(0, 12) : 'UNKNOWN' }}</strong><small>G{{ item.cohort.shadow_generation || '—' }} · {{ item.sample_days }} days · N={{ item.decision_count }}</small></div>
              <div class="fact-right"><n-tag size="small" :type="statusType(item.evidence_status)">{{ item.evidence_status }}</n-tag><small>{{ validationOutcomeText(item) }}</small></div>
            </div>
          </div>
          <n-empty v-else description="暂无有效 Live Evidence，等待真实交易日积累证据" />
          <div v-if="validation?.limitations?.length" class="limitations">
            <span v-for="item in validation.limitations" :key="item"><AlertTriangle :size="13" />{{ item }}</span>
          </div>
        </n-card>

        <n-card class="panel-card daily-panel" :bordered="false">
          <template #header>
            <div class="card-heading"><CalendarDays :size="17" /><span>Daily Shadow Snapshot</span><small>{{ dailySnapshots.length }} days</small></div>
          </template>
          <div v-if="dailySnapshots.length" class="daily-list">
            <div v-for="item in dailySnapshots.slice(0, 7)" :key="item.id" class="daily-row">
              <div><strong>{{ item.trade_date }}</strong><small>{{ item.position_count }} positions · {{ item.price_basis || 'basis —' }}</small></div>
              <div class="daily-values"><strong>{{ money(item.total_equity) }}</strong><span :class="percentClass(item.daily_return)">{{ percent(item.daily_return) }}</span></div>
            </div>
          </div>
          <n-empty v-else description="还没有收盘估值快照" />
          <div v-if="latestSnapshot" class="daily-foot"><span>最近快照 {{ latestSnapshot.trade_date }}</span><span>现金 {{ money(latestSnapshot.cash) }}</span><span>回撤 {{ percent(latestSnapshot.drawdown) }}</span></div>
        </n-card>
      </div>
    </template>

    <n-modal v-model:show="createOpen" preset="card" title="创建 Shadow Account" style="width: min(520px, 94vw)">
      <div class="modal-copy">
        <div class="modal-warning"><AlertTriangle :size="17" /><span>只会复制一次真实组合快照，之后 Shadow 独立演化。</span></div>
        <p>组合：<strong>{{ selectedPortfolio?.name }}</strong> · 初始化快照 #{{ latestSnapshotId }}</p>
        <label>账户名称<n-input v-model:value="accountName" maxlength="128" /></label>
        <div class="modal-facts"><span><WalletCards :size="14" />纸面模式</span><span><Database :size="14" />独立现金与持仓</span><span><ShieldCheck :size="14" />不会真实下单</span></div>
      </div>
      <template #footer>
        <div class="modal-actions"><n-button @click="createOpen = false">取消</n-button><n-button type="primary" :loading="working" @click="createAccount">确认创建</n-button></div>
      </template>
    </n-modal>
  </section>
</template>

<style scoped>
.shadow-page { display: grid; gap: 18px; min-width: 0; }
.shadow-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; padding: 8px 2px 2px; }
.shadow-eyebrow { display: inline-flex; align-items: center; gap: 7px; color: var(--app-primary); font-size: 11px; font-weight: 800; letter-spacing: .12em; }
h1 { margin: 7px 0 4px; font-size: 34px; line-height: 1.12; letter-spacing: 0; }
.shadow-header p { margin: 0; color: var(--app-text-muted); }
.shadow-safety { display: grid; grid-template-columns: auto auto; align-items: center; gap: 0 8px; min-width: 180px; border: 1px solid color-mix(in srgb, var(--app-warning) 45%, var(--app-border)); border-radius: 8px; background: color-mix(in srgb, var(--app-warning) 10%, var(--app-surface)); padding: 12px 15px; }
.shadow-safety strong { color: var(--app-warning); font-size: 16px; letter-spacing: .08em; }
.shadow-safety small { grid-column: 1 / -1; color: var(--app-text-muted); font-size: 11px; }
.safety-kicker { color: var(--app-warning); font-size: 11px; font-weight: 800; }
.shadow-toolbar { display: flex; align-items: flex-end; gap: 12px; border: 1px solid var(--app-border); border-radius: 8px; background: var(--app-surface); padding: 12px; box-shadow: var(--app-shadow); }
.toolbar-field { display: grid; flex: 0 1 260px; gap: 6px; color: var(--app-text-muted); font-size: 11px; font-weight: 800; }
.account-field { flex-basis: 300px; }
.toolbar-actions { display: flex; align-items: center; justify-content: flex-end; gap: 6px; margin-left: auto; }
.empty-panel { min-height: 230px; display: grid; place-items: center; }
.panel-card { min-width: 0; }
.card-heading { display: flex; align-items: center; gap: 8px; min-width: 0; font-weight: 800; }
.card-heading small { margin-left: auto; color: var(--app-text-muted); font-size: 11px; font-weight: 600; }
.account-meta { display: flex; flex-wrap: wrap; gap: 6px 16px; color: var(--app-text-muted); font-size: 11px; }
.account-meta strong, .account-meta code { color: var(--app-text); }
.metric-grid { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 8px; margin-top: 18px; }
.metric-cell { display: grid; gap: 4px; min-width: 0; border-left: 2px solid var(--app-primary-soft); padding: 0 8px; }
.metric-cell span, .metric-cell small { color: var(--app-text-muted); font-size: 11px; }
.metric-cell strong { overflow-wrap: anywhere; font-size: 16px; }
.metric-cell small { line-height: 1.35; }
.positive { color: var(--app-success); }
.negative { color: var(--app-danger); }
.neutral { color: var(--app-text); }
.shadow-grid { display: grid; grid-template-columns: minmax(0, 1.04fr) minmax(0, .96fr) minmax(0, 1fr); gap: 18px; align-items: start; }
.shadow-grid-bottom { grid-template-columns: minmax(0, 1.18fr) minmax(0, .82fr); }
.panel-filter { margin-bottom: 11px; }
.decision-list, .fact-list, .outcome-list, .cohort-list, .daily-list { display: grid; gap: 1px; }
.decision-row, .fact-row, .outcome-row, .cohort-row, .daily-row { min-width: 0; border-bottom: 1px solid var(--app-border-soft); }
.decision-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px; width: 100%; border-top: 0; border-right: 0; border-left: 0; padding: 10px 7px; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.decision-row:hover, .decision-row.selected { background: var(--app-row-hover); }
.decision-time, .decision-main, .fact-row > div:first-child, .cohort-row > div:first-child, .daily-row > div:first-child { display: grid; gap: 2px; min-width: 0; }
.decision-time small, .decision-main small, .fact-row small, .cohort-row small, .daily-row small { overflow: hidden; color: var(--app-text-muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.decision-main { justify-items: end; }
.decision-detail { display: grid; gap: 12px; margin-top: 16px; border-top: 1px solid var(--app-border-soft); padding-top: 14px; }
.detail-heading, .detail-actions, .lineage-line, .outcome-foot, .daily-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.detail-grid span { display: grid; gap: 3px; min-width: 0; }
.detail-grid small { color: var(--app-text-muted); font-size: 10px; }
.detail-grid strong { overflow-wrap: anywhere; font-size: 12px; }
.lineage-line { justify-content: flex-start; color: var(--app-text-muted); font-size: 11px; }
.lineage-line code { min-width: 0; overflow: hidden; color: var(--app-text); text-overflow: ellipsis; }
.reason-list { display: flex; flex-wrap: wrap; gap: 5px; }
.reason-list span { border-radius: 5px; background: var(--app-primary-soft); padding: 3px 6px; color: var(--app-primary); font-size: 10px; }
.execution-summary, .validation-kpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 17px; }
.validation-kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.execution-summary div, .validation-kpis div { display: grid; gap: 3px; border-left: 2px solid var(--app-primary-soft); padding-left: 8px; }
.execution-summary span, .validation-kpis span { color: var(--app-text-muted); font-size: 10px; }
.execution-summary strong, .validation-kpis strong { font-size: 16px; }
.subheading { display: flex; align-items: center; gap: 6px; margin: 13px 0 6px; color: var(--app-text-muted); font-size: 11px; font-weight: 800; }
.subheading small { margin-left: auto; font-weight: 500; }
.fact-row, .outcome-row, .cohort-row, .daily-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 0; }
.fact-right, .outcome-values, .daily-values { display: grid; justify-items: end; gap: 2px; min-width: 0; text-align: right; }
.outcome-row strong, .daily-row strong { font-size: 12px; }
.outcome-values span, .daily-values span { font-size: 11px; }
.outcome-foot { flex-wrap: wrap; margin-top: 12px; color: var(--app-text-muted); font-size: 11px; }
.limitations { display: grid; gap: 5px; margin-top: 14px; color: var(--app-warning); font-size: 11px; }
.limitations span { display: flex; align-items: flex-start; gap: 5px; }
.daily-foot { flex-wrap: wrap; margin-top: 13px; color: var(--app-text-muted); font-size: 11px; }
.modal-copy { display: grid; gap: 13px; }
.modal-copy p { margin: 0; color: var(--app-text-muted); font-size: 12px; }
.modal-copy label { display: grid; gap: 6px; color: var(--app-text-muted); font-size: 12px; font-weight: 700; }
.modal-warning { display: flex; align-items: center; gap: 7px; border-radius: 6px; background: color-mix(in srgb, var(--app-warning) 11%, transparent); padding: 9px 10px; color: var(--app-warning); font-size: 12px; font-weight: 700; }
.modal-facts { display: flex; flex-wrap: wrap; gap: 7px 13px; color: var(--app-text-muted); font-size: 11px; }
.modal-facts span { display: inline-flex; align-items: center; gap: 5px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 1180px) {
  .metric-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .shadow-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .decision-panel { grid-row: span 2; }
}
@media (max-width: 780px) {
  .shadow-header { align-items: flex-start; flex-direction: column; }
  .shadow-safety { width: 100%; min-width: 0; }
  .shadow-toolbar { align-items: stretch; flex-direction: column; }
  .toolbar-field, .account-field { flex-basis: auto; }
  .toolbar-actions { justify-content: flex-start; margin-left: 0; }
  .shadow-grid, .shadow-grid-bottom { grid-template-columns: 1fr; }
  .decision-panel { grid-row: auto; }
}
@media (max-width: 520px) {
  h1 { font-size: 29px; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .validation-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .toolbar-actions { flex-wrap: wrap; }
}
</style>
