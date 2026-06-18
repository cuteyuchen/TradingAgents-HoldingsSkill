<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type { Claim, RunDetail } from '../api/types'
import QualityGateTable from '../components/QualityGateTable.vue'
import DebateTimeline from '../components/DebateTimeline.vue'
import ClaimTable from '../components/ClaimTable.vue'
import VerdictCard from '../components/VerdictCard.vue'

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

const fmtPct = (v?: number | null): string => (v == null ? '—' : (v * 100).toFixed(2) + '%')
const pctClass = (v?: number | null): string => (v == null ? '' : v >= 0 ? 'pos' : 'neg')
const fmtBlock = (v: unknown): string => (typeof v === 'string' ? v : JSON.stringify(v, null, 2))

onMounted(async () => {
  try {
    run.value = await api.getRun(props.id)
  } catch (e) {
    err.value = (e as Error).message
  }
})
</script>

<template>
  <div v-if="err" class="card"><n-alert type="error" :show-icon="false">{{ err }}</n-alert></div>
  <div v-else-if="!run" class="card muted" style="padding: 24px">加载中...</div>
  <div v-else class="detail-page">
    <div class="card summary-card">
      <div class="detail-title-row">
        <h3>① 证据包</h3>
        <span v-if="run.data_quality_grade" class="tag" :class="`grade-${run.data_quality_grade}`">
          数据质量 {{ run.data_quality_grade }}
        </span>
      </div>
      <div class="kv">
        <span><b>时间：</b>{{ run.timestamp.slice(0, 16).replace('T', ' ') }}</span>
        <span><b>检查点：</b>{{ run.checkpoint || '—' }}</span>
        <span><b>持仓来源：</b>{{ run.holdings_source || '—' }}</span>
        <span><b>数据质量：</b>{{ run.data_quality_grade || '—' }}</span>
      </div>
      <div v-if="run.intent" class="kv">
        <span v-if="run.intent.tickers"><b>标的：</b>{{ run.intent.tickers.join(', ') }}</span>
        <span v-if="run.intent.horizon"><b>周期：</b>{{ run.intent.horizon }}</span>
        <span v-if="run.intent.focus"><b>关注：</b>{{ run.intent.focus.join(', ') }}</span>
        <span v-if="run.intent.risk_profile"><b>风险偏好：</b>{{ run.intent.risk_profile }}</span>
      </div>
    </div>

    <div v-if="run.transcript || sectionEntries.length" class="card">
      <h3>原始记录</h3>
      <pre v-if="run.transcript" class="transcript">{{ run.transcript }}</pre>
      <div v-if="sectionEntries.length" class="section-grid">
        <div v-for="section in sectionEntries" :key="section.key" class="section-block">
          <div class="section-title">{{ section.label }}</div>
          <pre>{{ fmtBlock(section.value) }}</pre>
        </div>
      </div>
    </div>

    <div v-if="run.quality_gates.length || run.data_quality_grade" class="card">
      <h3>② 质量门控</h3>
      <n-alert v-if="weakGrade" type="warning" :show-icon="false" class="mb-3">
        数据质量为 {{ run.data_quality_grade }}，以下缺口需要在裁决和交易动作中降权处理。
      </n-alert>
      <QualityGateTable :gates="run.quality_gates" />
    </div>

    <div class="card">
      <h3>③ 多空辩论</h3>
      <DebateTimeline :claims="invClaims" />
    </div>

    <div class="card">
      <h3>④ 研究总监裁决</h3>
      <VerdictCard title="研究总监" :verdict="run.research_verdict" />
    </div>

    <div class="card">
      <h3>⑤ 交易员方案</h3>
      <table class="data-table">
        <thead>
          <tr><th>代码</th><th>动作</th><th>触发价</th><th>数量/比例</th><th>止盈</th><th>止损</th><th>失效条件</th></tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in run.trader_proposals" :key="i">
            <td data-label="代码">{{ p.code }}</td>
            <td data-label="动作">{{ p.action || '—' }}</td>
            <td data-label="触发价">{{ p.trigger_price ?? '—' }}</td>
            <td data-label="数量/比例">{{ p.qty || '—' }}</td>
            <td data-label="止盈">{{ p.take_profit || '—' }}</td>
            <td data-label="止损">{{ p.stop_loss || '—' }}</td>
            <td data-label="失效条件" class="muted">{{ p.invalidation || '—' }}</td>
          </tr>
          <tr v-if="!run.trader_proposals.length"><td colspan="7" class="muted">无交易员方案</td></tr>
        </tbody>
      </table>
      <div v-for="(p, i) in run.trader_proposals" :key="'r' + i">
        <div v-if="p.revision && p.revision.verdict !== 'pass'" class="revision-box">
          <strong>风控修正 - {{ p.revision.verdict === 'revise' ? '退回修正' : '否决' }}</strong>
          <p class="muted">{{ p.revision.revision_reason }}</p>
          <div v-if="p.revision.hard_constraints?.length">
            <b>硬性约束：</b>{{ p.revision.hard_constraints.join('；') }}
          </div>
          <div v-if="p.revision.de_risk_triggers?.length">
            <b>去风险触发：</b>{{ p.revision.de_risk_triggers.join('；') }}
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>⑥ 三方风控辩论</h3>
      <ClaimTable v-if="riskClaims.length" :claims="riskClaims" />
      <div v-else-if="riskDebateFallback" class="fallback-box">
        <div class="muted mb-2">历史记录未写入结构化 RISK claim，以下为原始三方风控记录。</div>
        <pre>{{ fmtBlock(riskDebateFallback) }}</pre>
      </div>
      <n-empty v-else description="无三方风控记录" />
    </div>

    <div class="card">
      <h3>⑦ 组合经理最终决策</h3>
      <VerdictCard title="组合经理" accent="green" :verdict="run.pm_final" />
    </div>

    <div class="card">
      <h3>⑧ 今日买入/轮动候选</h3>
      <table class="data-table">
        <thead>
          <tr><th>候选</th><th>类型</th><th>评分</th><th>入场</th><th>仓位</th><th>止盈1/2</th><th>止损</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="(c, i) in run.candidates" :key="i">
            <td data-label="候选">{{ c.name }} <code>{{ c.code }}</code></td>
            <td data-label="类型">{{ c.type || '—' }}</td>
            <td data-label="评分">{{ c.score ?? '—' }}</td>
            <td data-label="入场" class="muted">{{ c.entry_trigger || '—' }}</td>
            <td data-label="仓位">{{ c.initial_size || '—' }}</td>
            <td data-label="止盈1/2">{{ [c.take_profit_1, c.take_profit_2].filter(Boolean).join(' / ') || '—' }}</td>
            <td data-label="止损">{{ c.stop_loss || '—' }}</td>
            <td data-label="状态">{{ c.status }}</td>
          </tr>
          <tr v-if="!run.candidates.length"><td colspan="8" class="muted">无候选</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>持仓快照（含 Alpha）</h3>
      <table class="data-table">
        <thead>
          <tr><th>代码</th><th>名称</th><th>现价</th><th>成本</th><th>盈亏</th><th>标的收益</th><th>沪深300</th><th>Alpha</th></tr>
        </thead>
        <tbody>
          <tr v-for="(h, i) in run.holdings" :key="i">
            <td data-label="代码"><code>{{ h.code }}</code></td>
            <td data-label="名称">{{ h.name || '—' }}</td>
            <td data-label="现价">{{ h.price ?? '—' }}</td>
            <td data-label="成本">{{ h.cost ?? '—' }}</td>
            <td data-label="盈亏" :class="pctClass(h.pnl)">{{ h.pnl != null ? (h.pnl * 100).toFixed(2) + '%' : '—' }}</td>
            <td data-label="标的收益" :class="pctClass(h.raw_return)">{{ fmtPct(h.raw_return) }}</td>
            <td data-label="沪深300" class="muted">{{ fmtPct(h.benchmark_return) }}</td>
            <td data-label="Alpha" :class="pctClass(h.alpha)">{{ fmtPct(h.alpha) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.detail-page {
  display: grid;
  gap: 20px;
  font-size: 15px;
  line-height: 1.65;
}

.detail-page .card {
  margin-bottom: 0;
  padding: 22px 24px;
  border-color: color-mix(in srgb, var(--app-border) 78%, var(--app-primary) 8%);
}

.detail-page .card h3 {
  margin-bottom: 18px;
  font-size: 19px;
  line-height: 1.35;
}

.detail-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.detail-title-row h3 {
  margin-bottom: 0;
}

.summary-card {
  border-color: color-mix(in srgb, var(--app-primary) 34%, var(--app-border));
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--app-primary) 10%, transparent), transparent 42%),
    var(--app-surface);
  box-shadow: var(--app-shadow-strong);
}

