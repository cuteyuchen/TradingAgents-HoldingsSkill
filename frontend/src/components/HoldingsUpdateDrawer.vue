<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Camera, CheckCircle2, ClipboardPaste, FileImage, Play, Plus, RefreshCw, Save, X } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import { usePortfolioContext } from '../composables/portfolio'
import { fmtDateTime } from '../utils/ui'
import type { AnalysisJob, AnalysisMode, HoldingUpload, ParsedHoldings, PortfolioSnapshot } from '../api/types'
import HoldingsIdentityTable from './HoldingsIdentityTable.vue'

const props = defineProps<{ show: boolean; portfolioId: number | null }>()
const emit = defineEmits<{
  'update:show': [value: boolean]
  confirmed: [snapshot: PortfolioSnapshot]
}>()

const route = useRoute()
const router = useRouter()
const message = useMessage()
const selectedFile = ref<File | null>(null)
const previewUrl = ref('')
const upload = ref<HoldingUpload | null>(null)
const parsed = ref<ParsedHoldings | null>(null)
const snapshot = ref<PortfolioSnapshot | null>(null)
const job = ref<AnalysisJob | null>(null)
const loading = ref(false)
const saving = ref(false)
const confirming = ref(false)
const loadingSnapshot = ref(false)
const analysisStarting = ref(false)
const jobActionLoading = ref(false)
const retryingUpload = ref(false)
const pollingError = ref('')
const analysisMode = ref<AnalysisMode>('deep')
const checkpoint = ref('10:30')
const notify = ref(true)
const analysisPanel = ref<HTMLElement | null>(null)
let pollTimer: number | null = null
let pollBusy = false

const { portfolios, selectedPortfolioId, setSelectedPortfolio } = usePortfolioContext()
const activePortfolioId = computed(() => props.portfolioId || selectedPortfolioId.value)
const canUpload = computed(() => Boolean(selectedFile.value))
const identityIssueCount = computed(() => parsed.value?.holdings.filter((holding) => holding.resolution_status !== 'RESOLVED' || !holding.canonical_code || !holding.security_id).length || 0)
const canConfirm = computed(() => Boolean(upload.value && parsed.value?.holdings.length && !upload.value.validation_errors.length && identityIssueCount.value === 0))
const snapshotIdentityBlocked = computed(() => Boolean(snapshot.value && snapshot.value.identity_status && snapshot.value.identity_status !== 'RESOLVED'))
const terminalJob = computed(() => ['succeeded', 'failed', 'cancelled'].includes(job.value?.status || ''))
const jobProgressStatus = computed<'error' | 'success' | 'default'>(() => {
  if (job.value?.status === 'failed') return 'error'
  if (job.value?.status === 'succeeded') return 'success'
  return 'default'
})
const jobSucceeded = computed(() => job.value?.status === 'succeeded' && Boolean(job.value.run_id))
const jobRunning = computed(() => ['queued', 'running'].includes(String(job.value?.status || '')))
const jobFailed = computed(() => job.value?.status === 'failed')
const stageLabels: Record<string, string> = {
  queued: '等待执行', context_loading: '加载历史分析', market_collecting: '采集行情与技术数据', symbol_resolving: '匹配证券代码',
  analysts_running: '多维分析师研判', quality_gate: '执行数据质量门控', investment_debate: '多空观点辩论', research_verdict: '研究总监裁决',
  trader_proposal: '生成交易员方案', risk_revision: '风控审查与修正', risk_debate: '三方风控辩论', candidate_screening: '扫描新增机会候选',
  portfolio_synthesis: '组合经理裁决', final_quote_refresh: '刷新最终行情', report_rendering: '生成结构化报告', completed: '分析完成', failed: '分析失败', cancelled: '已取消',
}

function uploadStatusText(status?: string | null) {
  return ({ uploaded: '已上传', vision_parsing: '识别中', identity_resolving: '正在匹配证券身份...', waiting_confirmation: '待人工确认', confirmed: '已确认', failed: '识别失败', needs_model: '缺少识图模型' }[String(status || '').toLowerCase()] || String(status || '未知'))
}

