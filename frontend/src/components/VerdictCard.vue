<script setup lang="ts">
import type { ResearchVerdict, PMFinal } from '../api/types'

defineProps<{
  title: string
  verdict?: ResearchVerdict | PMFinal | null
  accent?: 'blue' | 'green'
}>()

const ratingType = (rating?: string | null): 'default' | 'success' | 'warning' | 'error' | 'info' => {
  if (!rating) return 'default'
  return ({
    Buy: 'error',
    Overweight: 'warning',
    Hold: 'default',
    Underweight: 'info',
    Sell: 'success',
  }[rating] || 'default') as 'default' | 'success' | 'warning' | 'error' | 'info'
}
</script>

<template>
  <n-card class="verdict-card" :class="accent" size="small" embedded>
    <template #header>{{ title }}</template>
    <n-descriptions v-if="verdict" label-placement="left" :column="1" size="small" bordered>
      <n-descriptions-item label="评级">
        <n-tag :type="ratingType(verdict.rating)" round :bordered="false">{{ verdict.rating || '—' }}</n-tag>
      </n-descriptions-item>
      <n-descriptions-item v-if="'winner' in verdict && verdict.winner" label="胜出方">
        {{ verdict.winner === 'bull' ? '多头' : '空头' }}
      </n-descriptions-item>
      <n-descriptions-item v-if="'rationale' in verdict && verdict.rationale" label="理由">
        {{ verdict.rationale }}
      </n-descriptions-item>
      <n-descriptions-item v-if="'cash_target' in verdict && verdict.cash_target" label="现金目标">
        {{ verdict.cash_target }}
      </n-descriptions-item>
      <n-descriptions-item v-if="'confidence' in verdict && verdict.confidence" label="置信度">
        {{ verdict.confidence }}
      </n-descriptions-item>
      <n-descriptions-item v-if="'priority_notes' in verdict && verdict.priority_notes" label="备注">
        {{ verdict.priority_notes }}
      </n-descriptions-item>
    </n-descriptions>
    <n-empty v-else description="无裁决记录" />
  </n-card>
</template>

<style scoped>
.verdict-card {
  border-left: 4px solid var(--app-primary);
}

.verdict-card.green {
  border-left-color: var(--app-success);
}

:deep(.n-descriptions-table-content) {
  line-height: 1.7;
  overflow-wrap: anywhere;
}

:deep(.n-descriptions-table-header) {
  width: 76px;
  min-width: 76px;
  white-space: nowrap;
  word-break: keep-all;
}
</style>