.detail-page .kv {
  gap: 12px;
  font-size: 15px;
}

.detail-page .kv + .kv {
  margin-top: 12px;
}

.detail-page .kv span {
  display: inline-flex;
  min-height: 36px;
  align-items: center;
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-surface-strong) 78%, transparent);
  padding: 6px 10px;
  box-shadow: inset 0 1px 0 color-mix(in srgb, #ffffff 20%, transparent);
}

.detail-page .kv b {
  color: var(--app-text-muted);
  font-weight: 700;
}

.revision-box {
  margin-top: 14px;
  border-left: 3px solid var(--app-warning);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-warning) 14%, transparent);
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.7;
}

.transcript,
.section-block pre,
.fallback-box pre {
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

.section-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.section-block {
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-surface-strong) 72%, transparent);
  padding: 14px;
}

.section-block pre {
  margin: 0;
  border: 0;
  background: transparent;
  padding: 0;
}

.section-title {
  margin-bottom: 8px;
  color: var(--app-primary);
  font-size: 15px;
  font-weight: 700;
}

.fallback-box {
  border: 1px dashed var(--app-border);
  border-radius: 8px;
  padding: 16px;
  background: color-mix(in srgb, var(--app-surface-strong) 62%, transparent);
}

.detail-page :deep(.data-table),
.detail-page :deep(table) {
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
  overflow: hidden;
}

