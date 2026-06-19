<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { DataTableColumns } from 'naive-ui'
import { api } from '../api'
import type { Candidate, Claim, Holding, RunDetail, TraderProposal } from '../api/types'
import QualityGateTable from '../components/QualityGateTable.vue'
import DebateTimeline from '../components/DebateTimeline.vue'
import ClaimTable from '../components/ClaimTable.vue'
import VerdictCard from '../components/VerdictCard.vue'
import {
  actionLabel,
  emptyText,
  fmtDateTime,
  fmtPct,
  renderInstrument,
  renderMuted,
  renderPnl,
  renderPct,
  renderStatus,
} from '../utils/ui'

const props = defineProps<{ id: string }>()
const run = ref<RunDetail | null>(null)
const err = ref('')

const invClaims = computed<Claim[]>(() =>
  (run.value?.claims || []).filter((c) => c.claim_id.startsWith('INV-') || c.speaker === 'bull' || c.speaker === 'bear'),
)
const riskClaims = computed<Claim[]>(() => (run.value?.claims || []).filter((c) => c.claim_id.startsWith('RISK-')))

const sectionLabels: Record<string, string> = {
  evidence: '证据包',
  quality_gate: '质量门控',
  investment_debate: '多空辩论',
  research_verdict: '研究总监裁决',
  trader_proposal: '交易员方案',
  risk_debate: '三方风控辩论',
  pm_final: '组合经理结论',
  candidates: '买入/轮动候选',
}
const sectionOrder = Object.keys(sectionLabels)
const sectionEntries = computed(() =>
  Object.entries(run.value?.sections || {})
    .sort((a, b) => sectionOrder.indexOf(a[0]) - sectionOrder.indexOf(b[0]))
    .map(([key, value]) => ({ key, label: sectionLabels[key] || key, value })),
)
const riskDebateFallback = computed(() => run.value?.sections?.risk_debate ?? null)
const weakGrade = computed(() => ['C', 'D', 'F'].includes(run.value?.data_quality_grade || ''))
const nameByCode = computed(() => {
  const map = new Map<string, string>()
  for (const h of run.value?.holdings || []) {
    if (h.code && h.name) map.set(h.code.toUpperCase(), h.name)
  }
  for (const c of run.value?.candidates || []) {
    if (c.code && c.name && !map.has(c.code.toUpperCase())) map.set(c.code.toUpperCase(), c.name)
  }
  return map
})
const screenshotDataUrl = computed(() => {
  const value = run.value?.screenshot?.data_url
  return typeof value === 'string' && value.startsWith('data:image/') ? value : ''
})
const screenshotEntries = computed(() => {
  const s = run.value?.screenshot
  if (!s) return []
  return [
    { label: '文件名', value: s.filename },
    { label: '格式', value: s.mime_type },
    { label: '来源', value: s.source },
    { label: '截图时间', value: s.captured_at },
  ].filter((item) => item.value)
})

const fmtBlock = (v: unknown): string => (typeof v === 'string' ? v : JSON.stringify(v, null, 2))
const gradeType = (grade?: string | null): 'success' | 'warning' | 'error' | 'default' =>
  !grade ? 'default' : grade === 'A' ? 'success' : grade === 'B' ? 'warning' : 'error'
const nameForCode = (code?: string | null): string | undefined =>
  code ? nameByCode.value.get(code.toUpperCase()) : undefined

const traderColumns: DataTableColumns<TraderProposal> = [
  { title: '标的', key: 'code', minWidth: 190, render: (row) => renderInstrument(nameForCode(row.code), row.code) },
  { title: '动作', key: 'action', width: 130, render: (row) => actionLabel(row.action) },
  { title: '触发价', key: 'trigger_price', width: 110, render: (row) => row.trigger_price ?? emptyText },
  { title: '数量/比例', key: 'qty', minWidth: 220, render: (row) => row.qty || emptyText },
  { title: '止盈', key: 'take_profit', minWidth: 160, render: (row) => row.take_profit || emptyText },
  { title: '止损', key: 'stop_loss', minWidth: 160, render: (row) => row.stop_loss || emptyText },
  { title: '失效条件', key: 'invalidation', minWidth: 260, render: (row) => renderMuted(row.invalidation) },
]

