<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type { RunDetail, Claim } from '../api/types'
import QualityGateTable from '../components/QualityGateTable.vue'
import DebateTimeline from '../components/DebateTimeline.vue'
import ClaimTable from '../components/ClaimTable.vue'
import VerdictCard from '../components/VerdictCard.vue'

const props = defineProps<{ id: string }>()
const run = ref<RunDetail | null>(null)
const err = ref('')

const invClaims = computed<Claim[]>(() => (run.value?.claims || []).filter((c) => c.claim_id.startsWith('INV-')))
const riskClaims = computed<Claim[]>(() => (run.value?.claims || []).filter((c) => c.claim_id.startsWith('RISK-')))

const fmtPct = (v?: number | null): string => (v == null ? '—' : (v * 100).toFixed(2) + '%')
const pctClass = (v?: number | null): string => (v == null ? '' : v >= 0 ? 'pos' : 'neg')

onMounted(async () => {
  try {
    run.value = await api.getRun(props.id)
  } catch (e) {
    err.value = (e as Error).message
  }
})
</script>

<template>
  <div v-if="err" class="card"><div class="err">{{ err }}</div></div>
  <div v-else-if="!run" class="card muted" style="padding: 24px">加载中…</div>
  <template v-else>
    <!-- 1. Evidence Pack -->
    <div class="card">
      <h3>① 证据包</h3>
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

    <!-- 2. Quality Gate -->
    <div class="card" v-if="run.data_quality_grade && run.data_quality_grade <= 'B'">
      <h3>② 质量门控</h3>
      <QualityGateTable :gates="run.quality_gates" />
    </div>

    <!-- 3. Bull/Bear Debate -->
    <div class="card">
      <h3>③ 多空辩论</h3>
      <DebateTimeline :claims="invClaims" />
    </div>

    <!-- 4. Research Manager Verdict -->
    <div class="card">
      <h3>④ 研究总监裁决</h3>
      <VerdictCard title="Research Manager" :verdict="run.research_verdict" />
    </div>

    <!-- 5. Trader Proposal (+ revision) -->
    <div class="card">
      <h3>⑤ 交易员方案</h3>
      <table>
        <thead>
          <tr><th>代码</th><th>动作</th><th>触发价</th><th>数量/比例</th><th>止盈</th><th>止损</th><th>失效条件</th></tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in run.trader_proposals" :key="i">
            <td>{{ p.code }}</td><td>{{ p.action || '—' }}</td><td>{{ p.trigger_price ?? '—' }}</td>
            <td>{{ p.qty || '—' }}</td><td>{{ p.take_profit || '—' }}</td><td>{{ p.stop_loss || '—' }}</td>
            <td class="muted">{{ p.invalidation || '—' }}</td>
          </tr>
          <tr v-if="!run.trader_proposals.length"><td colspan="7" class="muted">无交易员方案</td></tr>
        </tbody>
      </table>
      <div v-for="(p, i) in run.trader_proposals" :key="'r' + i">
        <div v-if="p.revision && p.revision.verdict !== 'pass'" class="revision-box">
          <strong>风控修正 — {{ p.revision.verdict === 'revise' ? '退回修正' : '否决' }}</strong>
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

    <!-- 6. Three-Way Risk Debate -->
    <div class="card">
      <h3>⑥ 三方风控辩论</h3>
      <ClaimTable :claims="riskClaims" />
    </div>

    <!-- 7. Portfolio Manager Final -->
    <div class="card">
      <h3>⑦ 组合经理最终决策</h3>
      <VerdictCard title="Portfolio Manager" accent="green" :verdict="run.pm_final" />
    </div>

    <!-- 8. Buy/Rotation Candidates -->
    <div class="card">
      <h3>⑧ 今日买入/轮动候选</h3>
      <table>
        <thead>
          <tr><th>候选</th><th>类型</th><th>评分</th><th>入场</th><th>仓位</th><th>止盈1/2</th><th>止损</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="(c, i) in run.candidates" :key="i">
            <td>{{ c.name }} <code>{{ c.code }}</code></td>
            <td>{{ c.type || '—' }}</td>
            <td>{{ c.score ?? '—' }}</td>
            <td class="muted">{{ c.entry_trigger || '—' }}</td>
            <td>{{ c.initial_size || '—' }}</td>
            <td>{{ [c.take_profit_1, c.take_profit_2].filter(Boolean).join(' / ') || '—' }}</td>
            <td>{{ c.stop_loss || '—' }}</td>
            <td>{{ c.status }}</td>
          </tr>
          <tr v-if="!run.candidates.length"><td colspan="8" class="muted">无候选</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Holdings snapshot with alpha -->
    <div class="card">
      <h3>持仓快照（含 Alpha）</h3>
      <table>
        <thead>
          <tr><th>代码</th><th>名称</th><th>现价</th><th>成本</th><th>盈亏</th><th>标的收益</th><th>沪深300</th><th>Alpha</th></tr>
        </thead>
        <tbody>
          <tr v-for="(h, i) in run.holdings" :key="i">
            <td><code>{{ h.code }}</code></td>
            <td>{{ h.name || '—' }}</td>
            <td>{{ h.price ?? '—' }}</td>
            <td>{{ h.cost ?? '—' }}</td>
            <td :class="pctClass(h.pnl)">{{ h.pnl != null ? (h.pnl * 100).toFixed(2) + '%' : '—' }}</td>
            <td :class="pctClass(h.raw_return)">{{ fmtPct(h.raw_return) }}</td>
            <td class="muted">{{ fmtPct(h.benchmark_return) }}</td>
            <td :class="pctClass(h.alpha)">{{ fmtPct(h.alpha) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</template>

<style scoped>
.kv { display: flex; flex-wrap: wrap; gap: 18px; font-size: 13px; margin-bottom: 6px; }
.revision-box { margin-top: 12px; background: #fff7e6; border-left: 3px solid #d48806; padding: 10px 14px; font-size: 13px; }
.err { color: #cf1322; font-size: 13px; }
</style>
