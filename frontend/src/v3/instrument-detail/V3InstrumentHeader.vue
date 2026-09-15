<script setup lang="ts">
import { computed } from 'vue'
import type { InstrumentIdentity } from '@/api/types'
import {
  DASH,
  formatExchangeLabel,
  formatInstrumentType,
} from './formatters'
import V3DataTimestamp from '../components/V3DataTimestamp.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'

const props = defineProps<{
  identity: InstrumentIdentity | null
  observedAt: string | null
  dataBasis: string | null
  quality: string | null
  stale: boolean
  fallback: boolean
  suspended: boolean
  loading?: boolean
}>()

const name = computed(() => props.identity?.name || DASH)
const code = computed(() => props.identity?.code || DASH)
const typeLabel = computed(() => formatInstrumentType(props.identity?.instrument_type))
const exchangeLabel = computed(() => formatExchangeLabel(props.identity?.exchange))
const board = computed(() => props.identity?.board || DASH)
</script>

<template>
  <header class="inst-header" data-testid="instrument-header">
    <div class="inst-header__main">
      <div class="inst-header__title-row">
        <h1 class="inst-header__name" data-testid="instrument-name">{{ name }}</h1>
        <span class="inst-header__code v3-number" data-testid="instrument-code">{{ code }}</span>
        <V3StatusBadge
          v-if="identity"
          :label="typeLabel"
          tone="info"
          data-testid="instrument-type"
        />
        <V3StatusBadge
          v-if="suspended || identity?.is_suspended"
          label="停牌"
          tone="warning"
          data-testid="instrument-suspended"
        />
        <V3StatusBadge
          v-if="identity?.is_st"
          label="ST"
          tone="warning"
          data-testid="instrument-st"
        />
      </div>
      <div class="inst-header__meta">
        <span data-testid="instrument-exchange">{{ exchangeLabel }}</span>
        <span aria-hidden="true">·</span>
        <span data-testid="instrument-board">板块 {{ board }}</span>
        <span aria-hidden="true">·</span>
        <span data-testid="instrument-status">{{ identity?.status || DASH }}</span>
      </div>
      <div class="inst-header__status">
        <V3StatusBadge
          v-if="quality"
          :label="`质量 ${quality}`"
          :tone="quality === 'A' ? 'success' : quality === 'B' ? 'info' : quality === 'F' ? 'danger' : 'warning'"
          data-testid="instrument-quality"
        />
        <V3StatusBadge v-if="stale" label="数据可能过期" tone="warning" data-testid="instrument-stale" />
        <V3StatusBadge v-if="fallback" label="备用数据源" tone="info" data-testid="instrument-fallback" />
        <V3DataTimestamp
          :at="observedAt"
          kind="quote"
          :quote-state="observedAt ? (dataBasis === 'live' ? '盘中' : dataBasis === 'session_close' ? '已收盘' : dataBasis === 'previous_session_close' ? '上一交易日收盘' : '') : '行情时间未知'"
          data-testid="instrument-observed-at"
        />
      </div>
    </div>
  </header>
</template>

<style scoped>
.inst-header {
  display: flex;
  flex-direction: column;
  gap: var(--v3-space-2);
  padding-bottom: var(--v3-space-3);
  border-bottom: 1px solid var(--v3-border-subtle);
}
.inst-header__title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--v3-space-2);
}
.inst-header__name {
  margin: 0;
  font-size: var(--v3-font-size-3xl);
  font-weight: 700;
  line-height: 1.25;
}
.inst-header__code {
  color: var(--v3-text-secondary);
  font-size: var(--v3-font-size-xl);
  font-weight: 600;
}
.inst-header__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.inst-header__status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--v3-space-2);
}
</style>