function emptyParsed(): ParsedHoldings {
  return { holdings: [], excluded_items: [], notes: [] }
}

function stopPolling() {
  if (pollTimer !== null) window.clearInterval(pollTimer)
  pollTimer = null
  pollBusy = false
}

function clearDraft() {
  stopPolling()
  selectedFile.value = null
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  upload.value = null
  parsed.value = null
  job.value = null
}

function setSelectedFile(file: File | null) {
  selectedFile.value = file
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = file ? URL.createObjectURL(file) : ''
  upload.value = null
  parsed.value = null
  snapshot.value = null
  job.value = null
}

function selectFile(event: Event) {
  setSelectedFile((event.target as HTMLInputElement).files?.[0] || null)
}

function pasteImage(event: ClipboardEvent) {
  const source = Array.from(event.clipboardData?.files || []).find((file) => file.type.startsWith('image/'))
    || Array.from(event.clipboardData?.items || []).find((item) => item.type.startsWith('image/'))?.getAsFile()
  if (!source) return
  event.preventDefault()
  const extension = source.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png'
  setSelectedFile(new File([source], `clipboard-holdings-${Date.now()}.${extension}`, { type: source.type }))
  message.success('已从剪贴板读取持仓截图')
}

async function loadLatestSnapshot(id: number | null) {
  const portfolio = portfolios.value.find((item) => item.id === id)
  if (!portfolio?.latest_snapshot_id) {
    snapshot.value = null
    return
  }
  loadingSnapshot.value = true
  try {
    snapshot.value = await api.getSnapshot(portfolio.latest_snapshot_id)
  } catch (error) {
    snapshot.value = null
    message.error((error as Error).message)
  } finally {
    loadingSnapshot.value = false
  }
}

async function loadContext() {
  await (async () => {
    const rows = await api.listPortfolios()
    portfolios.value = rows
  })()
  const requested = Number(route.query.portfolio)
  const preferred = portfolios.value.find((item) => item.id === requested)?.id || activePortfolioId.value || portfolios.value.find((item) => item.is_default)?.id || portfolios.value[0]?.id || null
  if (preferred) setSelectedPortfolio(preferred)
  await loadLatestSnapshot(preferred)
}

async function resumeJob(jobId: number) {
  job.value = await api.getAnalysisJob(jobId)
  setSelectedPortfolio(job.value.portfolio_id)
  snapshot.value = await api.getSnapshot(job.value.snapshot_id)
  if (!terminalJob.value) startJobPolling()
  await focusAnalysisPanel()
}

