<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, errorMessage } from '../api'
import SectionCard from './SectionCard.vue'
import MetricTile from './MetricTile.vue'
import EmptyState from './EmptyState.vue'
import TechnicalDetails from './TechnicalDetails.vue'
import { fmtDateTime, formatCurrency, formatPercent } from '../utils/ui'

const props = defineProps<{ portfolioId: number; compact?: boolean; accountVersion?: string | null }>()
const review = ref<Record<string, any> | null>(null)
const error = ref('')
const busy = ref(false)
let sequence = 0
let controller: AbortController | null = null
const account = computed(() => review.value?.account_return || {})
const hypotheses = computed<Record<string, any>[]>(() => review.value?.learning?.hypotheses || [])
const money = (value: unknown) => value == null ? '待补齐' : formatCurrency(Number(value), 2)
const percent = (value: unknown) => value == null ? '待补齐' : formatPercent(Number(value))
const actions: Record<string, string> = { hold: '持有', buy: '买入', add: '加仓', reduce: '减仓', sell: '卖出', watch: '等待条件', no_action: '无需操作', new_position: '新开仓' }
const action = (value: unknown) => actions[String(value || '').toLowerCase()] || String(value || '未形成结论')
const learningStatus = (value: unknown) => ({ PENDING: '待验证', REFERENCE: '可参考', VERIFIED: '已验证', DISABLED: '已停用', PROPOSED: '待验证' } as Record<string, string>)[String(value || '').toUpperCase()] || '待验证'
const hypothesisTitle = (item: Record<string, any>) => item.statement || item.conclusion || item.summary || item.title || item.rule_key || '经验条目'
const scopeText = (item: Record<string, any>) => {
  if (item.scope_summary || item.description) return item.scope_summary || item.description
  const scope = item.scope
  if (scope && typeof scope === 'object') {
    const parts = [
      scope.market_regime && scope.market_regime !== 'UNKNOWN' ? `市场 ${scope.market_regime}` : '',
      scope.security_type ? `标的 ${scope.security_type}` : '',
      scope.action ? `动作 ${scope.action}` : '',
      scope.horizon ? `${scope.horizon} 个交易日` : '',
    ].filter(Boolean)
    if (parts.length) return parts.join(' · ')
  }
  return '按适用市场、标的与角色筛选引用。'
}
async function load() {
  const current = ++sequence
  const id = props.portfolioId
  controller?.abort()
  controller = new AbortController()
  error.value = ''
  try { const data = await api.getAccountReview(id, controller.signal); if (current === sequence && id === props.portfolioId) review.value = data }
  catch (reason) { if (current === sequence) error.value = errorMessage(reason) }
}
async function refreshLearning() {
  if (busy.value) return
  busy.value = true
  try { await api.refreshLearning(props.portfolioId); await load() }
  catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = false }
}
watch(() => [props.portfolioId, props.accountVersion], () => { review.value = null; void load() }, { immediate: true })
onUnmounted(() => { sequence++; controller?.abort() })
</script>

