<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Camera, CheckCircle2, ClipboardPaste, Play, Plus, RefreshCw, Save } from 'lucide-vue-next'
import { fmtDateTime } from '@/utils/ui'
import V3DetailDrawer from '../components/V3DetailDrawer.vue'
import V3HoldingsIdentityTable from './V3HoldingsIdentityTable.vue'
import { useV3HoldingsUpdate } from './useV3HoldingsUpdate'

const props = defineProps<{
  modelValue: boolean
  portfolioId: number | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirmed: [snapshotId: number]
}>()

const router = useRouter()
const {
  show,
  portfolios,
  activePortfolioId,
  selectedFile,
  previewUrl,
  upload,
  parsed,
  snapshot,
  job,
  loading,
  saving,
  confirming,
  analysisStarting,
  error,
  analysisMode,
  checkpoint,
  notify,
  canUpload,
  identityIssueCount,
  canConfirm,
  snapshotIdentityBlocked,
  terminalJob,
  selectFile,
  submitUpload,
  manualEntry,
  addHolding,
  removeHolding,
  saveParsed,
  retryVision,
  confirmHoldings,
  runAnalysis,
  cancelAnalysis,
  retryAnalysis,
  openContext,
  close,
  setSelectedPortfolio,
} = useV3HoldingsUpdate({
  onConfirmed: async (snapshotRow) => {
    emit('confirmed', snapshotRow.id)
  },
})

const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => {
    if (!value) {
      close()
      emit('update:modelValue', false)
    }
  },
})

const statusText = computed(() => {
  const map: Record<string, string> = {
    uploaded: '已上传',
    vision_parsing: '识别中',
    identity_resolving: '正在匹配证券身份',
    waiting_confirmation: '待人工确认',
    confirmed: '已确认',
    failed: '识别失败',
    needs_model: '缺少识图模型',
  }
  return map[String(upload.value?.parsing_status || '').toLowerCase()] || String(upload.value?.parsing_status || '未知')
})

watch(() => props.modelValue, async (value) => {
  show.value = value
  if (value) {
    const jobId = Number((router.currentRoute.value.query.job as string) || 0) || null
    await openContext(props.portfolioId, jobId)
  } else {
    close()
  }
}, { immediate: true })
</script>

