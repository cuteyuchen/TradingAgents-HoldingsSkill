<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import type { Holding, HoldingTimeline } from '../api/types'
import AlphaChart from '../components/AlphaChart.vue'

const route = useRoute()
const portfolio = ref<{ run_id: number | null; timestamp: string | null; holdings: Holding[] }>({ run_id: null, timestamp: null, holdings: [] })
const timeline = ref<HoldingTimeline | null>(null)
const selectedCode = ref<string>('')
const err = ref('')

async function loadPortfolio() {
  err.value = ''
  try {
    portfolio.value = await api.currentPortfolio()
    if (!selectedCode.value && portfolio.value.holdings.length) {
      selectedCode.value = portfolio.value.holdings[0].code
    }
    if (selectedCode.value) await loadTimeline(selectedCode.value)
  } catch (e) {
    err.value = (e as Error).message
  }
}

async function loadTimeline(code: string) {
  try {
    timeline.value = await api.holdingTimeline(code, 10)
  } catch (e) {
    err.value = (e as Error).message
  }
}

watch(() => route.params.code, (c) => { if (c) { selectedCode.value = c as string; loadTimeline(c as string) } })
onMounted(loadPortfolio)

const fmtPct = (v?: number | null): string => (v == null ? '—' : (v * 100).toFixed(2) + '%')
const pctClass = (v?: number | null): string => (v == null ? '' : v >= 0 ? 'pos' : 'neg')
</script>

<template>
  <div v-if="err" class="card"><n-alert type="error" :show-icon="false">{{ err }}</n-alert></div>
  <div class="card">
    <h3>当前持仓（{{ portfolio.timestamp ? portfolio.timestamp.slice(0, 16).replace('T', ' ') : '无' }}）</h3>
    <table class="data-table">
      <thead>
        <tr><th>代码</th><th>名称</th><th>现价</th><th>成本</th><th>盈亏</th><th>Alpha</th></tr>
      </thead>
      <tbody>
        <tr v-for="(h, i) in portfolio.holdings" :key="i"
            :class="{ active: selectedCode === h.code }"
            class="clickable"
            @click="selectedCode = h.code; loadTimeline(h.code)">
          <td data-label="代码"><code>{{ h.code }}</code></td>
          <td data-label="名称">{{ h.name || '—' }}</td>
          <td data-label="现价">{{ h.price ?? '—' }}</td>
          <td data-label="成本">{{ h.cost ?? '—' }}</td>
          <td data-label="盈亏" :class="pctClass(h.pnl)">{{ h.pnl != null ? (h.pnl * 100).toFixed(2) + '%' : '—' }}</td>
          <td data-label="Alpha" :class="pctClass(h.alpha)">{{ fmtPct(h.alpha) }}</td>
        </tr>
        <tr v-if="!portfolio.holdings.length"><td colspan="6" class="muted">暂无持仓</td></tr>
      </tbody>
    </table>
  </div>
  <div class="card" v-if="selectedCode">
    <h3>{{ selectedCode }} 收益 / Alpha 曲线</h3>
    <AlphaChart :points="timeline?.points || []" />
  </div>
  <div class="card" v-if="timeline">
    <h3>最近决策</h3>
    <div class="kv">
      <span><b>评级：</b>{{ timeline.verdict?.rating || '—' }}</span>
      <span><b>动作：</b>{{ timeline.proposal?.action || '—' }}</span>
      <span><b>触发价：</b>{{ timeline.proposal?.trigger_price ?? '—' }}</span>
      <span><b>止损：</b>{{ timeline.proposal?.stop_loss || '—' }}</span>
    </div>
  </div>
</template>

<style scoped>
.kv { display: flex; gap: 18px; font-size: 13px; flex-wrap: wrap; }
</style>
