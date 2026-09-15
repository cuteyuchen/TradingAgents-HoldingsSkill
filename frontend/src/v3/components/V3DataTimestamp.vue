<script setup lang="ts">
/**
 * V3 数据时间戳：行情时间与策略分析时间必须分开表达。
 * product-level hard constraint: quote/session vs strategy analysis.
 */
import { computed } from 'vue'
import { fmtDateTime, fmtTime } from '@/utils/ui'

export type V3TimestampKind = 'quote' | 'analysis'

const props = withDefaults(
  defineProps<{
    /** 行情/会话时间 */
    quoteAt?: string | null
    /** 策略分析时间 */
    analysisAt?: string | null
    /** 单时间戳模式的 kind */
    kind?: V3TimestampKind
    /** 单时间戳模式 */
    at?: string | null
    /** 行情状态文案，如「已收盘」 */
    quoteState?: string
    compact?: boolean
  }>(),
  {
    quoteAt: null,
    analysisAt: null,
    kind: 'quote',
    at: null,
    quoteState: '',
    compact: false,
  },
)

const hasDual = computed(() => Boolean(props.quoteAt) && Boolean(props.analysisAt))

function formatQuote(value: string | null): string {
  if (!value) return '—'
  return fmtTime(value)
}

function formatAnalysis(value: string | null): string {
  if (!value) return '—'
  return fmtTime(value)
}

function formatSingle(value: string | null): string {
  if (!value) return '—'
  return props.compact ? fmtTime(value) : fmtDateTime(value)
}
</script>

<template>
  <div class="v3-data-ts" :class="{ 'v3-data-ts--compact': compact }" data-testid="v3-data-timestamp">
    <!-- 双时间戳：行情 + 分析 -->
    <template v-if="hasDual">
      <span class="v3-data-ts__item" data-testid="v3-quote-ts">
        <span class="v3-data-ts__label">行情</span>
        <span class="v3-data-ts__value v3-number">{{ formatQuote(quoteAt) }}</span>
        <span v-if="quoteState" class="v3-data-ts__state">{{ quoteState }}</span>
      </span>
      <span class="v3-data-ts__sep" aria-hidden="true">·</span>
      <span class="v3-data-ts__item" data-testid="v3-analysis-ts">
        <span class="v3-data-ts__label">分析</span>
        <span class="v3-data-ts__value v3-number">{{ formatAnalysis(analysisAt) }}</span>
      </span>
    </template>
    <!-- 单时间戳模式 -->
    <span v-else class="v3-data-ts__item" :data-testid="kind === 'quote' ? 'v3-quote-ts' : 'v3-analysis-ts'">
      <span class="v3-data-ts__label">{{ kind === 'quote' ? '行情' : '分析' }}</span>
      <span class="v3-data-ts__value v3-number">{{ formatSingle(at) }}</span>
      <span v-if="quoteState && kind === 'quote'" class="v3-data-ts__state">{{ quoteState }}</span>
    </span>
  </div>
</template>

<style scoped>
.v3-data-ts {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
}
.v3-data-ts--compact { font-size: var(--v3-font-size-xs); }
.v3-data-ts__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.v3-data-ts__label {
  color: var(--v3-text-muted);
  opacity: 0.85;
}
.v3-data-ts__value {
  color: var(--v3-text-secondary);
  font-weight: 600;
}
.v3-data-ts__state {
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface-muted);
  padding: 1px 6px;
  font-size: 10px;
  color: var(--v3-text-muted);
}
.v3-data-ts__sep { opacity: 0.5; }
</style>