<template>
  <V3DetailDrawer
    v-model="open"
    title="更新持仓"
    subtitle="识别结果会停留在核对阶段，确认后才会成为当前组合快照。"
    data-testid="v3-holdings-update-drawer"
  >
    <div class="update" data-testid="v3-holdings-update-body">
      <section class="update__section">
        <header class="update__header">
          <h3>1. 上传截图</h3>
          <p>支持 PNG、JPEG、WebP，也可以直接粘贴截图。</p>
        </header>
        <label v-if="portfolios.length > 1" class="update__field">
          <span>更新组合</span>
          <select
            :value="activePortfolioId ?? ''"
            data-testid="v3-update-portfolio-select"
            @change="setSelectedPortfolio(Number(($event.target as HTMLSelectElement).value) || null)"
          >
            <option v-for="item in portfolios" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label class="dropzone" :class="{ 'dropzone--selected': selectedFile }">
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            data-testid="v3-update-file-input"
            @change="selectFile"
          />
          <template v-if="previewUrl">
            <img class="dropzone__preview" :src="previewUrl" alt="待上传持仓截图预览" />
            <span>{{ selectedFile?.name }}</span>
          </template>
          <template v-else>
            <Camera :size="24" />
            <strong>点击选择持仓截图</strong>
            <span class="dropzone__hint"><ClipboardPaste :size="14" /> 也可按 Ctrl + V 粘贴</span>
          </template>
        </label>
        <button
          type="button"
          class="btn btn--primary"
          data-testid="v3-update-submit"
          :disabled="!canUpload || loading"
          @click="submitUpload"
        >
          {{ portfolios.length ? '上传并识别' : '创建默认组合并上传识别' }}
        </button>
      </section>

      <section class="update__section">
        <header class="update__header">
          <h3>识别状态</h3>
          <RefreshCw :size="16" />
        </header>
        <div v-if="!upload" class="update__state">
          <CheckCircle2 v-if="snapshot" :size="16" />
          <span>
            {{ snapshot
              ? `最近确认快照 #${snapshot.id} · ${fmtDateTime(snapshot.snapshot_time)}`
              : '上传截图后，这里会显示识别结果。' }}
          </span>
        </div>
        <div v-if="!upload || !parsed" class="update__actions">
          <button type="button" class="btn" data-testid="v3-update-manual" @click="manualEntry">手工录入</button>
        </div>
        <template v-else>
          <div class="update__state-line">
            <span>当前状态</span>
            <strong data-testid="v3-update-status">{{ statusText }}</strong>
          </div>
          <p v-if="upload.error_message" class="update__alert">{{ upload.error_message }}</p>
          <p v-if="upload.validation_errors.length" class="update__alert update__alert--error">
            <span v-for="item in upload.validation_errors" :key="item">{{ item }}</span>
          </p>
          <div class="update__actions">
            <button
              v-if="['failed', 'needs_model'].includes(upload.parsing_status)"
              type="button"
              class="btn"
              data-testid="v3-update-retry"
              @click="retryVision"
            >重新识别</button>
          </div>
        </template>
      </section>

      <section v-if="parsed" class="update__section">
        <header class="update__header">
          <h3>2. 核对并修正</h3>
          <div class="update__actions">
            <button type="button" class="btn" @click="addHolding"><Plus :size="14" /> 新增一行</button>
            <button type="button" class="btn" :disabled="saving" @click="saveParsed"><Save :size="14" /> 保存修正</button>
          </div>
        </header>
        <div class="fund-grid">
          <label>总资产 <input v-model="parsed.total_assets" type="number" /></label>
          <label>持仓总市值 <input v-model="parsed.total_market_value" type="number" /></label>
          <label>券商可用资金 <input v-model="parsed.broker_available_cash" type="number" /></label>
          <label>修正后未使用资金 <input v-model="parsed.corrected_unused_funds" type="number" /></label>
        </div>
        <V3HoldingsIdentityTable
          :holdings="parsed.holdings"
          :portfolio-id="activePortfolioId"
          @remove="removeHolding"
        />
        <p v-if="identityIssueCount" class="update__alert">
          还有 {{ identityIssueCount }} 个持仓未确认证券身份。请先补全或确认代码，再保存为正式持仓快照。
        </p>
        <div class="update__confirm">
          <button
            type="button"
            class="btn"
            data-testid="v3-update-confirm"
            :disabled="!canConfirm || confirming"
            @click="confirmHoldings(false)"
          >仅确认快照</button>
          <button
            type="button"
            class="btn btn--primary"
            data-testid="v3-update-confirm-analyze"
            :disabled="!canConfirm || confirming"
            @click="confirmHoldings(true)"
          ><Play :size="14" /> 确认并立即分析</button>
        </div>
      </section>

      <section v-if="snapshot" class="update__section">
        <header class="update__header">
          <h3>3. 手动执行组合分析</h3>
          <p>当前使用快照 #{{ snapshot.id }} · {{ fmtDateTime(snapshot.snapshot_time) }}</p>
        </header>
        <p v-if="snapshotIdentityBlocked" class="update__alert">
          证券身份不完整。该快照保留审计历史，但不会作为新的分析默认输入。
        </p>
        <div class="analysis-form">
          <label>
            分析模式
            <select v-model="analysisMode" data-testid="v3-update-mode">
              <option value="fast">快速</option>
              <option value="standard">标准</option>
              <option value="deep">深度</option>
            </select>
          </label>
          <label>
            检查点
            <select v-model="checkpoint">
              <option v-for="value in ['09:35', '10:30', '13:05', '14:30', '15:10']" :key="value" :value="value">{{ value }}</option>
            </select>
          </label>
          <label class="check">
            <input v-model="notify" type="checkbox" /> 完成后通知
          </label>
          <button
            type="button"
            class="btn btn--primary"
            data-testid="v3-update-run-analysis"
            :disabled="Boolean(snapshotIdentityBlocked || analysisStarting || (job && !terminalJob))"
            @click="runAnalysis"
          ><Play :size="14" /> 开始分析</button>
        </div>
        <div v-if="job" class="job" data-testid="v3-update-job">
          <div class="job__row">
            <strong data-testid="v3-update-job-status">
              {{ job.status === 'succeeded' ? '分析完成' : job.status === 'failed' ? '分析暂时失败' : job.status === 'cancelled' ? '已取消' : job.current_stage }}
            </strong>
            <span>{{ job.progress_percent }}%</span>
          </div>
          <p v-if="job.error_message" class="update__alert update__alert--error" data-testid="v3-update-job-error">{{ job.error_message }}</p>
          <div class="update__actions">
            <button
              v-if="job.status === 'succeeded' && job.run_id"
              type="button"
              class="btn btn--primary"
              data-testid="v3-update-view-analysis"
              @click="router.push({ name: 'analysis', query: { run: job.run_id, portfolio: snapshot.portfolio_id } })"
            >
              查看今日分析
            </button>
            <button
              v-if="['queued', 'running'].includes(job.status)"
              type="button"
              class="btn"
              data-testid="v3-update-cancel-job"
              @click="cancelAnalysis"
            >取消任务</button>
            <button
              v-if="job.status === 'failed'"
              type="button"
              class="btn"
              data-testid="v3-update-retry-job"
              @click="retryAnalysis"
            >重新分析</button>
          </div>
        </div>
      </section>

      <p v-if="error" class="update__alert update__alert--error" data-testid="v3-update-error">{{ error }}</p>
    </div>
  </V3DetailDrawer>