const candidateColumns: DataTableColumns<Candidate> = [
  { title: '候选', key: 'name', minWidth: 180, render: (row) => renderInstrument(row.name, row.code) },
  { title: '类型', key: 'type', width: 90, render: (row) => row.type || emptyText },
  { title: '评分', key: 'score', width: 82, render: (row) => row.score ?? emptyText },
  { title: '入场', key: 'entry_trigger', minWidth: 280, render: (row) => renderMuted(row.entry_trigger) },
  { title: '仓位', key: 'initial_size', minWidth: 220, render: (row) => row.initial_size || emptyText },
  {
    title: '止盈1/2',
    key: 'take_profit',
    minWidth: 150,
    render: (row) => [row.take_profit_1, row.take_profit_2].filter(Boolean).join(' / ') || emptyText,
  },
  { title: '止损', key: 'stop_loss', minWidth: 140, render: (row) => row.stop_loss || emptyText },
  { title: '状态', key: 'status', width: 120, render: (row) => renderStatus(row.status) },
]

const holdingColumns: DataTableColumns<Holding> = [
  { title: '标的', key: 'code', minWidth: 190, render: (row) => renderInstrument(row.name, row.code) },
  { title: '现价', key: 'price', width: 100, render: (row) => row.price ?? emptyText },
  { title: '成本', key: 'cost', width: 100, render: (row) => row.cost ?? emptyText },
  { title: '盈亏率', key: 'pnl', width: 130, render: (row) => renderPnl(row.pnl, row.pnl_amount) },
  { title: '标的收益', key: 'raw_return', width: 120, render: (row) => renderPct(row.raw_return) },
  { title: '沪深300', key: 'benchmark_return', width: 120, render: (row) => renderMuted(fmtPct(row.benchmark_return)) },
  { title: 'Alpha', key: 'alpha', width: 120, render: (row) => renderPct(row.alpha) },
]

onMounted(async () => {
  try {
    run.value = await api.getRun(props.id)
  } catch (e) {
    err.value = (e as Error).message
  }
})
</script>

<template>
  <n-alert v-if="err" type="error" :show-icon="false">{{ err }}</n-alert>
  <n-card v-else-if="!run">
    <n-skeleton text :repeat="4" />
  </n-card>
  <div v-else class="detail-page">
    <n-card class="summary-card" title="① 证据包">
      <template #header-extra>
        <n-tag v-if="run.data_quality_grade" :type="gradeType(run.data_quality_grade)" round :bordered="false">
          数据质量 {{ run.data_quality_grade }}
        </n-tag>
      </template>
      <n-descriptions :column="4" label-placement="left" bordered size="small">
        <n-descriptions-item label="时间">{{ fmtDateTime(run.timestamp) }}</n-descriptions-item>
        <n-descriptions-item label="检查点">{{ run.checkpoint || '—' }}</n-descriptions-item>
        <n-descriptions-item label="持仓来源">{{ run.holdings_source || '—' }}</n-descriptions-item>
        <n-descriptions-item label="数据质量">{{ run.data_quality_grade || '—' }}</n-descriptions-item>
        <n-descriptions-item v-if="run.intent?.tickers" label="标的">
          {{ run.intent.tickers.join(', ') }}
        </n-descriptions-item>
        <n-descriptions-item v-if="run.intent?.horizon" label="周期">{{ run.intent.horizon }}</n-descriptions-item>
        <n-descriptions-item v-if="run.intent?.focus" label="关注">
          {{ run.intent.focus.join(', ') }}
        </n-descriptions-item>
        <n-descriptions-item v-if="run.intent?.risk_profile" label="风险偏好">
          {{ run.intent.risk_profile }}
        </n-descriptions-item>
      </n-descriptions>
    </n-card>

    <n-card v-if="run.screenshot" title="持仓截图">
      <div class="screenshot-grid">
        <n-image
          v-if="screenshotDataUrl"
          class="screenshot-image"
          :src="screenshotDataUrl"
          object-fit="contain"
        />
        <n-descriptions v-if="screenshotEntries.length" :column="2" label-placement="left" bordered size="small">
          <n-descriptions-item
            v-for="item in screenshotEntries"
            :key="item.label"
            :label="item.label"
          >
            {{ item.value }}
          </n-descriptions-item>
        </n-descriptions>
        <pre v-if="!screenshotDataUrl" class="section-pre">{{ fmtBlock(run.screenshot) }}</pre>
      </div>
    </n-card>

    <n-card v-if="run.transcript || sectionEntries.length" title="原始记录">
      <n-collapse>
        <n-collapse-item v-if="run.transcript" title="完整 Transcript" name="transcript">
          <pre class="transcript">{{ run.transcript }}</pre>
        </n-collapse-item>
        <n-collapse-item
          v-for="section in sectionEntries"
          :key="section.key"
          :title="section.label"
          :name="section.key"
        >
          <pre class="section-pre">{{ fmtBlock(section.value) }}</pre>
        </n-collapse-item>
      </n-collapse>
    </n-card>

    <n-card v-if="run.quality_gates.length || run.data_quality_grade" title="② 质量门控">
      <n-alert v-if="weakGrade" type="warning" :show-icon="false" class="mb-3">
        数据质量为 {{ run.data_quality_grade }}，以下缺口需要在裁决和交易动作中降权处理。
      </n-alert>
      <QualityGateTable :gates="run.quality_gates" />
    </n-card>

    <n-card title="③ 多空辩论">
      <DebateTimeline :claims="invClaims" />
    </n-card>

    <n-card title="④ 研究总监裁决">
      <VerdictCard title="研究总监" :verdict="run.research_verdict" />
    </n-card>

    <n-card title="⑤ 交易员方案">
      <n-data-table
        :columns="traderColumns"
        :data="run.trader_proposals"
        :bordered="false"
        :single-line="false"
        :scroll-x="1150"
      />
      <div v-for="(p, i) in run.trader_proposals" :key="'r' + i">
        <n-alert v-if="p.revision && p.revision.verdict !== 'pass'" type="warning" :show-icon="false" class="mt-3">
          <strong>风控修正 - {{ p.revision.verdict === 'revise' ? '退回修正' : '否决' }}</strong>
          <p>{{ p.revision.revision_reason }}</p>
          <div v-if="p.revision.hard_constraints?.length">
            <b>硬性约束：</b>{{ p.revision.hard_constraints.join('；') }}
          </div>
          <div v-if="p.revision.de_risk_triggers?.length">
            <b>去风险触发：</b>{{ p.revision.de_risk_triggers.join('；') }}
          </div>
        </n-alert>
      </div>
    </n-card>

    <n-card title="⑥ 三方风控辩论">
      <ClaimTable v-if="riskClaims.length" :claims="riskClaims" />
      <n-alert v-else-if="riskDebateFallback" type="info" :show-icon="false">
        <div class="muted mb-2">历史记录未写入结构化 RISK claim，以下为原始三方风控记录。</div>
        <pre class="section-pre">{{ fmtBlock(riskDebateFallback) }}</pre>
      </n-alert>
      <n-empty v-else description="无三方风控记录" />
    </n-card>

    <n-card title="⑦ 组合经理最终决策">
      <VerdictCard title="组合经理" accent="green" :verdict="run.pm_final" />
    </n-card>

    <n-card title="⑧ 今日买入/轮动候选">
      <n-data-table
        :columns="candidateColumns"
        :data="run.candidates"
        :bordered="false"
        :single-line="false"
        :scroll-x="1260"
      />
    </n-card>

    <n-card title="持仓快照（含 Alpha）">
      <n-data-table
        :columns="holdingColumns"
        :data="run.holdings"
        :bordered="false"
        :single-line="false"
        :scroll-x="940"
      />
    </n-card>
  </div>
