<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ClipboardList, FileUp, PenLine, RotateCcw, Save, Trash2, X } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type { LedgerImportPreview, TradeLedgerEntry } from '../api/types'
import { errorMessage } from '../api'
import { fmtDateTime, formatCurrency, actionLabel, statusLabel, ledgerSourceLabel, ledgerEntryTypeLabel } from '../utils/ui'

const props = defineProps<{ show: boolean; portfolioId: number | null }>()
const emit = defineEmits<{
  'update:show': [value: boolean]
  changed: []
}>()

const message = useMessage()
const mode = ref<'list' | 'manual' | 'import'>('list')
const entries = ref<TradeLedgerEntry[]>([])
const loading = ref(false)
const listError = ref('')

const manual = ref({
  kind: 'BUY',
  code: '',
  name: '',
  quantity: null as number | null,
  price: null as number | null,
  fees: null as number | null,
  taxes: null as number | null,
  amount: null as number | null,
  executedAt: Date.now(),
  notes: '',
})
const savingManual = ref(false)

const importFile = ref<File | null>(null)
const importPreview = ref<LedgerImportPreview | null>(null)
const importBusy = ref(false)
const importResult = ref('')

const revisingId = ref<number | null>(null)
const reviseQuantity = ref<number | null>(null)
const revisePrice = ref<number | null>(null)
const reviseReason = ref('')
const voidingId = ref<number | null>(null)
const voidReason = ref('')
const mutating = ref(false)

function ledgerFieldLabel(field: string): string {
  return ({ entry_type: '业务类型', side: '成交方向', security_code: '证券代码', security_name: '证券名称', quantity: '成交数量', price: '成交价格', executed_at: '成交时间', trade_date: '交易日期', fees: '手续费', taxes: '税费', net_amount: '净金额', amount: '金额', notes: '备注', source_ref: '来源记录' } as Record<string, string>)[field] || '其他字段'
}

const tradeKinds = [
  { label: '买入', value: 'BUY' },
  { label: '卖出', value: 'SELL' },
  { label: '现金转入', value: 'CASH_IN' },
  { label: '现金转出', value: 'CASH_OUT' },
  { label: '股息红利', value: 'DIVIDEND' },
  { label: '手续费', value: 'FEE' },
  { label: '税费', value: 'TAX' },
]
const isTrade = computed(() => ['BUY', 'SELL'].includes(manual.value.kind))
const readyRows = computed(() => (importPreview.value?.rows || []).filter((row) => row.status === 'READY'))

function kindLabel(entry: TradeLedgerEntry): string {
  return entry.entry_type === 'TRADE' ? actionLabel(entry.side) : ledgerEntryTypeLabel(entry.entry_type)
}



function statusType(status: string): 'success' | 'warning' | 'default' {
  if (status === 'CONFIRMED') return 'success'
  if (status === 'PENDING_REVIEW') return 'warning'
  return 'default'
}

