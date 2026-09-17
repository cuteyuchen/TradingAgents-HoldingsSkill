/**
 * V3 Holdings update workflow controller.
 * Upload poller and analysis job poller are independent.
 * Close/unmount stops polling only — does not cancel backend jobs.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../api'
import type {
  AnalysisJob,
  AnalysisMode,
  HoldingUpload,
  ParsedHoldings,
  Portfolio,
  PortfolioSnapshot,
} from '../../api/types'
import { usePortfolioContext } from '../../composables/portfolio'

const TERMINAL_UPLOAD = new Set(['waiting_confirmation', 'failed', 'needs_model', 'confirmed'])
const TERMINAL_JOB = new Set(['succeeded', 'failed', 'cancelled'])

function emptyParsed(): ParsedHoldings {
  return { holdings: [], excluded_items: [], notes: [] }
}

export function useV3HoldingsUpdate(options: {
  onConfirmed?: (snapshot: PortfolioSnapshot) => void | Promise<void>
} = {}) {
  const { portfolios, selectedPortfolioId, setSelectedPortfolio } = usePortfolioContext()

  const show = ref(false)
  const selectedFile = ref<File | null>(null)
  const previewUrl = ref('')
  const upload = ref<HoldingUpload | null>(null)
  const parsed = ref<ParsedHoldings | null>(null)
  const snapshot = ref<PortfolioSnapshot | null>(null)
  const job = ref<AnalysisJob | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const confirming = ref(false)
  const analysisStarting = ref(false)
  const jobActionLoading = ref(false)
  const retryingUpload = ref(false)
  const error = ref<string | null>(null)
  const analysisMode = ref<AnalysisMode>('deep')
  const checkpoint = ref('10:30')
  const notify = ref(true)
  const targetPortfolioId = ref<number | null>(null)

  let uploadTimer: number | null = null
  let jobTimer: number | null = null
  let uploadBusy = false
  let jobBusy = false

  const activePortfolioId = computed(() => targetPortfolioId.value || selectedPortfolioId.value)
  const canUpload = computed(() => Boolean(selectedFile.value))
  const identityIssueCount = computed(() => parsed.value?.holdings.filter(
    (holding) => holding.resolution_status !== 'RESOLVED' || !holding.canonical_code || !holding.security_id,
  ).length || 0)
  const canConfirm = computed(() => Boolean(
    upload.value
    && parsed.value?.holdings.length
    && !upload.value.validation_errors.length
    && identityIssueCount.value === 0,
  ))
  const snapshotIdentityBlocked = computed(() => Boolean(
    snapshot.value && snapshot.value.identity_status && snapshot.value.identity_status !== 'RESOLVED',
  ))
  const terminalJob = computed(() => TERMINAL_JOB.has(job.value?.status || ''))

  function stopUploadPolling(): void {
    if (uploadTimer !== null) window.clearTimeout(uploadTimer)
    uploadTimer = null
    uploadBusy = false
  }

  function stopJobPolling(): void {
    if (jobTimer !== null) window.clearTimeout(jobTimer)
    jobTimer = null
    jobBusy = false
  }

  function stopAllPolling(): void {
    stopUploadPolling()
    stopJobPolling()
  }

  function clearPreview(): void {
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }

  function clearDraft(): void {
    stopUploadPolling()
    selectedFile.value = null
    clearPreview()
    upload.value = null
    parsed.value = null
    job.value = null
    error.value = null
  }

  function setSelectedFile(file: File | null): void {
    selectedFile.value = file
    clearPreview()
    previewUrl.value = file ? URL.createObjectURL(file) : ''
    upload.value = null
    parsed.value = null
    snapshot.value = null
    job.value = null
    error.value = null
  }

  function selectFile(event: Event): void {
    setSelectedFile((event.target as HTMLInputElement).files?.[0] || null)
  }

  function pasteImage(event: ClipboardEvent): void {
    const source = Array.from(event.clipboardData?.files || []).find((file) => file.type.startsWith('image/'))
      || Array.from(event.clipboardData?.items || []).find((item) => item.type.startsWith('image/'))?.getAsFile()
    if (!source) return
    event.preventDefault()
    const extension = source.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png'
    setSelectedFile(new File([source], `clipboard-holdings-${Date.now()}.${extension}`, { type: source.type }))
  }

  async function loadLatestSnapshot(id: number | null): Promise<void> {
    const portfolio = portfolios.value.find((item) => item.id === id)
    if (!portfolio?.latest_snapshot_id) {
      snapshot.value = null
      return
    }
    try {
      snapshot.value = await api.getSnapshot(portfolio.latest_snapshot_id)
    } catch {
      snapshot.value = null
    }
  }

  async function openContext(portfolioId: number | null, resumeJobId?: number | null): Promise<void> {
    targetPortfolioId.value = portfolioId
    if (portfolioId) setSelectedPortfolio(portfolioId)
    await loadLatestSnapshot(activePortfolioId.value)
    if (resumeJobId) await resumeJob(resumeJobId)
  }

  async function resumeJob(jobId: number): Promise<void> {
    job.value = await api.getAnalysisJob(jobId)
    targetPortfolioId.value = job.value.portfolio_id
    setSelectedPortfolio(job.value.portfolio_id)
    snapshot.value = await api.getSnapshot(job.value.snapshot_id)
    if (!terminalJob.value) startJobPolling()
  }

  async function submitUpload(): Promise<void> {
    if (!selectedFile.value || loading.value) return
    loading.value = true
    error.value = null
    try {
      let targetId = activePortfolioId.value
      if (!targetId) {
        const created = await api.createPortfolio({ name: '默认组合', is_default: true })
        portfolios.value = [...portfolios.value, created as Portfolio]
        setSelectedPortfolio(created.id)
        targetPortfolioId.value = created.id
        targetId = created.id
      }
      upload.value = await api.uploadHoldings(targetId, selectedFile.value)
      parsed.value = upload.value.parsed || null
      startUploadPolling()
    } catch (reason) {
      error.value = (reason as Error).message
    } finally {
      loading.value = false
    }
  }

  function startUploadPolling(): void {
    stopUploadPolling()
    const tick = async (): Promise<void> => {
      if (!show.value || !upload.value || uploadBusy) return
      uploadBusy = true
      try {
        const latest = await api.getUpload(upload.value.id)
        upload.value = latest
        parsed.value = latest.parsed || parsed.value
        if (TERMINAL_UPLOAD.has(latest.parsing_status)) return
      } catch (reason) {
        error.value = (reason as Error).message
      } finally {
        uploadBusy = false
      }
      if (show.value && upload.value && !TERMINAL_UPLOAD.has(upload.value.parsing_status)) {
        uploadTimer = window.setTimeout(() => { void tick() }, 1800)
      }
    }
    uploadTimer = window.setTimeout(() => { void tick() }, 800)
  }

  function startJobPolling(): void {
    stopJobPolling()
    const tick = async (): Promise<void> => {
      if (!show.value || !job.value || jobBusy) return
      jobBusy = true
      try {
        job.value = await api.getAnalysisJob(job.value.id)
        if (TERMINAL_JOB.has(job.value.status)) return
      } catch (reason) {
        error.value = (reason as Error).message
      } finally {
        jobBusy = false
      }
      if (show.value && job.value && !TERMINAL_JOB.has(job.value.status)) {
        jobTimer = window.setTimeout(() => { void tick() }, 1500)
      }
    }
    jobTimer = window.setTimeout(() => { void tick() }, 600)
  }

  function manualEntry(): void {
    parsed.value = parsed.value || emptyParsed()
    if (!parsed.value.holdings.length) addHolding()
  }

  function addHolding(): void {
    if (!parsed.value) parsed.value = emptyParsed()
    parsed.value.holdings.push({
      code: '',
      name: '',
      resolution_status: 'UNRESOLVED',
      qty: null,
      available_qty: null,
      cost: null,
      price: null,
      market_value: null,
      pnl: null,
      pnl_amount: null,
      extra: {},
    })
  }

  function removeHolding(index: number): void {
    parsed.value?.holdings.splice(index, 1)
  }

  async function saveParsed(): Promise<boolean> {
    if (!upload.value || !parsed.value) return false
    saving.value = true
    try {
      upload.value = await api.updateParsedHoldings(upload.value.id, parsed.value)
      parsed.value = upload.value.parsed || parsed.value
      return true
    } catch (reason) {
      error.value = (reason as Error).message
      return false
    } finally {
      saving.value = false
    }
  }

  async function retryVision(): Promise<void> {
    if (!upload.value || retryingUpload.value) return
    retryingUpload.value = true
    try {
      upload.value = await api.retryUploadParse(upload.value.id)
      startUploadPolling()
    } catch (reason) {
      error.value = (reason as Error).message
    } finally {
      retryingUpload.value = false
    }
  }

  async function confirmHoldings(startAnalysis = false): Promise<void> {
    if (!upload.value || confirming.value) return
    confirming.value = true
    error.value = null
    try {
      if (!await saveParsed()) return
      snapshot.value = await api.confirmUpload(upload.value.id)
      const portfolio = portfolios.value.find((item) => item.id === snapshot.value?.portfolio_id)
      if (portfolio && snapshot.value) {
        portfolio.latest_snapshot_id = snapshot.value.id
        portfolio.latest_snapshot_time = snapshot.value.snapshot_time
      }
      await options.onConfirmed?.(snapshot.value)
      if (startAnalysis) await runAnalysis()
    } catch (reason) {
      error.value = (reason as Error).message
    } finally {
      confirming.value = false
    }
  }

  async function runAnalysis(): Promise<void> {
    if (!snapshot.value || snapshotIdentityBlocked.value || analysisStarting.value) return
    analysisStarting.value = true
    try {
      job.value = await api.createAnalysisJob(
        snapshot.value.id,
        analysisMode.value,
        checkpoint.value || undefined,
        notify.value,
      )
      startJobPolling()
    } catch (reason) {
      error.value = (reason as Error).message
    } finally {
      analysisStarting.value = false
    }
  }

  async function cancelAnalysis(): Promise<void> {
    if (!job.value || terminalJob.value || jobActionLoading.value) return
    jobActionLoading.value = true
    try {
      job.value = await api.cancelAnalysisJob(job.value.id)
      stopJobPolling()
    } catch (reason) {
      error.value = (reason as Error).message
    } finally {
      jobActionLoading.value = false
    }
  }

  async function retryAnalysis(): Promise<void> {
    if (!job.value || job.value.status !== 'failed' || jobActionLoading.value) return
    jobActionLoading.value = true
    try {
      job.value = await api.retryAnalysisJob(job.value.id)
      startJobPolling()
    } catch (reason) {
      error.value = (reason as Error).message
    } finally {
      jobActionLoading.value = false
    }
  }

  function close(): void {
    show.value = false
    stopAllPolling()
  }

  function handlePaste(event: ClipboardEvent): void {
    if (show.value) pasteImage(event)
  }

  watch(show, (value, previous) => {
    if (value && !previous) {
      window.addEventListener('paste', handlePaste)
    }
    if (!value && previous) {
      window.removeEventListener('paste', handlePaste)
      stopAllPolling()
      // Preserve draft on ordinary close: keep selectedFile + previewUrl.
      // Revoke only on file replacement / clearDraft / unmount.
    }
  })

  onBeforeUnmount(() => {
    window.removeEventListener('paste', handlePaste)
    stopAllPolling()
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  })

  return {
    show,
    portfolios,
    selectedPortfolioId,
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
    jobActionLoading,
    retryingUpload,
    error,
    analysisMode,
    checkpoint,
    notify,
    canUpload,
    identityIssueCount,
    canConfirm,
    snapshotIdentityBlocked,
    terminalJob,
    setSelectedFile,
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
    resumeJob,
    close,
    setSelectedPortfolio: (id: number | null) => {
      targetPortfolioId.value = id
      setSelectedPortfolio(id)
      void loadLatestSnapshot(id)
    },
  }
}
