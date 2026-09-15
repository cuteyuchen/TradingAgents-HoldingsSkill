<script setup lang="ts">
import { computed } from 'vue'
import V3LoadingState from '../components/V3LoadingState.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import { formatNumber, formatPercentFromPoints, formatRatioPercent } from './dashboard-formatters'
import type {
  V3BreadthVM,
  V3ConcentrationVM,
  V3SystemicRiskVM,
  V3TurnoverVM,
  V3TypicalStockVM,
} from './dashboard-types'

const props = defineProps<{
  risk: V3SystemicRiskVM | null
  typical: V3TypicalStockVM | null
  breadth: V3BreadthVM | null
  turnover: V3TurnoverVM | null
  concentration: V3ConcentrationVM | null
  loading?: boolean
}>()

const trendLabel = computed(() => {
  const map = {
    rising: '趋向集中',
    falling: '趋向分散',
    flat: '大致持平',
    unavailable: '趋势未知',
  } as const
  return map[props.concentration?.trend || 'unavailable']
})
</script>

<template>
  <section class="systemic" data-testid="v3-systemic-risk-panel" aria-labelledby="systemic-title">
    <header class="systemic__header">
      <h2 id="systemic-title" class="systemic__title">系统性风险</h2>
      <V3LoadingState v-if="loading && !risk" label="加载风险…" :rows="1" variant="text" />
      <V3StatusBadge
        v-else-if="risk"
        :label="risk.level"
        :tone="risk.riskTone"
        size="md"
        data-testid="v3-risk-level"
      />
    </header>

    <div v-if="risk" class="systemic__risk-row">
      <div class="risk-block" :class="`risk-block--${risk.level.toLowerCase()}`">
        <span class="risk-block__label">风险等级</span>
        <strong class="risk-block__value" data-testid="v3-risk-level-text">{{ risk.level }}</strong>
        <span class="risk-block__score v3-number" data-testid="v3-risk-score">
          分值 {{ risk.score === null ? '—' : formatNumber(risk.score, 1) }}
        </span>
      </div>
      <ul v-if="risk.factors.length" class="risk-factors" data-testid="v3-risk-factors">
        <li v-for="factor in risk.factors.slice(0, 4)" :key="factor">{{ factor }}</li>
      </ul>
    </div>
    <p v-else class="systemic__empty">系统性风险暂不可用。</p>

    <div class="systemic__grid">
      <article class="panel" data-testid="v3-typical-stock">
        <h3 class="panel__title">典型个股表现</h3>
        <p class="panel__note">全市场个股中位表现，不是大盘指数。</p>
        <dl class="metrics">
          <div>
            <dt>全A中位日表现</dt>
            <dd class="v3-number" :class="typical && typical.medianDaily !== null ? (typical.medianDaily >= 0 ? 'up' : 'down') : ''" data-testid="v3-all-a-median">
              {{ formatPercentFromPoints(typical?.medianDaily ?? null, 2) }}
            </dd>
          </div>
          <div>
            <dt>20日趋势</dt>
            <dd class="v3-number" data-testid="v3-all-a-20d">{{ formatPercentFromPoints(typical?.trend20d ?? null, 2) }}</dd>
          </div>
          <div>
            <dt>250日分位</dt>
            <dd class="v3-number" data-testid="v3-all-a-250d">
              {{ typical?.percentile250d === null || typical?.percentile250d === undefined ? '—' : `${formatNumber(typical.percentile250d, 1)}%` }}
            </dd>
          </div>
        </dl>
      </article>

      <article class="panel" data-testid="v3-market-breadth">
        <h3 class="panel__title">涨跌家数 / 涨跌停</h3>
        <dl class="metrics metrics--compact">
          <div>
            <dt>上涨</dt>
            <dd class="v3-number up" data-testid="v3-breadth-up">{{ breadth ? formatNumber(breadth.advancers, 0) : '—' }}</dd>
          </div>
          <div>
            <dt>下跌</dt>
            <dd class="v3-number down" data-testid="v3-breadth-down">{{ breadth ? formatNumber(breadth.decliners, 0) : '—' }}</dd>
          </div>
          <div>
            <dt>平盘</dt>
            <dd class="v3-number" data-testid="v3-breadth-flat">{{ breadth ? formatNumber(breadth.unchanged, 0) : '—' }}</dd>
          </div>
          <div>
            <dt>涨跌比</dt>
            <dd class="v3-number" data-testid="v3-breadth-ratio">{{ breadth ? formatNumber(breadth.breadthRatio, 2) : '—' }}</dd>
          </div>
          <div>
            <dt>涨停</dt>
            <dd class="v3-number up" data-testid="v3-limit-up">{{ breadth ? formatNumber(breadth.limitUp, 0) : '—' }}</dd>
          </div>
          <div>
            <dt>跌停</dt>
            <dd class="v3-number down" data-testid="v3-limit-down">{{ breadth ? formatNumber(breadth.limitDown, 0) : '—' }}</dd>
          </div>
        </dl>
      </article>

      <article class="panel" data-testid="v3-turnover-panel">
        <h3 class="panel__title">成交额</h3>
        <dl class="metrics">
          <div>
            <dt>全市场成交额</dt>
            <dd class="v3-number" data-testid="v3-total-turnover">
              {{ turnover?.total === null || turnover?.total === undefined ? '—' : formatMoneyShort(turnover.total) }}
            </dd>
          </div>
          <div>
            <dt>20日均值</dt>
            <dd class="v3-number">
              {{ turnover?.totalAvg20d === null || turnover?.totalAvg20d === undefined ? '—' : formatMoneyShort(turnover.totalAvg20d) }}
            </dd>
          </div>
        </dl>
      </article>

      <article class="panel" data-testid="v3-top5-concentration">
        <h3 class="panel__title">Top 5% 成交集中度</h3>
        <dl class="metrics">
          <div>
            <dt>当前</dt>
            <dd class="v3-number" data-testid="v3-top5-current">{{ formatRatioPercent(concentration?.ratio ?? null, 1) }}</dd>
          </div>
          <div>
            <dt>20日均值</dt>
            <dd class="v3-number" data-testid="v3-top5-avg20">{{ formatRatioPercent(concentration?.avg20d ?? null, 1) }}</dd>
          </div>
          <div>
            <dt>趋势</dt>
            <dd data-testid="v3-top5-trend">{{ trendLabel }}</dd>
          </div>
        </dl>
        <svg
          v-if="concentration && concentration.history.length >= 2"
          class="sparkline"
          viewBox="0 0 120 28"
          role="img"
          :aria-label="`Top5集中度小趋势：当前 ${formatRatioPercent(concentration.ratio)}，20日均值 ${formatRatioPercent(concentration.avg20d)}`"
          data-testid="v3-top5-sparkline"
        >
          <polyline
            :points="sparkPoints(concentration.history)"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
          />
        </svg>
      </article>
    </div>
  </section>
