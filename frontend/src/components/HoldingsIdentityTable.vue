<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { Trash2 } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api, errorMessage } from '../api'
import type { Holding } from '../api/types'

const props = defineProps<{ holdings: Holding[]; portfolioId?: number | null }>()
const emit = defineEmits<{ remove: [index: number] }>()
const message = useMessage()
const resolving = ref<Record<number, boolean>>({})
const candidateIndex = ref<number | null>(null)
const timers = new Map<number, number>()

const candidateHolding = computed(() => candidateIndex.value === null ? null : props.holdings[candidateIndex.value] || null)
const candidateModalOpen = computed({
  get: () => candidateIndex.value !== null,
  set: (value: boolean) => { if (!value) candidateIndex.value = null },
})
const candidateRows = computed(() => {
  const raw = candidateHolding.value?.extra?.identity_candidates
  return Array.isArray(raw) ? raw as Array<Record<string, any>> : []
})

function statusOf(holding: Holding): 'RESOLVED' | 'AMBIGUOUS' | 'UNRESOLVED' | 'INVALID' {
  const status = String(holding.resolution_status || 'UNRESOLVED').toUpperCase()
  return status === 'RESOLVED' || status === 'AMBIGUOUS' || status === 'INVALID' ? status : 'UNRESOLVED'
}

function statusLabel(holding: Holding): string {
  const base = ({ RESOLVED: '已匹配', AMBIGUOUS: '需要选择', UNRESOLVED: '未找到', INVALID: '代码无效' } as Record<string, string>)[statusOf(holding)]
  if (statusOf(holding) !== 'RESOLVED') return base
  const source = String(holding.resolution_source || '')
  const suffix = source.startsWith('portfolio_history')
    ? '历史'
    : source.includes('fuyao')
      ? '行情核验'
      : source.includes('ranked') || source.includes('exact') || source.includes('direct_code')
        ? '证券库'
        : ''
  return suffix ? `${base} · ${suffix}` : base
}

function statusType(holding: Holding): 'success' | 'warning' | 'info' | 'error' {
  return ({ RESOLVED: 'success', AMBIGUOUS: 'warning', UNRESOLVED: 'info', INVALID: 'error' } as const)[statusOf(holding)]
}

function isResolved(holding: Holding): boolean {
  return statusOf(holding) === 'RESOLVED' && Boolean(holding.canonical_code && holding.security_id)
}

function displayNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
}

function invalidate(holding: Holding, options: { clearName?: boolean } = {}) {
  const clearName = options.clearName === true
  const extra = { ...(holding.extra || {}) }
  holding.canonical_code = null
  holding.display_name = clearName ? null : holding.display_name
  holding.asset_type = null
  holding.exchange = null
  holding.security_id = null
  holding.resolution_status = 'UNRESOLVED'
  holding.resolution_source = null
  holding.resolution_confidence = null
  extra.resolution_status = 'UNRESOLVED'
  delete extra.identity_candidates
  delete extra.identity_error
  delete extra.submitted_canonical_code
  delete extra.canonical_code
  delete extra.security_id
  delete extra.asset_type
  delete extra.exchange
  delete extra.code
  delete extra.display_name
  delete extra.name
  if (holding.code) extra.submitted_code = holding.code
  else delete extra.submitted_code
  holding.extra = extra
}

function clearTimer(index: number) {
  const timer = timers.get(index)
  if (timer !== undefined) window.clearTimeout(timer)
  timers.delete(index)
}

function scheduleResolve(index: number, delay = 400) {
  clearTimer(index)
  const timer = window.setTimeout(() => { void resolve(index) }, delay)
  timers.set(index, timer)
}

function updateCode(index: number, value: string) {
  const holding = props.holdings[index]
  if (!holding) return
  holding.code = value
  invalidate(holding)
  scheduleResolve(index)
}

function updateName(index: number, value: string) {
  const holding = props.holdings[index]
  if (!holding) return
  holding.name = value
  holding.display_name = value
  invalidate(holding, { clearName: false })
}

async function resolve(index: number) {
  clearTimer(index)
  const holding = props.holdings[index]
  if (!holding || resolving.value[index]) return
  if (!holding.code.trim() && !(holding.name || '').trim()) {
    invalidate(holding, { clearName: false })
    return
  }
  resolving.value = { ...resolving.value, [index]: true }
  try {
    const resolved = await api.resolveHolding(
      { ...holding, extra: { ...(holding.extra || {}), submitted_code: holding.code || undefined } },
      props.portfolioId,
    )
    Object.assign(holding, resolved)
  } catch (error) {
    invalidate(holding, { clearName: false })
    const detail = errorMessage(error)
    if (detail.includes('证券查询暂不可用') || detail.includes('后端服务暂时不可用')) {
      message.warning('证券查询暂不可用，你可以稍后重试或手动输入代码')
    } else {
      message.error(detail)
    }
  } finally {
    const next = { ...resolving.value }
    delete next[index]
    resolving.value = next
  }
}

async function rematch(index: number) {
  const holding = props.holdings[index]
  if (!holding || resolving.value[index]) return
  const ocrName = typeof holding.extra?.ocr_name === 'string' ? holding.extra.ocr_name : ''
  holding.code = ''
  holding.name = holding.name || ocrName || ''
  holding.display_name = holding.display_name || holding.name || ''
  invalidate(holding, { clearName: false })
  await resolve(index)
}

function openCandidates(index: number) {
  candidateIndex.value = index
}