async function loadEntries() {
  if (!props.portfolioId) return
  loading.value = true
  listError.value = ''
  try {
    entries.value = await api.listLedgerEntries(props.portfolioId)
  } catch (error) {
    listError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

function resetManual() {
  manual.value = {
    kind: 'BUY', code: '', name: '', quantity: null, price: null, fees: null, taxes: null,
    amount: null, executedAt: Date.now(), notes: '',
  }
}

async function submitManual() {
  if (!props.portfolioId) return
  const payload: Record<string, unknown> = {
    entry_type: isTrade.value ? 'TRADE' : manual.value.kind,
    executed_at: new Date(manual.value.executedAt).toISOString(),
    source: 'MANUAL',
    notes: manual.value.notes || null,
  }
  if (isTrade.value) {
    payload.security_code = manual.value.code.trim()
    payload.security_name = manual.value.name.trim() || null
    payload.side = manual.value.kind
    payload.quantity = manual.value.quantity
    payload.price = manual.value.price
    payload.fees = manual.value.fees
    payload.taxes = manual.value.taxes
  } else {
    payload.net_amount = manual.value.amount
  }
  savingManual.value = true
  try {
    await api.createLedgerEntry(props.portfolioId, payload)
    message.success('已记录，这笔成交会立即计入当前账户')
    resetManual()
    await loadEntries()
    mode.value = 'list'
    emit('changed')
  } catch (error) {
    message.error(errorMessage(error))
  } finally {
    savingManual.value = false
  }
}

function selectImportFile(event: Event) {
  importFile.value = (event.target as HTMLInputElement).files?.[0] || null
  importPreview.value = null
  importResult.value = ''
}

async function runImportPreview() {
  if (!props.portfolioId || !importFile.value) return
  importBusy.value = true
  importResult.value = ''
  try {
    importPreview.value = await api.previewLedgerImport(props.portfolioId, importFile.value)
  } catch (error) {
    message.error(errorMessage(error))
  } finally {
    importBusy.value = false
  }
}

async function commitImport() {
  if (!props.portfolioId || !importPreview.value) return
  importBusy.value = true
  try {
    const result = await api.commitLedgerImport(props.portfolioId, {
      source_ref: importPreview.value.source_ref,
      rows: readyRows.value.map((row) => row.normalized || {}),
      label: importFile.value?.name,
    })
    importResult.value = `已写入 ${result.created} 条，跳过重复 ${result.skipped} 条${result.errors.length ? `，${result.errors.length} 条未通过校验` : ''}。`
    if (result.created) emit('changed')
    await loadEntries()
  } catch (error) {
    message.error(errorMessage(error))
  } finally {
    importBusy.value = false
  }
}

function startRevise(entry: TradeLedgerEntry) {
  revisingId.value = entry.id
  voidingId.value = null
  reviseQuantity.value = entry.quantity ?? null
  revisePrice.value = entry.price ?? null
  reviseReason.value = ''
}

async function submitRevise(entry: TradeLedgerEntry) {
  if (!props.portfolioId || !reviseReason.value.trim()) return
  const changes: Record<string, unknown> = {}
  if (entry.entry_type === 'TRADE') {
    if (reviseQuantity.value !== entry.quantity && reviseQuantity.value !== null) changes.quantity = reviseQuantity.value
    if (revisePrice.value !== entry.price && revisePrice.value !== null) changes.price = revisePrice.value
  }
  if (!Object.keys(changes).length) {
    message.warning('没有需要保存的修改')
    return
  }
  mutating.value = true
  try {
    await api.reviseLedgerEntry(props.portfolioId, entry.id, { changes, reason: reviseReason.value.trim() })
    message.success('修订已保存，旧版本保留在审计记录中')
    revisingId.value = null
    await loadEntries()
    emit('changed')
  } catch (error) {
    message.error(errorMessage(error))
  } finally {
    mutating.value = false
  }
}

async function submitVoid(entry: TradeLedgerEntry) {
  if (!props.portfolioId || !voidReason.value.trim()) return
  mutating.value = true
  try {
    await api.voidLedgerEntry(props.portfolioId, entry.id, voidReason.value.trim())
    message.success('已撤销，该记录不再计入当前账户')
    voidingId.value = null
    await loadEntries()
    emit('changed')
  } catch (error) {
    message.error(errorMessage(error))
  } finally {
    mutating.value = false
  }
}

watch(() => props.show, (show) => {
  if (show) {
    mode.value = 'list'
    importFile.value = null
    importPreview.value = null
    importResult.value = ''
    revisingId.value = null
    voidingId.value = null
    void loadEntries()
  }
}, { immediate: true })
</script>

<template>
  <n-drawer :show="show" width="min(820px, 100vw)" placement="right" @update:show="emit('update:show', $event)">
    <n-drawer-content title="记录实际成交" closable>
      <div class="ledger-stack">
        <div class="ledger-intro">
          <div>
            <p class="ledger-eyebrow">真实成交记录</p>
            <h2>用真实成交更新当前账户</h2>
            <p>确认后的成交会叠加在最近一次确认快照上，分析将使用更新后的持仓和可用资金。</p>
          </div>
          <n-button quaternary circle aria-label="关闭成交记录" @click="emit('update:show', false)"><template #icon><X :size="18" /></template></n-button>
        </div>

        <n-radio-group v-model:value="mode" size="small">
          <n-radio-button value="list"><ClipboardList :size="14" /> 记录列表</n-radio-button>
          <n-radio-button value="manual"><PenLine :size="14" /> 手动录入</n-radio-button>
          <n-radio-button value="import"><FileUp :size="14" /> 导入流水</n-radio-button>
        </n-radio-group>

        <section v-if="mode === 'list'" class="ledger-section">
          <div class="section-head"><div><h3>已记录事实</h3><p>撤销或修订都会保留审计版本，不会重复扣减。</p></div><n-button size="small" secondary :loading="loading" @click="loadEntries"><template #icon><RotateCcw :size="14" /></template>刷新</n-button></div>
          <n-alert v-if="listError" type="error" :show-icon="false">{{ listError }}</n-alert>
          <div v-if="!entries.length && !loading" class="ledger-empty">还没有记录成交。上午卖出后在这里登记，下午分析就会按新持仓计算。</div>
          <div v-for="entry in entries" :key="entry.id" class="ledger-row" :class="{ muted: entry.status === 'VOIDED' }">
            <div class="ledger-main">
              <strong>{{ kindLabel(entry) }} {{ entry.security_name || entry.security_code || '' }}</strong>
              <span class="ledger-meta">
                {{ entry.trade_date }} · {{ fmtDateTime(entry.executed_at) }}
                <template v-if="entry.entry_type === 'TRADE'"> · {{ entry.quantity }} 股 @ {{ entry.price }}</template>
                <template v-if="entry.net_amount != null"> · 金额 {{ formatCurrency(entry.net_amount) }}</template>
                · 来源 {{ ledgerSourceLabel(entry.source) }}
              </span>
            </div>
            <div class="ledger-side">
              <n-tag size="small" :bordered="false" :type="statusType(entry.status)">{{ statusLabel(entry.status) }}</n-tag>
              <n-button v-if="entry.status !== 'VOIDED'" size="tiny" quaternary @click="startRevise(entry)">修订</n-button>
              <n-button v-if="entry.status !== 'VOIDED'" size="tiny" quaternary type="error" @click="voidingId = entry.id; revisingId = null; voidReason = ''">撤销</n-button>
            </div>
            <div v-if="revisingId === entry.id" class="ledger-inline">
              <template v-if="entry.entry_type === 'TRADE'">
                <n-input-number v-model:value="reviseQuantity" size="small" :min="0" placeholder="数量" />
                <n-input-number v-model:value="revisePrice" size="small" :min="0" placeholder="价格" />
              </template>
              <n-input v-model:value="reviseReason" size="small" placeholder="修订原因（必填）" />
              <n-button size="small" type="primary" :loading="mutating" @click="submitRevise(entry)"><template #icon><Save :size="13" /></template>保存修订</n-button>
            </div>
            <div v-if="voidingId === entry.id" class="ledger-inline">
              <n-input v-model:value="voidReason" size="small" placeholder="撤销原因（必填）" />
              <n-button size="small" type="error" :loading="mutating" @click="submitVoid(entry)"><template #icon><Trash2 :size="13" /></template>确认撤销</n-button>
            </div>
          </div>
        </section>

        <section v-if="mode === 'manual'" class="ledger-section">
          <div class="section-head"><div><h3>手动录入</h3><p>数量与价格分开填写；资金类记录只需金额。</p></div><PenLine :size="18" /></div>
          <n-form label-placement="top">
            <div class="manual-grid">
              <n-form-item label="业务类型"><n-select v-model:value="manual.kind" :options="tradeKinds" /></n-form-item>
              <n-form-item label="发生时间"><n-date-picker v-model:value="manual.executedAt" type="datetime" clearable /></n-form-item>
            </div>
            <div v-if="isTrade" class="manual-grid">
              <n-form-item label="证券代码"><n-input v-model:value="manual.code" placeholder="如 600519" /></n-form-item>
              <n-form-item label="证券名称"><n-input v-model:value="manual.name" placeholder="可选" /></n-form-item>
              <n-form-item label="成交数量"><n-input-number v-model:value="manual.quantity" :min="0" placeholder="股数" /></n-form-item>
              <n-form-item label="成交价格"><n-input-number v-model:value="manual.price" :min="0" placeholder="价格" /></n-form-item>
              <n-form-item label="手续费"><n-input-number v-model:value="manual.fees" :min="0" placeholder="手续费" /></n-form-item>
              <n-form-item label="印花税"><n-input-number v-model:value="manual.taxes" :min="0" placeholder="印花税" /></n-form-item>
            </div>
            <div v-else class="manual-grid">
              <n-form-item label="金额"><n-input-number v-model:value="manual.amount" :min="0" placeholder="金额" /></n-form-item>
            </div>
            <n-form-item label="备注"><n-input v-model:value="manual.notes" type="textarea" :rows="2" placeholder="可选，例如券商流水说明" /></n-form-item>
            <n-button type="primary" block size="large" :loading="savingManual" @click="submitManual"><template #icon><Save :size="16" /></template>记录这笔事实</n-button>
          </n-form>
        </section>

        <section v-if="mode === 'import'" class="ledger-section">
          <div class="section-head"><div><h3>导入券商流水</h3><p>支持 CSV/TXT，自动识别常见中英文表头；重复行默认跳过。</p></div><FileUp :size="18" /></div>
          <label class="import-drop">
            <input type="file" accept=".csv,.txt,text/csv" @change="selectImportFile" />
            <strong>{{ importFile ? importFile.name : '选择流水文件' }}</strong>
            <span>也可以先用 Excel 导出为 CSV</span>
          </label>
          <n-button secondary :disabled="!importFile" :loading="importBusy && !importPreview" @click="runImportPreview">预览解析结果</n-button>
          <template v-if="importPreview">
            <div class="import-summary">
              <span>共 {{ importPreview.summary.total }} 行</span>
              <span>可用 {{ importPreview.summary.ready }}</span>
              <span>重复 {{ importPreview.summary.duplicates }}</span>
              <span>异常 {{ importPreview.summary.invalid }}</span>
            </div>
            <div class="mapping-line">字段映射：{{ Object.entries(importPreview.mapping).map(([field, header]) => `${ledgerFieldLabel(field)}→${header}`).join('；') || '未识别到表头' }}</div>
            <div class="import-rows">
              <div v-for="row in importPreview.rows" :key="row.row_number" class="import-row" :class="row.status.toLowerCase()">
                <div><strong>第 {{ row.row_number }} 行</strong><span>{{ row.status === 'READY' ? '可导入' : row.status === 'DUPLICATE' ? `重复（${row.duplicate_kind === 'IN_FILE' ? '文件内' : '已有记录'}）` : '无法解析' }}</span></div>
                <div class="import-detail">
                  <template v-if="row.normalized">{{ ledgerEntryTypeLabel(row.normalized.entry_type) }} {{ row.normalized.security_code || '' }} {{ row.normalized.side ? actionLabel(row.normalized.side) : '' }} {{ row.normalized.quantity || '' }} {{ row.normalized.price || '' }}</template>
                  <template v-if="row.issues?.length"> · {{ row.issues.map((item) => item.message).join('；') }}</template>
                </div>
              </div>
            </div>
            <n-button type="primary" block size="large" :disabled="!readyRows.length" :loading="importBusy" @click="commitImport">
              导入 {{ readyRows.length }} 条可用记录
            </n-button>
            <n-alert v-if="importResult" type="success" :show-icon="false">{{ importResult }}</n-alert>
          </template>
        </section>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.ledger-stack { display: grid; gap: 14px; }
.ledger-intro { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--border); padding-bottom: 14px; }
.ledger-intro h2 { margin: 0; font-size: 21px; }.ledger-intro p:not(.ledger-eyebrow) { margin: 6px 0 0; color: var(--text-muted); }
.ledger-eyebrow { margin: 0 0 4px; color: var(--primary); font-size: 10px; font-weight: 800; letter-spacing: .08em; }
.ledger-section { display: grid; gap: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 16px; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.section-head h3 { margin: 0; font-size: 16px; }.section-head p { margin: 5px 0 0; color: var(--text-muted); font-size: 12px; }.section-head > svg { color: var(--primary); }
.ledger-empty { border: 1px dashed var(--border-strong); border-radius: 8px; background: var(--surface-muted); padding: 18px; color: var(--text-muted); text-align: center; }
.ledger-row { display: grid; gap: 8px; border-bottom: 1px solid var(--border); padding: 10px 0; }.ledger-row:last-child { border-bottom: 0; }
.ledger-row.muted { opacity: .55; }
.ledger-main { display: grid; gap: 3px; }.ledger-main strong { font-size: 14px; }.ledger-meta { color: var(--text-muted); font-size: 12px; }
.ledger-side { display: flex; align-items: center; gap: 6px; }
.ledger-inline { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; border-top: 1px dashed var(--border); padding-top: 8px; }.ledger-inline .n-input, .ledger-inline .n-input-number { max-width: 190px; }
.manual-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }
.import-drop { display: grid; min-height: 96px; place-items: center; align-content: center; gap: 6px; border: 1px dashed var(--border-strong); border-radius: 8px; background: var(--surface-muted); color: var(--text-muted); cursor: pointer; }.import-drop input { display: none; }.import-drop strong { color: var(--text); }
.import-summary { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--text-muted); font-size: 12px; }
.mapping-line { color: var(--text-muted); font-size: 11px; line-height: 1.6; }
.import-rows { display: grid; gap: 6px; max-height: 260px; overflow-y: auto; }
.import-row { display: grid; gap: 2px; border-left: 3px solid var(--border-strong); border-radius: 4px; background: var(--surface-muted); padding: 8px 10px; }
.import-row.ready { border-left-color: var(--primary); }.import-row.duplicate { border-left-color: var(--warning); }.import-row.invalid { border-left-color: var(--danger); }
.import-row > div:first-child { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; }.import-row span { color: var(--text-muted); }
.import-detail { color: var(--text-muted); font-size: 11px; word-break: break-all; }
@media (max-width: 680px) { .ledger-section { padding: 13px; }.ledger-intro { flex-direction: column; }.manual-grid { grid-template-columns: 1fr; } }
</style>
