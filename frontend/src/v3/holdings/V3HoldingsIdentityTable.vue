<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { Trash2 } from 'lucide-vue-next'
import { api, errorMessage } from '../../api'
import type { Holding } from '../../api/types'
import V3SecurityCandidateDialog from './V3SecurityCandidateDialog.vue'

const props = defineProps<{ holdings: Holding[]; portfolioId?: number | null }>()
const emit = defineEmits<{ remove: [index: number] }>()

const resolving = ref<Record<number, boolean>>({})
const candidateIndex = ref<number | null>(null)
const rowMeta = ref<Record<number, { seq: number; input: string }>>({})
const timers = new Map<number, number>()
const controllers = new Map<number, AbortController>()
const notify = ref<string | null>(null)

const candidateHolding = computed(() => candidateIndex.value === null ? null : props.holdings[candidateIndex.value] || null)
const candidateRows = computed(() => {
  const raw = candidateHolding.value?.extra?.identity_candidates
  return Array.isArray(raw) ? raw as Array<Record<string, any>> : []
})

function statusOf(holding: Holding): 'RESOLVED' | 'AMBIGUOUS' | 'UNRESOLVED' | 'INVALID' {
  const status = String(holding.resolution_status || 'UNRESOLVED').toUpperCase()
  return status === 'RESOLVED' || status === 'AMBIGUOUS' || status === 'INVALID' ? status : 'UNRESOLVED'
}

