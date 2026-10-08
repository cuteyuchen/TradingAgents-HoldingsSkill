<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, errorMessage } from '../api'
import type { TradeLedgerEntry } from '../api/types'
import SectionCard from './SectionCard.vue'
import EmptyState from './EmptyState.vue'
import TradeLedgerDrawer from './TradeLedgerDrawer.vue'
import TechnicalDetails from './TechnicalDetails.vue'
import { decisionLabel } from '../utils/decision'
import { fmtDateTime } from '../utils/ui'

const props = defineProps<{ portfolioId: number; accountVersion?: string | null }>()
const emit = defineEmits<{ changed: [] }>()
const plan = ref<Record<string, any> | null>(null)
const ledger = ref<TradeLedgerEntry[]>([])
const selections = ref<Record<number, number | null>>({})
const error = ref('')
const busy = ref(false)
const drawerOpen = ref(false)
let sequence = 0
let controller: AbortController | null = null
const actionNames: Record<string, string> = { hold: '持有', buy: '买入', add: '加仓', add_existing: '加仓', new_position: '新开仓', reduce: '减仓', sell: '卖出', exit: '清仓', watch: '条件观察', conditional_add: '条件加仓' }
const actionName = (value: unknown) => actionNames[String(value || '').toLowerCase()] || String(value || '待判断')
const valueText = (value: any) => typeof value === 'string' ? value : value ? JSON.stringify(value) : '无明确条件'
const stateSummary = computed(() => {
  const counts = plan.value?.summary
  if (!counts || typeof counts !== 'object') return ''
  const parts = Object.entries(counts as Record<string, unknown>)
    .filter(([, count]) => Number(count) > 0)
    .map(([state, count]) => `${decisionLabel(state)} ${count} 项`)
  return parts.length ? `本次计划：${parts.join(' · ')}` : ''
})

async function load() {
  const current = ++sequence
  const portfolioId = props.portfolioId
  controller?.abort()
  controller = new AbortController()
  error.value = ''
  try {
    const [next, entries] = await Promise.all([api.getDailyPlan(portfolioId, controller.signal), api.listLedgerEntries(portfolioId)])
    if (current === sequence && portfolioId === props.portfolioId) { plan.value = next; ledger.value = entries }
  } catch (reason) { if (current === sequence) error.value = errorMessage(reason) }
}

async function mutate(action: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await action(); await load(); emit('changed') }
  catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = false }
}
function entriesFor(action: Record<string, any>) {
  return ledger.value.filter(entry => entry.security_code === action.code && entry.side?.toUpperCase() === String(action.side || '').toUpperCase() && entry.status.toUpperCase() === 'CONFIRMED')
    .map(entry => ({ value: entry.id, label: `${fmtDateTime(entry.executed_at)} · ${entry.quantity} 股 · ${entry.price} 元` }))
}
async function ledgerChanged() { await load(); emit('changed') }
watch(() => [props.portfolioId, props.accountVersion], () => { plan.value = null; selections.value = {}; void load() }, { immediate: true })
onUnmounted(() => { sequence++; controller?.abort() })
</script>

<template>
  <SectionCard title="今日行动计划" description="先核对条件，再记录实际成交；计划会保留已成交部分并复核剩余数量。">
    <template #actions>
      <q-btn size="sm" outline :loading="busy" @click="mutate(() => api.refreshDailyPlan(portfolioId))">同步最新分析</q-btn>
      <q-btn size="sm" color="primary" @click="drawerOpen = true">记录实际成交</q-btn>
    </template>
    <q-banner v-if="error" role="alert">{{ error }}<q-btn flat @click="load">重试</q-btn></q-banner>
    <p v-if="stateSummary" class="summary">{{ stateSummary }}</p>
    <div v-if="plan?.actions?.length" class="plan-list">
      <article v-for="action in plan.actions" :key="action.plan_id || action.code" class="plan-row">
        <div class="plan-heading"><strong>{{ action.name || action.code }} <small>{{ action.code }}</small></strong><q-badge outline color="primary">{{ actionName(action.action) }} · {{ decisionLabel(action.decision_status) }}</q-badge></div>
        <div class="quantities"><span>计划 {{ action.planned_quantity ?? '—' }} 股</span><span>已成交 {{ action.filled_quantity ?? 0 }} 股</span><span>剩余 {{ action.remaining_quantity ?? '—' }} 股</span><strong>当前可执行 {{ action.executable_quantity ?? 0 }} 股</strong></div>
        <p>条件：{{ valueText(action.condition) }}</p>
        <p v-if="action.depends_on_plan_ids?.length">先完成关联卖出并确认可用资金，再复核买入。</p>
        <p v-if="action.expires_at">有效至 {{ fmtDateTime(action.expires_at) }}</p>
        <p v-if="action.reason_codes?.length" class="muted">{{ action.reason_codes.join(' · ') }}</p>
        <div v-if="action.plan_id" class="plan-controls">
          <q-btn size="sm" outline :disable="busy" @click="mutate(() => api.recheckDailyAction(action.plan_id))">复核剩余计划</q-btn>
          <template v-if="entriesFor(action).length"><select v-model="selections[action.plan_id]" :aria-label="`关联 ${action.code} 成交`"><option :value="null" disabled>选择已记录成交</option><option v-for="entry in entriesFor(action)" :key="entry.value" :value="entry.value">{{ entry.label }}</option></select><q-btn size="sm" outline :disable="busy || !selections[action.plan_id]" @click="mutate(() => api.linkDailyActionFill(action.plan_id, selections[action.plan_id]!))">关联成交</q-btn></template>
        </div>
      </article>
    </div>
    <EmptyState v-else-if="!error" :title="plan ? decisionLabel(plan.decision_status) : '正在读取行动计划'" description="完成分析后可同步计划；没有明确结论时保留待分析状态。" />
    <TechnicalDetails v-if="plan?.comparisons?.length" native title="持有、现金、加仓与新机会比较"><div v-for="(item, index) in plan.comparisons" :key="index" class="comparison"><strong>{{ item.label || item.name || actionName(item.alternative || item.action || item.option) }}</strong><p>{{ item.reason || item.summary || item.rationale || valueText(item) }}</p></div></TechnicalDetails>
    <TradeLedgerDrawer v-if="drawerOpen" v-model:show="drawerOpen" :portfolio-id="portfolioId" @changed="ledgerChanged" />
  </SectionCard>
</template>

<style scoped>
select { min-width: 0; max-width: 100%; color: var(--v3-text); background: var(--v3-surface); border: 1px solid var(--v3-border); padding: 6px; border-radius: 6px; }
.summary { margin: 0 0 12px; color: var(--text-muted); }.plan-list { display: grid; gap: 14px; }.plan-row { border-top: 1px solid var(--border); padding-top: 14px; min-width: 0; }.plan-heading, .quantities, .plan-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px; }.plan-heading { justify-content: space-between; }.plan-heading small, .muted { color: var(--text-muted); font-weight: normal; }.quantities { margin-top: 12px; font-size: 12px; }.plan-row p, .comparison p { color: var(--text-muted); margin: 8px 0; overflow-wrap: anywhere; }.plan-controls { margin-top: 12px; }.plan-controls .n-select { width: min(310px, 100%); }.comparison { padding-top: 10px; }
</style>