.detail-page :deep(.data-table th),
.detail-page :deep(table th) {
  padding: 12px 14px;
  color: var(--app-text);
  font-size: 13px;
}

.detail-page :deep(.data-table td),
.detail-page :deep(table td) {
  padding: 13px 14px;
}

.detail-page :deep(.tag) {
  min-height: 28px;
  padding: 3px 11px;
  font-size: 13px;
}

.detail-page :deep(.round) {
  margin-bottom: 20px;
}

.detail-page :deep(.round-head) {
  margin-bottom: 10px;
  font-size: 15px;
}

.detail-page :deep(.evidence) {
  font-size: 14px;
  line-height: 1.65;
}

.detail-page :deep(.unres-box) {
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.7;
}

.detail-page :deep(.verdict-card) {
  border-color: color-mix(in srgb, var(--app-primary) 24%, var(--app-border));
  background: color-mix(in srgb, var(--app-surface-strong) 78%, transparent);
  padding: 18px 20px;
}

.detail-page :deep(.vc-head) {
  font-size: 16px;
}

.detail-page :deep(.vc-row) {
  gap: 16px;
  margin-bottom: 10px;
  font-size: 15px;
  line-height: 1.65;
}

.detail-page :deep(.vc-label) {
  width: 76px;
}

.detail-page :deep(.vc-rating) {
  font-size: 18px;
}

@media (max-width: 640px) {
  .detail-page {
    gap: 16px;
    font-size: 16px;
  }

  .detail-page .card {
    padding: 16px;
  }

  .detail-page .card h3 {
    font-size: 18px;
  }

  .detail-title-row {
    flex-direction: column;
    gap: 10px;
    margin-bottom: 14px;
  }

  .detail-page .kv {
    display: grid;
    grid-template-columns: 1fr;
    font-size: 15px;
  }

  .section-grid {
    grid-template-columns: 1fr;
  }

  .transcript,
  .section-block pre,
  .fallback-box pre {
    max-height: none;
    font-size: 14px;
  }

  .section-block {
    padding: 12px;
  }

  .detail-page :deep(.data-table),
  .detail-page :deep(table) {
    border: 0;
    font-size: 15px;
  }

  .detail-page :deep(.data-table td),
  .detail-page :deep(table td) {
    grid-template-columns: minmax(96px, 34%) 1fr;
    padding: 12px;
  }

  .detail-page :deep(.vc-row) {
    display: grid;
    grid-template-columns: 76px 1fr;
  }
}
</style>