</template>

<script lang="ts">
function formatMoneyShort(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}万亿`
  if (abs >= 1e8) return `${(value / 1e8).toFixed(1)}亿`
  if (abs >= 1e4) return `${(value / 1e4).toFixed(1)}万`
  return value.toFixed(0)
}

function sparkPoints(history: number[]): string {
  if (history.length < 2) return ''
  const min = Math.min(...history)
  const max = Math.max(...history)
  const span = max - min || 1
  return history
    .map((value, index) => {
      const x = (index / (history.length - 1)) * 116 + 2
      const y = 26 - ((value - min) / span) * 22
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}
</script>

<style scoped>
.systemic__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--v3-space-3);
  margin-bottom: var(--v3-space-3);
}
.systemic__title { margin: 0; font-size: var(--v3-font-size-xl); font-weight: 700; }
.systemic__empty { color: var(--v3-text-muted); }
.systemic__risk-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--v3-space-4);
  align-items: flex-start;
  margin-bottom: var(--v3-space-4);
}
.risk-block {
  display: grid;
  gap: 2px;
  min-width: 140px;
  border-radius: var(--v3-radius-md);
  padding: var(--v3-space-3);
  background: var(--v3-risk-unknown-soft);
}
.risk-block--low { background: var(--v3-risk-low-soft); color: var(--v3-risk-low); }
.risk-block--medium { background: var(--v3-risk-medium-soft); color: var(--v3-risk-medium); }
.risk-block--high,
.risk-block--extreme { background: var(--v3-risk-high-soft); color: var(--v3-risk-high); }
.risk-block__label { font-size: var(--v3-font-size-xs); opacity: 0.9; }
.risk-block__value { font-size: var(--v3-font-size-2xl); font-weight: 800; }
.risk-block__score { font-size: var(--v3-font-size-sm); }
.risk-factors {
  margin: 0;
  padding-left: 18px;
  color: var(--v3-text-secondary);
  font-size: var(--v3-font-size-sm);
}
.systemic__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--v3-space-3);
}
.panel {
  border: 1px solid var(--v3-border-subtle);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface-muted);
  padding: var(--v3-space-3);
}
.panel__title { margin: 0; font-size: var(--v3-font-size-md); font-weight: 700; }
.panel__note { margin: 2px 0 var(--v3-space-2); color: var(--v3-text-muted); font-size: var(--v3-font-size-xs); }
.metrics { display: grid; gap: var(--v3-space-2); margin: 0; }
.metrics--compact { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.metrics dt { color: var(--v3-text-muted); font-size: var(--v3-font-size-xs); }
.metrics dd { margin: 0; font-size: var(--v3-font-size-lg); font-weight: 700; }
.metrics dd.up { color: var(--v3-market-up); }
.metrics dd.down { color: var(--v3-market-down); }
.sparkline { width: 100%; max-width: 160px; height: 28px; margin-top: var(--v3-space-2); color: var(--v3-primary); }
@media (max-width: 800px) {
  .systemic__grid { grid-template-columns: 1fr; }
  .metrics--compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
