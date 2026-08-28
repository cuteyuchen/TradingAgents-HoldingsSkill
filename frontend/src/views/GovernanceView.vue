<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { FilePlus2, RefreshCw, ShieldCheck, Undo2 } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type {
  CalibrationReport,
  GovernanceEventListResponse,
  GovernanceHealth,
  GovernanceParameter,
  GovernanceRegistryResponse,
  ParameterChangeProposal,
  ParameterGovernanceEvent,
  ParameterSetVersion,
  ProposalListResponse,
} from '../api/types'

const message = useMessage()
const loading = ref(false)
const registry = ref<GovernanceRegistryResponse | null>(null)
const active = ref<ParameterSetVersion | null>(null)
const versions = ref<ParameterSetVersion[]>([])
const proposals = ref<ParameterChangeProposal[]>([])
const calibrations = ref<CalibrationReport[]>([])
const events = ref<ParameterGovernanceEvent[]>([])
const health = ref<GovernanceHealth | null>(null)

const calibrationModal = ref(false)
const manualModal = ref(false)
const calibrationForm = reactive({
  reportId: 0,
  proposedValue: '',
  reason: '',
})
const manualForm = reactive({
  key: '',
  proposedValue: '',
  reason: '',
  riskAcknowledged: false,
})

const suggestions = computed(() => {
  const proposalReportIds = new Set(
    proposals.value
      .filter((item) => item.source_calibration_report_id != null)
      .map((item) => item.source_calibration_report_id as number),
  )
  return calibrations.value.filter(
    (item) => item.recommendation === 'CONSIDER_CHANGE' && !proposalReportIds.has(item.id),
  )
})
const pendingProposals = computed(() => proposals.value.filter((item) =>
  ['DRAFT', 'PENDING_REVIEW', 'APPROVED'].includes(item.status),
))
const calibratableKeys = computed(() => Object.entries(registry.value?.registry || {}).filter(([, spec]) => spec.calibration_supported).map(([key]) => key))
const statusType = (status?: string | null): 'success' | 'warning' | 'error' | 'info' | 'default' => {
  const value = String(status || '').toUpperCase()
  if (['ACTIVE', 'APPROVED', 'PASS', 'OK', 'COMPLETED'].includes(value)) return 'success'
  if (['PENDING_REVIEW', 'DRAFT', 'DEGRADED', 'WARNING', 'SUPERSEDED'].includes(value)) return 'warning'
  if (['REJECTED', 'BLOCKED', 'ROLLED_BACK'].includes(value)) return 'error'
  return 'info'
}
const fmt = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const pretty = (value: unknown): string => {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
const specFor = (key: string): GovernanceParameter | undefined => registry.value?.registry?.[key]
const specLabel = (key: string) => specFor(key)?.display_name || key

function parseValue(raw: string): unknown {
  const value = raw.trim()
  if (!value) return undefined
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

async function load() {
  loading.value = true
  try {
    const [registryResult, versionsResult, proposalsResult, eventsResult, calibrationResult] = await Promise.all([
      api.getGovernanceParameters(),
      api.listParameterSets(),
      api.listGovernanceProposals(),
      api.listGovernanceEvents(),
      api.listCalibrations(),
    ])
    registry.value = registryResult
    versions.value = versionsResult.versions
    proposals.value = proposalsResult.proposals
    events.value = eventsResult.events
    calibrations.value = calibrationResult
    try {
      active.value = await api.getActiveParameterSet()
    } catch {
      active.value = null
    }
    try {
      health.value = await api.getGovernanceHealth()
    } catch {
      health.value = null
    }
  } catch (error) {
    message.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

function openCalibration(report: CalibrationReport) {
  calibrationForm.reportId = report.id
  calibrationForm.proposedValue = String(report.challenger_value ?? '')
  calibrationForm.reason = ''
  calibrationModal.value = true
}

async function createCalibrationProposal() {
  try {
    await api.createProposalFromCalibration({
      calibration_report_id: calibrationForm.reportId,
      proposed_value: parseValue(calibrationForm.proposedValue),
      reason: calibrationForm.reason || null,
    })
    calibrationModal.value = false
    message.success('提案已创建，等待提交审批')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function createManualProposal() {
  if (!manualForm.key) {
    message.warning('请选择目标参数')
    return
  }
  try {
    await api.createManualProposal({
      target_parameter_key: manualForm.key,
      proposed_value: parseValue(manualForm.proposedValue),
      reason: manualForm.reason,
      proposal_type: 'MANUAL_EXCEPTION',
      risk_acknowledged: manualForm.riskAcknowledged,
    })
    manualModal.value = false
    message.success('手工例外提案已创建')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function submitProposal(proposal: ParameterChangeProposal) {
  try {
    await api.submitGovernanceProposal(proposal.id)
    message.success('提案已提交审批')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function approveProposal(proposal: ParameterChangeProposal) {
  if (!window.confirm(`批准提案 #${proposal.id}？批准不会立即激活生产参数。`)) return
  try {
    await api.approveGovernanceProposal(proposal.id)
    message.success('已生成 APPROVED 参数版本，需再次显式激活')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function rejectProposal(proposal: ParameterChangeProposal) {
  if (!window.confirm(`拒绝提案 #${proposal.id}？`)) return
  try {
    await api.rejectGovernanceProposal(proposal.id)
    message.info('提案已拒绝')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function validateVersion(version: ParameterSetVersion) {
  try {
    await api.validateParameterSet(version.id)
    message.success('确定性验证已完成')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function activateVersion(version: ParameterSetVersion) {
  const emergency = !window.confirm(`激活参数版本 v${version.version}？当前 ACTIVE 将被 SUPERSEDED。`)
  const reason = window.prompt(emergency ? '紧急激活必须填写原因' : '激活原因（可留空）') || ''
  if (emergency && !reason) {
    message.warning('紧急激活必须填写原因')
    return
  }
  try {
    await api.activateParameterSet(version.id, {
      emergency_override: emergency,
      reason: reason || null,
      expected_active_version_id: active.value?.id ?? null,
    })
    message.success(`参数版本 v${version.version} 已激活`)
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

async function rollbackTo(version: ParameterSetVersion) {
  const reason = window.prompt(`创建回滚到 v${version.version} 的提案原因`) || ''
  if (!reason) return
  try {
    await api.createRollbackProposal(version.id, reason)
    message.info('回滚提案已创建，需要继续提交、审批并激活')
    await load()
  } catch (error) {
    message.error((error as Error).message)
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="governance-page">
    <div class="page-head">
      <div>
        <h1>参数治理</h1>
        <span class="muted">Research proposes · Humans approve · Governance versions · Production consumes</span>
      </div>
      <div class="head-actions">
        <n-tag v-if="health" :type="statusType(health.status)" size="large">
          {{ health.status }} {{ health.reasons.join(' / ') }}
        </n-tag>
        <n-button :loading="loading" @click="load"><template #icon><RefreshCw :size="16" /></template>刷新</n-button>
      </div>
    </div>

    <n-spin :show="loading">
      <div class="gov-grid">
        <section class="panel-card gov-section">
          <div class="section-title">
            <ShieldCheck :size="18" />
            <strong>当前 ACTIVE 版本</strong>
          </div>
          <template v-if="active">
            <div class="active-banner">
              <strong>v{{ active.version }}</strong>
              <span>{{ fmt(active.activated_at) }}</span>
              <n-tag :type="statusType(active.status)" size="small">{{ active.status }}</n-tag>
            </div>
            <div class="kv-list">
              <div><span>Config Hash</span><code>{{ active.config_hash }}</code></div>
              <div><span>Runtime</span><code>{{ active.runtime_contract_version }}</code></div>
              <div><span>Decision</span><code>{{ active.decision_contract_version }}</code></div>
              <div><span>Source</span><span>{{ active.source_proposal_id ? `Proposal #${active.source_proposal_id}` : (active.activation_reason || 'SYSTEM_BOOTSTRAP') }}</span></div>
            </div>
          </template>
          <n-empty v-else description="没有 ACTIVE 参数版本" />
        </section>

        <section class="panel-card gov-section">
          <div class="section-title">
            <FilePlus2 :size="18" />
            <strong>Calibration 建议</strong>
          </div>
          <div v-if="suggestions.length" class="suggestion-list">
            <div v-for="report in suggestions" :key="report.id" class="suggestion-row">
              <div>
                <strong>{{ report.target_parameter }}</strong>
                <span>#{{ report.id }} · {{ report.challenger_value !== undefined ? `建议 ${pretty(report.challenger_value)}` : '' }}</span>
              </div>
              <n-button size="small" @click="openCalibration(report)">Create Proposal</n-button>
            </div>
          </div>
          <n-empty v-else description="暂无 CONSIDER_CHANGE 建议" />
        </section>
      </div>

      <div class="panel-card gov-section">
        <div class="section-title">
          <FilePlus2 :size="18" />
          <strong>待处理提案</strong>
          <n-button size="small" secondary @click="manualModal = true">手工例外提案</n-button>
        </div>
        <div v-if="pendingProposals.length" class="proposal-list">
          <div v-for="proposal in pendingProposals" :key="proposal.id" class="proposal-row">
            <div class="proposal-main">
              <div class="proposal-title">
                <strong>#{{ proposal.id }} · {{ specLabel(proposal.target_parameter) }}</strong>
                <n-tag size="small" :type="statusType(proposal.status)">{{ proposal.status }}</n-tag>
              </div>
              <div class="proposal-values">
                <span><b>当前</b> {{ pretty(proposal.current_value) }}</span>
                <span>→</span>
                <span><b>拟变更</b> {{ pretty(proposal.proposed_value) }}</span>
              </div>
              <div class="muted small">{{ proposal.reason || '' }}</div>
            </div>
            <div class="proposal-actions">
              <n-button v-if="proposal.status === 'DRAFT'" size="small" @click="submitProposal(proposal)">提交审批</n-button>
              <template v-if="proposal.status === 'PENDING_REVIEW'">
                <n-button size="small" type="primary" @click="approveProposal(proposal)">批准</n-button>
                <n-button size="small" @click="rejectProposal(proposal)">拒绝</n-button>
              </template>
              <n-button v-if="proposal.status === 'APPROVED' && proposal.approved_version_id" size="small" @click="validateVersion(versions.find((v) => v.id === proposal.approved_version_id)!)">验证</n-button>
            </div>
          </div>
        </div>
        <n-empty v-else description="暂无待处理提案" />
      </div>

      <div class="panel-card gov-section">
        <div class="section-title"><Undo2 :size="18" /><strong>版本历史</strong></div>
        <div class="version-list">
          <div v-for="version in versions" :key="version.id" class="version-row">
            <div class="version-main">
              <div class="proposal-title">
                <strong>v{{ version.version }}</strong>
                <n-tag size="small" :type="statusType(version.status)">{{ version.status }}</n-tag>
              </div>
              <div class="muted small">
                {{ fmt(version.activated_at || version.approved_at || version.created_at) }}
                <template v-if="version.rollback_from_version_id"> · rollback from v{{ (versions.find((v) => v.id === version.rollback_from_version_id))?.version }}</template>
              </div>
              <code class="small">{{ version.config_hash }}</code>
            </div>
            <div class="version-actions">
              <n-button v-if="version.status === 'APPROVED'" size="small" @click="validateVersion(version)">验证</n-button>
              <n-button v-if="version.status === 'APPROVED'" size="small" type="primary" @click="activateVersion(version)">激活</n-button>
              <n-button v-if="version.status !== 'ACTIVE'" size="small" secondary @click="rollbackTo(version)">回滚提案</n-button>
            </div>
          </div>
        </div>
      </div>

      <div class="panel-card gov-section">
        <div class="section-title"><ShieldCheck :size="18" /><strong>治理审计</strong></div>
        <n-timeline v-if="events.length">
          <n-timeline-item v-for="event in events" :key="event.id" :title="event.event_type" :content="`#${event.id} · proposal ${event.proposal_id ?? '—'} · version ${event.parameter_set_version_id ?? '—'}`" :time="fmt(event.occurred_at)" />
        </n-timeline>
        <n-empty v-else description="暂无审计事件" />
      </div>
    </n-spin>

    <n-modal v-model:show="calibrationModal" preset="card" title="从 Calibration 创建提案" class="gov-modal">
      <n-form label-placement="top">
        <n-form-item label="目标参数">
          <n-input :value="calibrationForm.reportId ? (suggestions.find((item) => item.id === calibrationForm.reportId)?.target_parameter || '') : ''" disabled />
        </n-form-item>
        <n-form-item label="拟变更值">
          <n-input v-model:value="calibrationForm.proposedValue" placeholder="数值或 JSON" />
        </n-form-item>
        <n-form-item label="理由">
          <n-input v-model:value="calibrationForm.reason" type="textarea" :rows="3" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-button @click="calibrationModal = false">取消</n-button>
        <n-button type="primary" @click="createCalibrationProposal">创建提案</n-button>
      </template>
    </n-modal>

    <n-modal v-model:show="manualModal" preset="card" title="手工例外提案" class="gov-modal">
      <n-form label-placement="top">
        <n-form-item label="目标参数">
          <n-select v-model:value="manualForm.key" :options="calibratableKeys.map((key) => ({ label: `${specLabel(key)} (${key})`, value: key }))" filterable />
        </n-form-item>
        <n-form-item label="拟变更值">
          <n-input v-model:value="manualForm.proposedValue" placeholder="数值或 JSON" />
        </n-form-item>
        <n-form-item label="理由">
          <n-input v-model:value="manualForm.reason" type="textarea" :rows="3" />
        </n-form-item>
        <n-checkbox v-model:checked="manualForm.riskAcknowledged">已明确知悉风险与回滚计划</n-checkbox>
      </n-form>
      <template #footer>
        <n-button @click="manualModal = false">取消</n-button>
        <n-button type="primary" :disabled="!manualForm.riskAcknowledged" @click="createManualProposal">创建提案</n-button>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.governance-page { display: grid; gap: 18px; }
.page-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.page-head h1 { margin: 0 0 4px; font-size: 24px; }
.head-actions { display: flex; align-items: center; gap: 10px; }
.gov-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 18px; }
.gov-section { padding: 18px; }
.section-title { display: flex; align-items: center; gap: 9px; margin-bottom: 14px; }
.section-title strong { font-size: 15px; }
.section-title .n-button { margin-left: auto; }
.active-banner { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.active-banner strong { font-size: 22px; }
.kv-list { display: grid; gap: 8px; }
.kv-list > div { display: grid; grid-template-columns: 110px 1fr; gap: 8px; align-items: start; }
.kv-list span:first-child { color: var(--app-text-muted); }
.kv-list code { overflow-wrap: anywhere; }
.suggestion-list, .proposal-list, .version-list { display: grid; gap: 10px; }
.suggestion-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px solid var(--app-border-soft); border-radius: 8px; padding: 10px 12px; }
.suggestion-row > div { display: grid; gap: 2px; }
.suggestion-row span, .proposal-values span { color: var(--app-text-muted); font-size: 12px; }
.proposal-row, .version-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; border: 1px solid var(--app-border-soft); border-radius: 8px; padding: 12px; }
.proposal-main, .version-main { min-width: 0; display: grid; gap: 5px; }
.proposal-title { display: flex; align-items: center; gap: 8px; }
.proposal-values { display: flex; align-items: center; gap: 8px; }
.proposal-values b { color: var(--app-text); }
.proposal-actions, .version-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.small { font-size: 12px; }
.version-list code { overflow-wrap: anywhere; }
.gov-modal { width: min(560px, calc(100vw - 32px)); }
@media (max-width: 900px) {
  .gov-grid { grid-template-columns: 1fr; }
  .page-head { align-items: flex-start; flex-direction: column; }
  .proposal-row, .version-row, .suggestion-row { align-items: flex-start; flex-direction: column; }
}
</style>