</template>

<style scoped>
.detail-page {
  display: grid;
  gap: 20px;
  font-size: 15px;
  line-height: 1.65;
}

.summary-card {
  border-color: color-mix(in srgb, var(--app-primary) 34%, var(--app-border));
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--app-primary) 10%, transparent), transparent 42%),
    var(--app-surface);
  box-shadow: var(--app-shadow-strong);
}

.transcript,
.section-pre {
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--app-border-soft);
  border-radius: 6px;
  background: color-mix(in srgb, var(--app-surface-strong) 78%, transparent);
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.8;
}

.transcript {
  max-height: 520px;
  overflow: auto;
  box-shadow: inset 0 1px 0 color-mix(in srgb, #ffffff 16%, transparent);
}

.screenshot-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}

.screenshot-image {
  width: min(100%, 860px);
  overflow: hidden;
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  background: var(--app-surface-strong);
}

.screenshot-image :deep(img) {
  display: block;
  max-width: 100%;
  max-height: 680px;
  object-fit: contain;
}

.detail-page :deep(.verdict-card) {
  border-color: color-mix(in srgb, var(--app-primary) 24%, var(--app-border));
  background: color-mix(in srgb, var(--app-surface-strong) 78%, transparent);
}

.detail-page :deep(.n-card-header) {
  font-size: 18px;
  font-weight: 800;
}

.detail-page :deep(.n-data-table) {
  font-size: 14px;
}

.detail-page :deep(.n-descriptions-table-content) {
  line-height: 1.7;
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .detail-page {
    gap: 16px;
    font-size: 16px;
  }

  .transcript,
  .section-pre {
    max-height: none;
    font-size: 14px;
  }

  .detail-page :deep(.n-card-header) {
    font-size: 17px;
  }
}
</style>