</template>

<style scoped>
.update {
  display: grid;
  gap: var(--v3-space-4);
  padding: var(--v3-space-4);
}
.update__section {
  display: grid;
  gap: var(--v3-space-3);
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  padding: var(--v3-space-4);
}
.update__header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--v3-space-2);
}
.update__header h3 {
  margin: 0;
  font-size: var(--v3-font-size-lg);
}
.update__header p {
  margin: 4px 0 0;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.update__field,
.analysis-form label,
.fund-grid label {
  display: grid;
  gap: 4px;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.update__field select,
.analysis-form select,
.fund-grid input {
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text);
  padding: 6px 8px;
  font: inherit;
}
.dropzone {
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  min-height: 160px;
  border: 1px dashed var(--v3-border-strong);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface-muted);
  color: var(--v3-text-muted);
  cursor: pointer;
  padding: var(--v3-space-3);
}
.dropzone input { display: none; }
.dropzone--selected { min-height: 0; border-color: var(--v3-primary); }
.dropzone__preview {
  display: block;
  width: 100%;
  max-height: 280px;
  object-fit: contain;
}
.dropzone__hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
}
.update__state,
.update__state-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--v3-text-muted);
  min-height: 40px;
}
.update__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.update__alert {
  margin: 0;
  color: var(--v3-status-warning);
  font-size: var(--v3-font-size-sm);
}
.update__alert--error { color: var(--v3-status-danger); }
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text-secondary);
  padding: 8px 12px;
  cursor: pointer;
  font: inherit;
}
.btn--primary {
  border-color: var(--v3-primary);
  background: var(--v3-primary);
  color: var(--v3-text-inverse);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.fund-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.update__confirm {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.analysis-form {
  display: grid;
  grid-template-columns: 1fr 1fr auto auto;
  gap: 12px;
  align-items: end;
}
.analysis-form .check {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--v3-text-secondary);
}
.job {
  display: grid;
  gap: 8px;
  border-top: 1px solid var(--v3-border-subtle);
  padding-top: 12px;
}
.job__row {
  display: flex;
  justify-content: space-between;
}
@media (max-width: 680px) {
  .fund-grid,
  .analysis-form { grid-template-columns: 1fr; }
  .update__confirm .btn { width: 100%; }
}
</style>
