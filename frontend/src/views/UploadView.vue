<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Camera, CheckCircle2, ClipboardPaste, FileImage, Play, Plus, RefreshCw, Save, Trash2 } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type { AnalysisJob, AnalysisMode, HoldingUpload, ParsedHoldings, Portfolio, PortfolioSnapshot } from '../api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const portfolios = ref<Portfolio[]>([])
const portfolioId = ref<number | null>(null)
const selectedFile = ref<File | null>(null)
const previewUrl = ref('')
const upload = ref<HoldingUpload | null>(null)
const parsed = ref<ParsedHoldings | null>(null)
const snapshot = ref<PortfolioSnapshot | null>(null)
const job = ref<AnalysisJob | null>(null)
const loading = ref(false)
const saving = ref(false)
const confirming = ref(false)
const loadingLatestSnapshot = ref(false)
const analysisStarting = ref(false)
const analysisMode = ref<AnalysisMode>('deep')
const checkpoint = ref('10:00')
const notify = ref(true)
const analysisPanel = ref<HTMLElement | null>(null)
let initialized = false
let pollTimer: number | null = null

const canUpload = computed(() => Boolean(selectedFile.value))
const canConfirm = computed(() => Boolean(upload.value && parsed.value?.holdings.length && !upload.value.validation_errors.length))
const missingCodeCount = computed(() => parsed.value?.holdings.filter((holding) => !holding.code.trim()).length || 0)
const terminalJob = computed(() => ['succeeded', 'failed', 'cancelled'].includes(job.value?.status || ''))
const selectedPortfolio = computed(() => portfolios.value.find((item) => item.id === portfolioId.value) || null)
const stageLabels: Record<string, string> = {
  queued: '等待执行',
  context_loading: '加载历史分析',
  market_collecting: '采集行情与技术数据',
  symbol_resolving: '匹配证券代码',
  analysts_running: '多维分析师研判',
  quality_gate: '执行数据质量门控',
  investment_debate: '多空观点辩论',
  research_verdict: '研究总监裁决',
  trader_proposal: '生成交易员方案',
  risk_revision: '风控审查与修正',
  risk_debate: '三方风控辩论',
  candidate_screening: '扫描今日买入候选',
  portfolio_synthesis: '组合经理裁决',
  final_quote_refresh: '刷新最终行情',
  report_rendering: '生成结构化报告',
  completed: '分析完成',
  failed: '分析失败',
  cancelled: '已取消',
}