async function submitUpload() {
  if (!selectedFile.value || loading.value) return
  loading.value = true
  try {
    let targetId = activePortfolioId.value
    if (!targetId) {
      const created = await api.createPortfolio({ name: '默认组合', is_default: true })
      portfolios.value.push(created)
      setSelectedPortfolio(created.id)
      targetId = created.id
      message.success('已自动创建默认持仓组合')
    }
    upload.value = await api.uploadHoldings(targetId, selectedFile.value)
    parsed.value = upload.value.parsed || null
    message.success('截图已上传，正在识别持仓')
    startUploadPolling()
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

function startUploadPolling() {
  stopPolling()
  pollingError.value = ''
  pollTimer = window.setInterval(async () => {
    if (!upload.value || pollBusy) return
    pollBusy = true
    try {
      const latest = await api.getUpload(upload.value.id)
      upload.value = latest
      parsed.value = latest.parsed || parsed.value
      if (['waiting_confirmation', 'failed', 'needs_model', 'confirmed'].includes(latest.parsing_status)) stopPolling()
    } catch (error) {
      pollingError.value = (error as Error).message
    } finally {
      pollBusy = false
    }
  }, 1600)
}

function manualEntry() {
  parsed.value = parsed.value || emptyParsed()
  if (!parsed.value.holdings.length) addHolding()
}

function addHolding() {
  if (!parsed.value) parsed.value = emptyParsed()
  parsed.value.holdings.push({ code: '', name: '', resolution_status: 'UNRESOLVED', qty: null, available_qty: null, cost: null, price: null, market_value: null, pnl: null, pnl_amount: null, extra: {} })
}

function removeHolding(index: number) {
  parsed.value?.holdings.splice(index, 1)
}

async function saveParsed(): Promise<boolean> {
  if (!upload.value || !parsed.value) return false
  saving.value = true
  try {
    upload.value = await api.updateParsedHoldings(upload.value.id, parsed.value)
    parsed.value = upload.value.parsed || parsed.value
    message.success('持仓修正已保存')
    return true
  } catch (error) {
    message.error((error as Error).message)
    return false
  } finally {
    saving.value = false
  }
}

async function retryVision() {
  if (!upload.value || retryingUpload.value) return
  retryingUpload.value = true
  try {
    upload.value = await api.retryUploadParse(upload.value.id)
    startUploadPolling()
    message.info('已重新提交识图任务')
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    retryingUpload.value = false
  }
}

async function confirmHoldings(startAnalysis = false) {
  if (!upload.value || confirming.value) return
  confirming.value = true
  try {
    if (!await saveParsed()) return
    snapshot.value = await api.confirmUpload(upload.value.id)
    const portfolio = portfolios.value.find((item) => item.id === snapshot.value?.portfolio_id)
    if (portfolio && snapshot.value) {
      portfolio.latest_snapshot_id = snapshot.value.id
      portfolio.latest_snapshot_time = snapshot.value.snapshot_time
    }
    emit('confirmed', snapshot.value)
    message.success(startAnalysis ? '持仓已确认，正在创建分析任务' : '持仓快照已确认，可随时手动分析')
    await focusAnalysisPanel()
    if (startAnalysis) await runAnalysis()
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    confirming.value = false
  }
}

async function runAnalysis() {
  if (!snapshot.value || snapshotIdentityBlocked.value || analysisStarting.value) return
  analysisStarting.value = true
  try {
    job.value = await api.createAnalysisJob(snapshot.value.id, analysisMode.value, checkpoint.value || undefined, notify.value)
    pollingError.value = ''
    message.success('手动分析任务已创建')
    await router.replace({ name: 'holdings', query: { ...route.query, portfolio: snapshot.value.portfolio_id, action: 'update', job: job.value.id, focus: 'analysis' } })
    startJobPolling()
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    analysisStarting.value = false
  }
}

async function cancelAnalysis() {
  if (!job.value || terminalJob.value || jobActionLoading.value) return
  jobActionLoading.value = true
  try {
    job.value = await api.cancelAnalysisJob(job.value.id)
    stopPolling()
    message.info('分析任务已取消')
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    jobActionLoading.value = false
  }
}

async function retryAnalysis() {
  if (!job.value || job.value.status !== 'failed' || jobActionLoading.value) return
  jobActionLoading.value = true
  try {
    job.value = await api.retryAnalysisJob(job.value.id)
    pollingError.value = ''
    startJobPolling()
    message.info('已重新提交分析任务')
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    jobActionLoading.value = false
  }
}

function startJobPolling() {
  stopPolling()
  pollingError.value = ''
  pollTimer = window.setInterval(async () => {
    if (!job.value || pollBusy) return
    pollBusy = true
    try {
      job.value = await api.getAnalysisJob(job.value.id)
      if (terminalJob.value) {
        stopPolling()
        if (job.value.status === 'succeeded') message.success('分析完成')
        else if (job.value.status === 'failed') message.error(job.value.error_message || '分析失败')
      }
    } catch (error) {
      pollingError.value = (error as Error).message
    } finally {
      pollBusy = false
    }
  }, 1400)
}

async function focusAnalysisPanel() {
  await nextTick()
  analysisPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function openAnalysis() {
  if (job.value?.run_id) void router.push({ name: 'analysis', query: { run: job.value.run_id, portfolio: snapshot.value?.portfolio_id } })
}

function close() {
  emit('update:show', false)
}

watch(() => props.show, async (show) => {
  if (!show) return
  try {
    await loadContext()
    const requestedJob = Number(route.query.job)
    if (requestedJob) await resumeJob(requestedJob)
  } catch (error) {
    message.error((error as Error).message)
  }
})
watch(() => props.portfolioId, (id, previous) => {
  if (id && id !== previous && props.show) {
    clearDraft()
    void loadLatestSnapshot(id)
  }
})

onMounted(() => window.addEventListener('paste', pasteImage))
onUnmounted(() => {
  window.removeEventListener('paste', pasteImage)
  stopPolling()
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})
</script>

<template>
  <n-drawer :show="show" width="min(880px, 100vw)" placement="right" @update:show="emit('update:show', $event)">
    <n-drawer-content title="更新持仓" closable>
      <div class="drawer-stack">
        <div class="drawer-intro"><div><p class="drawer-eyebrow">REVIEW BEFORE CONFIRM</p><h2>上传最新持仓截图</h2><p>识别结果会停留在核对阶段，确认后才会成为当前组合快照。</p></div><n-button quaternary circle aria-label="关闭更新持仓" @click="close"><template #icon><X :size="18" /></template></n-button></div>

        <section class="drawer-section">
          <div class="section-title"><div><h3>1. 上传截图</h3><p>支持 PNG、JPEG、WebP，也可以直接粘贴截图。</p></div><FileImage :size="19" /></div>
          <n-form label-placement="top">
            <n-form-item v-if="portfolios.length > 1" label="更新组合">
              <n-select :value="activePortfolioId" :options="portfolios.map((item) => ({ label: item.name, value: item.id }))" @update:value="setSelectedPortfolio" />
            </n-form-item>
            <label class="drop-zone" :class="{ selected: selectedFile }">
              <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" @change="selectFile" />
              <template v-if="previewUrl">
                <img class="drop-preview" :src="previewUrl" alt="待上传持仓截图预览" />
                <div class="preview-meta"><strong>{{ selectedFile?.name }}</strong><span>{{ ((selectedFile?.size || 0) / 1024 / 1024).toFixed(2) }} MB · 点击可更换</span></div>
              </template>
              <template v-else>
                <Camera :size="28" /><strong>点击选择持仓截图</strong><span class="paste-hint"><ClipboardPaste :size="14" />也可按 Ctrl + V 粘贴</span>
              </template>
            </label>
            <n-button type="primary" block size="large" :disabled="!canUpload" :loading="loading" @click="submitUpload">
              {{ portfolios.length ? '上传并识别' : '创建默认组合并上传识别' }}
            </n-button>
          </n-form>
        </section>

        <section class="drawer-section">
          <div class="section-title"><div><h3>识别状态</h3><p>低质量识别结果不会自动写入当前持仓。</p></div><RefreshCw :size="18" :class="{ spinning: upload && ['uploaded', 'vision_parsing', 'identity_resolving'].includes(upload.parsing_status) }" /></div>
          <div v-if="loadingSnapshot" class="loading-inline"><n-spin size="small" />正在读取最近确认快照</div>
          <div v-else-if="!upload" class="state-placeholder"><CheckCircle2 v-if="snapshot" :size="17" /><span>{{ snapshot ? `最近确认快照 #${snapshot.id} · ${fmtDateTime(snapshot.snapshot_time)}` : '上传截图后，这里会显示识别结果。' }}</span></div>
          <template v-else>
            <div class="state-line"><span>当前状态</span><n-tag :type="upload.parsing_status === 'failed' ? 'error' : upload.parsing_status === 'waiting_confirmation' ? 'warning' : upload.parsing_status === 'confirmed' ? 'success' : 'info'">{{ uploadStatusText(upload.parsing_status) }}</n-tag></div>
            <n-progress v-if="['uploaded', 'vision_parsing', 'identity_resolving'].includes(upload.parsing_status)" type="line" :percentage="upload.parsing_status === 'uploaded' ? 25 : upload.parsing_status === 'vision_parsing' ? 65 : 85" processing :show-indicator="false" />
            <n-alert v-if="upload.error_message" type="warning" :show-icon="false">{{ upload.error_message }}</n-alert>
            <n-alert v-if="upload.validation_errors.length" type="error" :show-icon="false"><div v-for="item in upload.validation_errors" :key="item">{{ item }}</div></n-alert>
            <div class="state-actions"><n-button v-if="['failed', 'needs_model'].includes(upload.parsing_status)" secondary :loading="retryingUpload" @click="retryVision">重新识别</n-button><n-button v-if="!parsed" secondary @click="manualEntry">手工录入</n-button></div>
          </template>
        </section>

        <section v-if="parsed" class="drawer-section">
          <div class="section-title"><div><h3>2. 核对并修正</h3><p>请核对后确认，数量和现金均来自你提交的快照。</p></div><div class="section-actions"><n-button secondary size="small" @click="addHolding"><template #icon><Plus :size="14" /></template>新增一行</n-button><n-button secondary size="small" :loading="saving" @click="saveParsed"><template #icon><Save :size="14" /></template>保存修正</n-button></div></div>
          <div class="fund-grid"><n-form-item label="总资产"><n-input-number v-model:value="parsed.total_assets" :show-button="false" /></n-form-item><n-form-item label="持仓总市值"><n-input-number v-model:value="parsed.total_market_value" :show-button="false" /></n-form-item><n-form-item label="券商可用资金"><n-input-number v-model:value="parsed.broker_available_cash" :show-button="false" /></n-form-item><n-form-item label="修正后未使用资金"><n-input-number v-model:value="parsed.corrected_unused_funds" :show-button="false" /></n-form-item></div>
          <HoldingsIdentityTable :holdings="parsed.holdings" @remove="removeHolding" />
          <n-alert v-if="upload?.parsing_status === 'identity_resolving'" type="info" :show-icon="false">正在匹配证券身份...</n-alert>
          <n-alert v-else-if="identityIssueCount" type="warning" :show-icon="false">还有 {{ identityIssueCount }} 个持仓未确认证券身份。请先补全或确认代码，再保存为正式持仓快照。</n-alert>
          <div class="confirm-row"><n-button secondary size="large" :disabled="!canConfirm" :loading="confirming" @click="confirmHoldings(false)">仅确认快照</n-button><n-button type="success" size="large" :disabled="!canConfirm" :loading="confirming" @click="confirmHoldings(true)"><template #icon><Play :size="16" /></template>确认并立即分析</n-button></div>
        </section>

        <section v-if="snapshot" ref="analysisPanel" class="drawer-section analysis-panel">
          <div class="section-title"><div><h3>{{ parsed ? '3.' : '2.' }} 手动执行组合分析</h3><p>当前使用快照 #{{ snapshot.id }} · {{ fmtDateTime(snapshot.snapshot_time) }}</p></div><Play :size="18" /></div>
          <n-alert v-if="snapshotIdentityBlocked" type="warning" :show-icon="false">证券身份不完整。该快照保留审计历史，但不会作为新的分析默认输入，请重新导入并修正。</n-alert>
          <div class="analysis-form"><n-form-item label="分析模式"><n-radio-group v-model:value="analysisMode"><n-radio-button value="fast">快速</n-radio-button><n-radio-button value="standard">标准</n-radio-button><n-radio-button value="deep">深度</n-radio-button></n-radio-group></n-form-item><n-form-item label="检查点"><n-select v-model:value="checkpoint" :options="['09:35', '10:30', '13:05', '14:30', '15:10'].map((value) => ({ label: value, value }))" /></n-form-item><n-form-item label="完成后通知"><n-switch v-model:value="notify" /></n-form-item><n-button type="primary" size="large" :loading="analysisStarting" :disabled="snapshotIdentityBlocked || Boolean(job && !terminalJob)" @click="runAnalysis"><template #icon><Play :size="16" /></template>开始分析</n-button></div>
          <div v-if="job" class="job-status"><div><strong>{{ stageLabels[job.current_stage] || job.current_stage }}</strong><span>{{ job.progress_percent }}%</span></div><n-progress type="line" :percentage="job.progress_percent" :status="jobProgressStatus" :processing="!terminalJob" /><n-alert v-if="job.error_message" type="error" :show-icon="false">{{ job.error_message }}</n-alert><n-alert v-if="pollingError" type="warning" :show-icon="false">{{ pollingError }} 页面会继续尝试恢复。</n-alert><div class="job-actions"><n-button v-if="jobSucceeded" type="primary" @click="openAnalysis">查看今日分析</n-button><n-button v-if="jobRunning" secondary :loading="jobActionLoading" @click="cancelAnalysis">取消任务</n-button><n-button v-if="jobFailed" secondary :loading="jobActionLoading" @click="retryAnalysis">重试分析</n-button><n-button v-if="terminalJob" secondary @click="job = null">再次分析当前快照</n-button></div></div>
        </section>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.drawer-stack { display: grid; gap: 16px; }
.drawer-intro { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--border); padding-bottom: 14px; }
.drawer-intro h2 { margin: 0; font-size: 22px; }.drawer-intro p:not(.drawer-eyebrow) { margin: 6px 0 0; color: var(--text-muted); }
.drawer-eyebrow { margin: 0 0 4px; color: var(--primary); font-size: 10px; font-weight: 800; letter-spacing: .08em; }
.drawer-section { display: grid; gap: 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 16px; }
.section-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.section-title h3 { margin: 0; font-size: 16px; }.section-title p { margin: 5px 0 0; color: var(--text-muted); font-size: 12px; }.section-title > svg { color: var(--primary); }
.section-actions, .state-actions, .job-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.drop-zone { display: grid; min-height: 180px; place-items: center; align-content: center; gap: 8px; margin-bottom: 14px; overflow: hidden; border: 1px dashed var(--border-strong); border-radius: 8px; background: var(--surface-muted); color: var(--text-muted); cursor: pointer; }.drop-zone input { display: none; }.drop-zone.selected { min-height: 0; border-color: var(--primary); }.drop-preview { display: block; width: 100%; max-height: 320px; object-fit: contain; }.preview-meta { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px solid var(--border); background: var(--surface); padding: 10px 12px; }.preview-meta strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.preview-meta span, .paste-hint { color: var(--text-muted); font-size: 11px; }.paste-hint { display: inline-flex; align-items: center; gap: 5px; }
.loading-inline, .state-placeholder { display: flex; align-items: center; justify-content: center; gap: 9px; min-height: 70px; color: var(--text-muted); }.state-line { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); padding-bottom: 10px; }.state-line > span { color: var(--text-muted); }.spinning { animation: spin 1.2s linear infinite; }@keyframes spin { to { transform: rotate(360deg); } }
.fund-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }.holdings-table-wrap { overflow-x: auto; }.edit-table { width: 100%; min-width: 1160px; border-collapse: collapse; }.edit-table th { padding: 8px; color: var(--text-muted); font-size: 11px; text-align: left; }.edit-table td { min-width: 108px; border-top: 1px solid var(--border); padding: 7px 5px; }.edit-table td:first-child { min-width: 120px; }.confirm-row { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }.analysis-panel { scroll-margin-top: 12px; }.analysis-form { display: grid; grid-template-columns: 1fr 1fr .6fr auto; align-items: end; gap: 12px; }.job-status { display: grid; gap: 10px; border-top: 1px solid var(--border); padding-top: 14px; }.job-status > div:first-child { display: flex; justify-content: space-between; }
@media (max-width: 680px) { .drawer-section { padding: 14px; }.section-title, .drawer-intro { align-items: stretch; flex-direction: column; }.fund-grid, .analysis-form { grid-template-columns: 1fr; }.confirm-row .n-button { width: 100%; }.preview-meta { align-items: flex-start; flex-direction: column; } }
</style>