function selectCandidate(candidate: Record<string, any>) {
  const holding = candidateHolding.value
  if (!holding) return
  const name = String(candidate.display_name || candidate.name || '').trim()
  const code = String(candidate.code || '').trim()
  const canonical = String(candidate.canonical_code || '').trim()
  const extra = { ...(holding.extra || {}), ...candidate }
  extra.resolution_status = 'RESOLVED'
  extra.resolution_source = 'user_selected'
  extra.resolution_confidence = 1
  delete extra.identity_candidates
  holding.code = code
  holding.canonical_code = canonical || null
  holding.name = name || holding.name
  holding.display_name = name || holding.name
  holding.asset_type = String(candidate.asset_type || candidate.security_type || '').toUpperCase() || null
  holding.exchange = candidate.exchange || null
  holding.security_id = Number(candidate.security_id || candidate.id) || null
  holding.resolution_status = 'RESOLVED'
  holding.resolution_source = 'user_selected'
  holding.resolution_confidence = 1
  holding.extra = extra
  candidateIndex.value = null
}

onUnmounted(() => {
  for (const index of timers.keys()) clearTimer(index)
})
</script>

<template>
  <div class="holdings-table-wrap">
    <table class="edit-table">
      <thead>
        <tr><th class="code-column">代码</th><th class="name-column">名称</th><th>总持仓</th><th>可用</th><th>成本</th><th>现价</th><th>市值</th><th>盈亏率</th><th>盈亏金额</th><th class="status-column">状态</th><th class="action-column" /></tr>
      </thead>
      <tbody>
        <tr v-for="(holding, index) in holdings" :key="index">
          <td class="code-column"><n-input :value="holding.code" placeholder="证券代码" @update:value="updateCode(index, $event)" @blur="resolve(index)" /></td>
          <td class="name-column">
            <n-input v-if="isResolved(holding)" :value="holding.display_name || holding.name || ''" placeholder="名称" readonly />
            <n-input v-else :value="holding.name || ''" placeholder="名称" @update:value="updateName(index, $event)" @blur="resolve(index)" />
          </td>
          <td><n-input-number v-model:value="holding.qty" :show-button="false" /></td>
          <td><n-input-number v-model:value="holding.available_qty" :show-button="false" /></td>
          <td><n-input-number v-model:value="holding.cost" :show-button="false" /></td>
          <td><n-input-number v-if="isResolved(holding)" v-model:value="holding.price" :show-button="false" /><span v-else class="missing-value">—</span></td>
          <td><n-input-number v-if="isResolved(holding)" v-model:value="holding.market_value" :show-button="false" /><span v-else class="missing-value">—</span></td>
          <td><n-input-number v-model:value="holding.pnl" :show-button="false" /></td>
          <td><n-input-number v-model:value="holding.pnl_amount" :show-button="false" /></td>
          <td class="status-column">
            <n-tag size="small" :type="statusType(holding)">{{ resolving[index] ? '匹配中' : statusLabel(holding) }}</n-tag>
            <n-button v-if="statusOf(holding) === 'AMBIGUOUS'" text type="primary" size="small" @click="openCandidates(index)">选择证券</n-button>
            <n-button v-if="!resolving[index] && ['UNRESOLVED', 'INVALID'].includes(statusOf(holding))" text type="primary" size="small" @click="rematch(index)">重新匹配</n-button>
          </td>
          <td class="action-column"><n-button quaternary circle type="error" aria-label="删除持仓行" @click="emit('remove', index)"><template #icon><Trash2 :size="15" /></template></n-button></td>
        </tr>
      </tbody>
    </table>
  </div>

  <n-modal v-model:show="candidateModalOpen" preset="card" title="选择证券" :style="{ width: 'min(640px, calc(100vw - 32px))' }" :mask-closable="false">
    <div class="candidate-table-wrap">
      <table class="candidate-table">
        <thead><tr><th>代码</th><th>名称</th><th>类型</th><th>交易所</th><th aria-label="操作" /></tr></thead>
        <tbody>
          <tr v-for="candidate in candidateRows" :key="String(candidate.security_id || candidate.canonical_code || candidate.code)">
            <td>{{ candidate.canonical_code || candidate.code || '—' }}</td>
            <td>{{ candidate.display_name || candidate.name || '—' }}</td>
            <td>{{ candidate.asset_type || candidate.security_type || '—' }}</td>
            <td>{{ candidate.exchange || '—' }}</td>
            <td><n-button size="small" type="primary" @click="selectCandidate(candidate)">选择</n-button></td>
          </tr>
        </tbody>
      </table>
      <n-empty v-if="!candidateRows.length" description="没有可选择的证券候选" />
    </div>
  </n-modal>
</template>

<style scoped>
.holdings-table-wrap { overflow-x: auto; }
.edit-table { width: 100%; min-width: 1120px; border-collapse: collapse; }
.edit-table th { padding: 7px 6px; color: var(--text-muted, var(--app-text-muted)); font-size: 11px; text-align: left; white-space: nowrap; }
.edit-table td { min-width: 100px; border-top: 1px solid var(--border, var(--app-border-soft)); padding: 6px 4px; vertical-align: middle; }
.edit-table .code-column { width: 112px; min-width: 112px; }
.edit-table .name-column { width: 160px; min-width: 150px; }
.edit-table .status-column { width: 96px; min-width: 96px; }
.edit-table .action-column { width: 42px; min-width: 42px; }
.status-column { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.missing-value { display: inline-block; min-width: 80px; color: var(--text-muted, var(--app-text-muted)); text-align: center; }
.candidate-table-wrap { overflow-x: auto; }
.candidate-table { width: 100%; border-collapse: collapse; }
.candidate-table th, .candidate-table td { border-bottom: 1px solid var(--border, var(--app-border-soft)); padding: 9px 7px; text-align: left; }
.candidate-table th { color: var(--text-muted, var(--app-text-muted)); font-size: 12px; }
@media (max-width: 680px) {
  .edit-table { min-width: 1080px; }
  .edit-table .name-column { width: 150px; min-width: 140px; }
}
</style>