<template>
  <SectionCard :title="compact ? '昨日执行与今日变化' : '真实账户与建议复盘'" :description="review ? `${review.previous_trade_date || '上一交易日'} → ${review.trade_date} · 截至 ${fmtDateTime(review.as_of)}` : '根据已确认持仓与成交核算，缺少事实时保留待补齐状态。'">
    <template #actions><n-button size="small" secondary @click="load">刷新复盘</n-button><n-button v-if="!compact" size="small" :loading="busy" @click="refreshLearning">评估到期建议</n-button></template>
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
    <template v-else-if="review">
      <div class="review-metrics"><MetricTile label="当日账户净收益" :value="money(account.net_pnl)" helper="剔除出入金" /><MetricTile label="当日收益率" :value="percent(account.return_rate)" /><MetricTile label="已实现盈亏" :value="money(account.realized_pnl)" helper="自成本基准起，移动加权成本" /><MetricTile label="未实现盈亏" :value="money(account.unrealized_pnl)" /></div>
      <p v-if="account.status !== 'COMPLETE' && account.status !== 'READY' && account.status !== 'VALID'" class="review-note">账户资料尚不完整，缺失指标保持待补齐。{{ (account.reason_codes || []).join(' · ') }}</p>
      <div v-if="review.day_comparison?.changes?.length" class="change-list"><article v-for="item in review.day_comparison.changes" :key="item.code"><strong>{{ item.code }}</strong><span>{{ action(item.previous_action) }} → {{ action(item.action) }}</span><span>{{ item.previous_quantity ?? '—' }} → {{ item.quantity ?? '—' }} 股</span></article></div>
      <EmptyState v-else title="暂未形成昨日与今日的可比记录" description="持续记录每日建议和实际成交，系统将显示变更及对应依据。" />
      <template v-if="!compact">
        <div class="review-metrics secondary-metrics"><MetricTile label="交易费用" :value="money(account.fees)" /><MetricTile label="税费" :value="money(account.taxes)" /><MetricTile label="股息" :value="money(account.dividends)" /><MetricTile label="转入 / 转出" :value="`${money(account.cash_in)} / ${money(account.cash_out)}`" /></div>
        <h3>建议效果</h3><p class="review-note">建议后的市场表现单独评估；未成交不计入真实收益，条件未触发不直接视为预测失败。</p>
        <div v-if="review.recommendation_effect?.items?.length" class="outcome-list"><article v-for="(item, index) in review.recommendation_effect.items" :key="item.id || index"><strong>{{ item.code || item.target_key || '组合' }}</strong><span>{{ item.horizon_days || item.horizon || '—' }} 交易日</span><span>{{ percent(item.direction_adjusted_return ?? item.raw_return ?? item.market_return) }}</span><span>{{ item.status || item.quality_status || '等待评估' }}</span></article></div><p v-else class="review-note">暂无到期且数据完整的建议样本。</p>
        <h3>自学习经验</h3><p class="review-note">经验通过后续独立样本检验后调整参考状态。经验不会自动修改模型权重和仓位上限。</p>
        <article v-for="item in hypotheses" :key="item.id || item.hypothesis_id" class="learning-item"><div><strong>{{ hypothesisTitle(item) }}</strong><n-tag size="small" :bordered="false">{{ learningStatus(item.status) }}</n-tag></div><p>{{ scopeText(item) }}</p><small>版本 {{ item.version ?? '—' }} · 可用时间 {{ item.available_at ? fmtDateTime(item.available_at) : '待确认' }}</small><TechnicalDetails title="支持证据、反例与检验结果"><pre>{{ JSON.stringify(item, null, 2) }}</pre></TechnicalDetails></article>
        <p v-if="!hypotheses.length" class="review-note">暂无可用经验，继续积累复盘样本。</p>
        <TechnicalDetails title="模拟对照、复盘维度与账户核算明细"><pre>{{ JSON.stringify({ account, simulation: review.simulation_comparison, dimensions: review.review_dimensions, weekly: review.weekly_review }, null, 2) }}</pre></TechnicalDetails>
      </template>
    </template>
    <p v-else>正在读取账户与复盘记录…</p>
  </SectionCard>
</template>

<style scoped>
.review-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; }.secondary-metrics { border-top: 1px solid var(--border); padding-top: 18px; margin-top: 18px; }.review-note { color: var(--text-muted); line-height: 1.6; overflow-wrap: anywhere; }.change-list article, .outcome-list article { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 18px; padding: 10px 0; border-bottom: 1px solid var(--border); }.learning-item { border-top: 1px solid var(--border); padding: 14px 0; }.learning-item > div { display: flex; justify-content: space-between; gap: 14px; }.learning-item p, .learning-item small { color: var(--text-muted); }.learning-item strong { overflow-wrap: anywhere; }pre { max-height: 360px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }h3 { margin-top: 24px; }@media(max-width: 700px) { .review-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }@media(max-width: 380px) { .review-metrics { grid-template-columns: 1fr; } }
</style>