function fmt(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

function emptyParsed(): ParsedHoldings {
  return { holdings: [], excluded_items: [], notes: [] }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
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
  const input = event.target as HTMLInputElement
  setSelectedFile(input.files?.[0] || null)
}

function pasteImage(event: ClipboardEvent) {
  const source = Array.from(event.clipboardData?.files || []).find((file) => file.type.startsWith('image/'))
    || Array.from(event.clipboardData?.items || [])
      .find((item) => item.type.startsWith('image/'))
      ?.getAsFile()
  if (!source) return
  event.preventDefault()
  const extension = source.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png'
  const file = new File([source], `clipboard-holdings-${Date.now()}.${extension}`, { type: source.type })
  setSelectedFile(file)
  message.success('已从剪贴板读取持仓截图')
}

async function loadPortfolios() {
  portfolios.value = await api.listPortfolios()
  const requested = Number(route.query.portfolio)
  const preferred = portfolios.value.find((item) => item.id === requested)
    || portfolios.value.find((item) => item.is_default)
    || portfolios.value[0]
  portfolioId.value = preferred?.id || null
}

async function loadLatestSnapshot(id: number | null) {
  const portfolio = portfolios.value.find((item) => item.id === id)
  if (!portfolio?.latest_snapshot_id) {
    snapshot.value = null
    return
  }
  loadingLatestSnapshot.value = true
  try {
    snapshot.value = await api.getSnapshot(portfolio.latest_snapshot_id)
  } catch (error) {
    snapshot.value = null
    message.error((error as Error).message)
  } finally {
    loadingLatestSnapshot.value = false
  }
}

async function focusAnalysisPanel() {
  await nextTick()
  analysisPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function resumeJob(jobId: number) {
  const latest = await api.getAnalysisJob(jobId)
  job.value = latest
  portfolioId.value = latest.portfolio_id
  snapshot.value = await api.getSnapshot(latest.snapshot_id)
  if (!['succeeded', 'failed', 'cancelled'].includes(latest.status)) startJobPolling()
  await focusAnalysisPanel()
}

async function submitUpload() {
  if (!selectedFile.value) return
  loading.value = true
  try {
    if (!portfolioId.value) {
      const portfolio = await api.createPortfolio({ name: '默认组合', is_default: true })
      portfolios.value.push(portfolio)
      portfolioId.value = portfolio.id
      message.success('已自动创建默认持仓组合')
    }
    upload.value = await api.uploadHoldings(portfolioId.value, selectedFile.value)
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
  pollTimer = window.setInterval(async () => {
    if (!upload.value) return
    try {
      const latest = await api.getUpload(upload.value.id)
      upload.value = latest
      parsed.value = latest.parsed || parsed.value
      if (['waiting_confirmation', 'failed', 'needs_model', 'confirmed'].includes(latest.parsing_status)) stopPolling()
    } catch (error) {
      stopPolling()
      message.error((error as Error).message)
    }
  }, 1600)
}

function manualEntry() {
  parsed.value = parsed.value || emptyParsed()
  if (!parsed.value.holdings.length) addHolding()
}

function addHolding() {
  if (!parsed.value) parsed.value = emptyParsed()
  parsed.value.holdings.push({ code: '', name: '', qty: null, available_qty: null, cost: null, price: null, market_value: null, pnl: null, pnl_amount: null, extra: {} })
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
  if (!upload.value) return
  try {
    upload.value = await api.retryUploadParse(upload.value.id)
    startUploadPolling()
    message.info('已重新提交识图任务')
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function confirmHoldings(startAnalysis = false) {
  if (!upload.value) return
  confirming.value = true
  try {
    const saved = await saveParsed()
    if (!saved) return
    snapshot.value = await api.confirmUpload(upload.value.id)
    const portfolio = portfolios.value.find((item) => item.id === snapshot.value?.portfolio_id)
    if (portfolio && snapshot.value) {
      portfolio.latest_snapshot_id = snapshot.value.id
      portfolio.latest_snapshot_time = snapshot.value.snapshot_time
    }
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
  if (!snapshot.value || analysisStarting.value) return
  analysisStarting.value = true
  try {
    job.value = await api.createAnalysisJob(snapshot.value.id, analysisMode.value, checkpoint.value || undefined, notify.value)
    message.success('手动分析任务已创建')
    await router.replace({
      name: 'upload',
      query: { portfolio: snapshot.value.portfolio_id, job: job.value.id, focus: 'analysis' },
    })
    startJobPolling()
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    analysisStarting.value = false
  }
}

function startJobPolling() {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    if (!job.value) return
    try {
      job.value = await api.getAnalysisJob(job.value.id)
      if (terminalJob.value) {
        stopPolling()
        if (job.value.status === 'succeeded' && job.value.run_id) {
          message.success('分析完成')
        } else if (job.value.status === 'failed') {
          message.error(job.value.error_message || '分析失败')
        }
      }
    } catch (error) {
      stopPolling()
      message.error((error as Error).message)
    }
  }, 1400)
}

async function openReport() {
  if (job.value?.run_id) await router.push({ name: 'reports', query: { run: job.value.run_id } })
}

watch(portfolioId, async (id, previous) => {
  if (!initialized || id === previous) return
  clearDraft()
  snapshot.value = null
  await loadLatestSnapshot(id)
})

onMounted(async () => {
  window.addEventListener('paste', pasteImage)
  try {
    await loadPortfolios()
    const requestedJob = Number(route.query.job)
    if (requestedJob) {
      await resumeJob(requestedJob)
    } else {
      await loadLatestSnapshot(portfolioId.value)
      if (route.query.focus === 'analysis' && snapshot.value) await focusAnalysisPanel()
    }
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    initialized = true
  }
})

onUnmounted(() => {
  window.removeEventListener('paste', pasteImage)
  stopPolling()
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})
</script>

<template>
  <section class="page-stack">
    <div class="page-heading">
      <div><p class="eyebrow">DAILY HOLDINGS</p><h1>上传与手动分析</h1><p>上传新截图，或直接使用该组合最近一次已确认持仓发起分析。</p></div>
      <n-tag v-if="snapshot" type="success" :bordered="false"><CheckCircle2 :size="14" /> 当前快照 #{{ snapshot.id }}</n-tag>
    </div>

    <n-alert v-if="snapshot && !selectedFile && !upload" type="success" :show-icon="false">
      已载入 <strong>{{ selectedPortfolio?.name }}</strong> 的最新确认快照 #{{ snapshot.id }}（{{ fmt(snapshot.snapshot_time) }}），可以直接在页面下方手动触发分析，也可以上传新截图更新持仓。
    </n-alert>

    <div class="upload-grid">
      <section class="panel-card upload-panel">
        <div class="section-title"><div><h2>1. 上传新持仓（可选）</h2><p>支持选择文件或直接按 Ctrl + V 粘贴截图</p></div><FileImage :size="21" /></div>
        <n-form label-placement="top">
          <n-form-item label="持仓组合">
            <n-select v-model:value="portfolioId" :options="portfolios.map(p => ({ label: p.name, value: p.id }))" placeholder="未选择时自动创建默认组合" />
          </n-form-item>
          <label class="drop-zone" :class="{ selected: selectedFile }">
            <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" @change="selectFile" />
            <template v-if="previewUrl">
              <img class="drop-preview" :src="previewUrl" alt="待上传持仓截图预览" />
              <div class="preview-meta">
                <strong>{{ selectedFile?.name }}</strong>
                <span>{{ ((selectedFile?.size || 0) / 1024 / 1024).toFixed(2) }} MB · 点击可更换图片</span>
              </div>
            </template>
            <template v-else>
              <Camera :size="30" />
              <strong>点击选择持仓截图</strong>
              <span class="paste-hint"><ClipboardPaste :size="14" />也可在此页面按 Ctrl + V 粘贴图片</span>
            </template>
          </label>
          <n-button type="primary" block size="large" :disabled="!canUpload" :loading="loading" @click="submitUpload">
            {{ portfolios.length ? '上传并识别' : '创建默认组合并上传识别' }}
          </n-button>
        </n-form>
      </section>

      <section class="panel-card state-panel">
        <div class="section-title"><div><h2>处理状态</h2><p>新截图的识图结果必须经过你的确认</p></div><RefreshCw :size="20" :class="{ spinning: upload && ['uploaded', 'vision_parsing'].includes(upload.parsing_status) }" /></div>
        <div v-if="loadingLatestSnapshot" class="loading-state"><n-spin size="small" />正在读取最新确认快照</div>
        <n-empty v-else-if="!upload" :description="snapshot ? `当前可分析快照 #${snapshot.id}` : '上传截图后显示处理状态。'" />
        <template v-else>
          <div class="state-line"><span>上传编号</span><strong>#{{ upload.id }}</strong></div>
          <div class="state-line"><span>当前状态</span><n-tag :type="upload.parsing_status === 'failed' ? 'error' : upload.parsing_status === 'waiting_confirmation' ? 'warning' : upload.parsing_status === 'confirmed' ? 'success' : 'info'">{{ upload.parsing_status }}</n-tag></div>
          <n-progress v-if="['uploaded', 'vision_parsing'].includes(upload.parsing_status)" type="line" :percentage="upload.parsing_status === 'uploaded' ? 25 : 65" processing :show-indicator="false" />
          <n-alert v-if="upload.error_message" type="warning" :show-icon="false">{{ upload.error_message }}</n-alert>
          <n-alert v-if="upload.validation_errors.length" type="error" :show-icon="false">
            <div v-for="item in upload.validation_errors" :key="item">{{ item }}</div>
          </n-alert>
          <div class="state-actions">
            <n-button v-if="['failed', 'needs_model'].includes(upload.parsing_status)" secondary @click="retryVision">重新识别</n-button>
            <n-button v-if="!parsed" secondary @click="manualEntry">手工录入</n-button>
          </div>
        </template>
      </section>
    </div>

    <section v-if="parsed" class="panel-card holdings-panel">
      <div class="section-title">
        <div><h2>2. 核对并修正持仓</h2><p>总持仓与可用数量分别保存；不可用数量不会被视为已卖出</p></div>
        <div class="section-actions"><n-button secondary @click="addHolding"><template #icon><Plus :size="15" /></template>新增一行</n-button><n-button secondary :loading="saving" @click="saveParsed"><template #icon><Save :size="15" /></template>保存修正</n-button></div>
      </div>

      <div class="fund-grid">
        <n-form-item label="总资产"><n-input-number v-model:value="parsed.total_assets" :show-button="false" /></n-form-item>
        <n-form-item label="持仓总市值"><n-input-number v-model:value="parsed.total_market_value" :show-button="false" /></n-form-item>
        <n-form-item label="券商可用资金"><n-input-number v-model:value="parsed.broker_available_cash" :show-button="false" /></n-form-item>
        <n-form-item label="修正后未使用资金"><n-input-number v-model:value="parsed.corrected_unused_funds" :show-button="false" /></n-form-item>
      </div>

      <div class="holdings-table-wrap">
        <table class="edit-table">
          <thead><tr><th>股票代码（可选）</th><th>名称</th><th>总持仓</th><th>可用</th><th>成本</th><th>截图现价</th><th>市值</th><th>盈亏率</th><th>盈亏金额</th><th /></tr></thead>
          <tbody>
            <tr v-for="(holding, index) in parsed.holdings" :key="index">
              <td><n-input v-model:value="holding.code" placeholder="可留空" /></td>
              <td><n-input v-model:value="holding.name" placeholder="名称" /></td>
              <td><n-input-number v-model:value="holding.qty" :show-button="false" /></td>
              <td><n-input-number v-model:value="holding.available_qty" :show-button="false" /></td>
              <td><n-input-number v-model:value="holding.cost" :show-button="false" /></td>
              <td><n-input-number v-model:value="holding.price" :show-button="false" /></td>
              <td><n-input-number v-model:value="holding.market_value" :show-button="false" /></td>
              <td><n-input-number v-model:value="holding.pnl" :show-button="false" /></td>
              <td><n-input-number v-model:value="holding.pnl_amount" :show-button="false" /></td>
              <td><n-button quaternary circle type="error" @click="removeHolding(index)"><template #icon><Trash2 :size="15" /></template></n-button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <n-alert v-if="missingCodeCount" type="info" :show-icon="false">有 {{ missingCodeCount }} 个持仓未填写股票代码，确认快照后将在分析流程中由模型尝试匹配。</n-alert>
      <n-alert type="info" :show-icon="false">盈亏率使用小数，例如 -27.73% 应填写 -0.2773。减仓建议的数量上限是当前可用数量。</n-alert>
      <div class="confirm-row">
        <n-button secondary size="large" :disabled="!canConfirm" :loading="confirming" @click="confirmHoldings(false)">仅确认快照</n-button>
        <n-button type="success" size="large" :disabled="!canConfirm" :loading="confirming" @click="confirmHoldings(true)"><template #icon><Play :size="17" /></template>确认并立即分析</n-button>
      </div>
    </section>

    <section v-if="snapshot" ref="analysisPanel" class="panel-card analysis-panel">
      <div class="section-title">
        <div><h2>{{ parsed ? '3.' : '2.' }} 手动执行组合分析</h2><p>当前使用快照 #{{ snapshot.id }} · {{ fmt(snapshot.snapshot_time) }} · {{ snapshot.holdings.length }} 个持仓</p></div>
        <Play :size="21" />
      </div>
      <div class="analysis-form">
        <n-form-item label="分析模式"><n-radio-group v-model:value="analysisMode"><n-radio-button value="quick">快速</n-radio-button><n-radio-button value="deep">深度</n-radio-button></n-radio-group></n-form-item>
        <n-form-item label="检查点"><n-select v-model:value="checkpoint" :options="['09:35','10:00','12:00','14:30'].map(v => ({ label: v, value: v }))" /></n-form-item>
        <n-form-item label="完成后通知"><n-switch v-model:value="notify" /></n-form-item>
        <n-button type="primary" size="large" :loading="analysisStarting" :disabled="Boolean(job && !terminalJob)" @click="runAnalysis"><template #icon><Play :size="17" /></template>手动开始分析</n-button>
      </div>
      <div v-if="job" class="job-status">
        <div><strong>{{ stageLabels[job.current_stage] || job.current_stage }}</strong><span>{{ job.progress_percent }}%</span></div>
        <n-progress type="line" :percentage="job.progress_percent" :status="job.status === 'failed' ? 'error' : job.status === 'succeeded' ? 'success' : 'default'" :processing="!terminalJob" />
        <n-alert v-if="job.error_message" type="error" :show-icon="false">{{ job.error_message }}</n-alert>
        <div class="job-actions">
          <n-button v-if="job.status === 'succeeded' && job.run_id" type="primary" @click="openReport">查看完整报告</n-button>
          <n-button v-if="terminalJob" secondary @click="job = null">再次分析当前快照</n-button>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.page-stack { display: grid; gap: 18px; }
.page-heading { display: flex; align-items: end; justify-content: space-between; gap: 16px; }
.eyebrow { margin: 0 0 5px; color: var(--app-primary); font-size: 11px; font-weight: 900; letter-spacing: .13em; }
h1 { margin: 0; font-size: clamp(28px, 4vw, 42px); letter-spacing: -.035em; }
.page-heading p:not(.eyebrow), .section-title p { margin: 6px 0 0; color: var(--app-text-muted); }
.upload-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(300px, .65fr); gap: 16px; }
.panel-card { padding: 20px; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
.section-title h2 { margin: 0; font-size: 17px; }
.section-title p { font-size: 12px; }
.section-actions, .state-actions, .job-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.drop-zone { display: grid; min-height: 180px; place-items: center; align-content: center; gap: 8px; margin-bottom: 14px; overflow: hidden; border: 1px dashed var(--app-border); border-radius: 14px; background: var(--app-surface); color: var(--app-text-muted); cursor: pointer; }
.drop-zone input { display: none; }
.drop-zone.selected { min-height: 0; border-color: var(--app-primary); background: #05080d; color: var(--app-text); }
.drop-zone span { font-size: 11px; }
.paste-hint { display: inline-flex; align-items: center; gap: 5px; }
.drop-preview { display: block; width: 100%; height: clamp(220px, 36vh, 360px); object-fit: contain; }
.preview-meta { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px solid var(--app-border-soft); background: var(--app-surface); padding: 10px 12px; }
.preview-meta strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.preview-meta span { flex: none; color: var(--app-text-muted); }
.state-panel { align-self: start; }
.loading-state { display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 90px; color: var(--app-text-muted); }
.state-line { display: flex; align-items: center; justify-content: space-between; margin-bottom: 13px; border-bottom: 1px solid var(--app-border-soft); padding-bottom: 13px; }
.state-line span { color: var(--app-text-muted); }
.spinning { animation: spin 1.2s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.fund-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.holdings-table-wrap { margin-bottom: 14px; overflow-x: auto; }
.edit-table { width: 100%; min-width: 1240px; border-collapse: collapse; }
.edit-table th { padding: 8px; color: var(--app-text-muted); font-size: 11px; text-align: left; }
.edit-table td { min-width: 112px; border-top: 1px solid var(--app-border-soft); padding: 7px 5px; }
.edit-table td:first-child { min-width: 145px; }
.edit-table td:last-child { min-width: 40px; }
.confirm-row { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px; margin-top: 16px; }
.analysis-panel { scroll-margin-top: 18px; }
.analysis-form { display: grid; grid-template-columns: 1fr 1fr .6fr auto; align-items: end; gap: 16px; }
.job-status { display: grid; gap: 11px; margin-top: 18px; border-top: 1px solid var(--app-border-soft); padding-top: 18px; }
.job-status > div:first-child { display: flex; justify-content: space-between; }
@media (max-width: 950px) { .upload-grid { grid-template-columns: 1fr; } .fund-grid { grid-template-columns: repeat(2, 1fr); } .analysis-form { grid-template-columns: 1fr 1fr; } }
@media (max-width: 600px) { .page-heading, .section-title { align-items: start; flex-direction: column; } .fund-grid, .analysis-form { grid-template-columns: 1fr; } .section-actions { width: 100%; } .confirm-row .n-button { width: 100%; } }
</style>