function statusLabel(holding: Holding): string {
  const base = ({
    RESOLVED: '已匹配',
    AMBIGUOUS: '需要选择',
    UNRESOLVED: '未找到',
    INVALID: '代码无效',
  } as Record<string, string>)[statusOf(holding)]
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

function statusTone(holding: Holding): 'success' | 'warning' | 'info' | 'danger' {
  return ({ RESOLVED: 'success', AMBIGUOUS: 'warning', UNRESOLVED: 'info', INVALID: 'danger' } as const)[statusOf(holding)]
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
  if (clearName) holding.display_name = null
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

function isAbortError(error: unknown): boolean {
  return Boolean(error && (error as { name?: string }).name === 'AbortError')
}

function isCurrentResolve(index: number, seq: number, capturedInput: string): boolean {
  const meta = rowMeta.value[index]
  if (!meta || meta.seq !== seq) return false
  return meta.input === String(props.holdings[index]?.code ?? '').trim()
}

async function resolve(index: number) {
  clearTimer(index)
  const holding = props.holdings[index]
  if (!holding) return
  const capturedInput = holding.code.trim()
  const capturedName = (holding.name || '').trim()
  if (!capturedInput && !capturedName) {
    invalidate(holding, { clearName: false })
    return
  }

  // Overlapping resolve is allowed: a new edit supersedes an in-flight old request.
  controllers.get(index)?.abort()
  const controller = new AbortController()
  controllers.set(index, controller)

  const seq = (rowMeta.value[index]?.seq || 0) + 1
  rowMeta.value = { ...rowMeta.value, [index]: { seq, input: capturedInput } }
  resolving.value = { ...resolving.value, [index]: true }

  try {
    const resolved = await api.resolveHolding(
      {
        ...holding,
        code: capturedInput,
        name: capturedName || holding.name,
        extra: { ...(holding.extra || {}), submitted_code: capturedInput || undefined },
      },
      props.portfolioId,
      controller.signal,
    )
    if (controller.signal.aborted) return
    if (!isCurrentResolve(index, seq, capturedInput)) return
    Object.assign(holding, resolved)
  } catch (error) {
    if (controller.signal.aborted || isAbortError(error)) return
    if (!isCurrentResolve(index, seq, capturedInput)) return
    invalidate(holding, { clearName: false })
    notify.value = errorMessage(error)
  } finally {
    if (controllers.get(index) === controller) {
      controllers.delete(index)
    }
    if (isCurrentResolve(index, seq, capturedInput) || rowMeta.value[index]?.seq === seq) {
      const next = { ...resolving.value }
      delete next[index]
      resolving.value = next
    }
  }
}

async function rematch(index: number) {
  const holding = props.holdings[index]
  if (!holding) return
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

function numericInput(value: string): number | null {
  if (value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

onUnmounted(() => {
  for (const index of timers.keys()) clearTimer(index)
  for (const controller of controllers.values()) controller.abort()
  controllers.clear()
})
</script>

<template>
  <div class="identity" data-testid="v3-holdings-identity-table">
    <p v-if="notify" class="identity__notify" role="status">{{ notify }}</p>
    <div class="identity__scroll">
      <table class="identity__table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>总持仓</th>
            <th>可用</th>
            <th>成本</th>
            <th>现价</th>
            <th>市值</th>
            <th>盈亏率</th>
            <th>盈亏金额</th>
            <th>状态</th>
            <th aria-label="操作" />
          </tr>
        </thead>
        <tbody>
          <tr v-for="(holding, index) in holdings" :key="index" :data-testid="`v3-identity-row-${index}`">
            <td>
              <input
                :value="holding.code"
                data-testid="v3-identity-code"
                placeholder="证券代码"
                @input="updateCode(index, ($event.target as HTMLInputElement).value)"
                @blur="resolve(index)"
              />
            </td>
            <td>
              <input
                :value="isResolved(holding) ? (holding.display_name || holding.name || '') : (holding.name || '')"
                placeholder="名称"
                :readonly="isResolved(holding)"
                @input="updateName(index, ($event.target as HTMLInputElement).value)"
                @blur="resolve(index)"
              />
            </td>
            <td><input :value="holding.qty ?? ''" type="number" @input="holding.qty = numericInput(($event.target as HTMLInputElement).value)" /></td>
            <td><input :value="holding.available_qty ?? ''" type="number" data-testid="v3-identity-available" @input="holding.available_qty = numericInput(($event.target as HTMLInputElement).value)" /></td>
            <td><input :value="holding.cost ?? ''" type="number" @input="holding.cost = numericInput(($event.target as HTMLInputElement).value)" /></td>
            <td>
              <input
                v-if="isResolved(holding)"
                :value="holding.price ?? ''"
                type="number"
                @input="holding.price = numericInput(($event.target as HTMLInputElement).value)"
              />
              <span v-else>—</span>
            </td>
            <td>
              <input
                v-if="isResolved(holding)"
                :value="holding.market_value ?? ''"
                type="number"
                @input="holding.market_value = numericInput(($event.target as HTMLInputElement).value)"
              />
              <span v-else>—</span>
            </td>
            <td><input :value="holding.pnl ?? ''" type="number" @input="holding.pnl = numericInput(($event.target as HTMLInputElement).value)" /></td>
            <td><input :value="holding.pnl_amount ?? ''" type="number" @input="holding.pnl_amount = numericInput(($event.target as HTMLInputElement).value)" /></td>
            <td class="identity__status">
              <span :data-status="statusOf(holding)">{{ resolving[index] ? '匹配中' : statusLabel(holding) }}</span>
              <button
                v-if="statusOf(holding) === 'AMBIGUOUS'"
                type="button"
                data-testid="v3-identity-select-candidate"
                @click="openCandidates(index)"
              >选择证券</button>
              <button
                v-if="!resolving[index] && ['UNRESOLVED', 'INVALID'].includes(statusOf(holding))"
                type="button"
                data-testid="v3-identity-rematch"
                @click="rematch(index)"
              >重新匹配</button>
            </td>
            <td>
              <button type="button" class="icon-btn" aria-label="删除持仓行" @click="emit('remove', index)">
                <Trash2 :size="15" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <V3SecurityCandidateDialog
      v-if="candidateIndex !== null"
      :candidates="candidateRows"
      data-testid="v3-security-candidate-dialog"
      @close="candidateIndex = null"
      @select="selectCandidate"
    />
  </div>
</template>

<style scoped>
.identity__notify {
  margin: 0 0 8px;
  color: var(--v3-status-warning);
  font-size: var(--v3-font-size-sm);
}
.identity__scroll { overflow-x: auto; }
.identity__table {
  width: 100%;
  min-width: 1120px;
  border-collapse: collapse;
}
.identity__table th,
.identity__table td {
  border-bottom: 1px solid var(--v3-border-subtle);
  padding: 6px 4px;
  text-align: left;
  font-size: var(--v3-font-size-xs);
}
.identity__table th { color: var(--v3-text-muted); }
.identity__table input {
  width: 100%;
  min-width: 72px;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text);
  padding: 4px 6px;
  font: inherit;
}
.identity__status {
  display: grid;
  gap: 4px;
  justify-items: start;
}
.identity__status button,
.icon-btn {
  border: 0;
  background: none;
  color: var(--v3-primary);
  cursor: pointer;
  padding: 0;
  font: inherit;
  font-size: 11px;
}
.icon-btn { color: var(--v3-status-danger); }
</style>
