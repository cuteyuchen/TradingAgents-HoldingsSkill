<script setup lang="ts">
import type { ResearchVerdict, PMFinal } from '../api/types'

defineProps<{
  title: string
  verdict?: ResearchVerdict | PMFinal | null
  accent?: 'blue' | 'green'
}>()

const ratingColor = (rating?: string | null): string => {
  if (!rating) return ''
  return ({ Buy: '#cf1322', Overweight: '#d48806', Hold: '#86909c', Underweight: '#4e83f0', Sell: '#00a854' }[rating]) || '#86909c'
}
</script>

<template>
  <div class="verdict-card" :class="accent">
    <div class="vc-head">{{ title }}</div>
    <div v-if="verdict" class="vc-body">
      <div class="vc-row">
        <span class="vc-label">评级</span>
        <span class="vc-rating" :style="{ color: ratingColor(verdict.rating) }">{{ verdict.rating || '—' }}</span>
      </div>
      <div v-if="'winner' in verdict && verdict.winner" class="vc-row">
        <span class="vc-label">胜出方</span>
        <span>{{ verdict.winner === 'bull' ? '多头' : '空头' }}</span>
      </div>
      <div v-if="'rationale' in verdict && verdict.rationale" class="vc-row">
        <span class="vc-label">理由</span>
        <span>{{ verdict.rationale }}</span>
      </div>
      <div v-if="'cash_target' in verdict && verdict.cash_target" class="vc-row">
        <span class="vc-label">现金目标</span>
        <span>{{ verdict.cash_target }}</span>
      </div>
      <div v-if="'confidence' in verdict && verdict.confidence" class="vc-row">
        <span class="vc-label">置信度</span>
        <span>{{ verdict.confidence }}</span>
      </div>
      <div v-if="'priority_notes' in verdict && verdict.priority_notes" class="vc-row">
        <span class="vc-label">备注</span>
        <span>{{ verdict.priority_notes }}</span>
      </div>
    </div>
    <div v-else class="muted">无裁决记录</div>
  </div>
</template>

<style scoped>
.verdict-card { border-radius: 8px; padding: 14px 18px; border: 1px solid #e5e6eb; border-left: 4px solid #4e83f0; background: #fff; }
.verdict-card.green { border-left-color: #00a854; }
.vc-head { font-weight: 600; font-size: 14px; margin-bottom: 10px; color: #1f2329; }
.vc-row { display: flex; gap: 12px; font-size: 13px; margin-bottom: 6px; }
.vc-label { color: #86909c; width: 64px; flex-shrink: 0; }
.vc-rating { font-weight: 700; font-size: 15px; }
</style>
