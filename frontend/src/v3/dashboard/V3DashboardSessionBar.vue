<script setup lang="ts">
import { computed } from 'vue'
import V3DataTimestamp from '../components/V3DataTimestamp.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import type { V3SessionBarVM } from './dashboard-types'

const props = defineProps<{
  sessionBar: V3SessionBarVM
  loading?: boolean
  stale?: boolean
}>()

const basisLabel = computed(() => {
  const map: Record<string, string> = {
    live: '实时',
    session_close: '收盘快照',
    previous_session_close: '上一交易日',
  }
  return map[props.sessionBar.dataBasis] || props.sessionBar.dataBasis
})
</script>

<template>
  <div class="session-bar" data-testid="v3-dashboard-session-bar">
    <div class="session-bar__left">
      <V3StatusBadge
        :label="sessionBar.sessionLabel"
        :tone="sessionBar.sessionTone"
        size="md"
        data-testid="v3-session-label"
      />
      <V3StatusBadge :label="basisLabel" tone="neutral" size="sm" data-testid="v3-data-basis" />
      <span v-if="sessionBar.tradingDate" class="session-bar__date" data-testid="v3-trading-date">
        交易日 {{ sessionBar.tradingDate }}
      </span>
      <span v-if="sessionBar.isPreviousClose" class="session-bar__prev" data-testid="v3-previous-close">
        上一交易日收盘
      </span>
    </div>
    <div class="session-bar__right">
      <V3DataTimestamp
        :quote-at="sessionBar.quoteAt"
        :analysis-at="sessionBar.strategyAt"
        :quote-state="sessionBar.quoteStateText"
        compact
      />
      <span v-if="stale" class="session-bar__flag" data-testid="v3-stale-flag">过期</span>
      <span v-if="loading" class="session-bar__flag">刷新中</span>
    </div>
  </div>
</template>

<style scoped>
.session-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--v3-space-3);
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  padding: var(--v3-space-3) var(--v3-space-4);
}
.session-bar__left,
.session-bar__right {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--v3-space-2);
}
.session-bar__date {
  color: var(--v3-text-secondary);
  font-size: var(--v3-font-size-sm);
  font-weight: 600;
}
.session-bar__prev {
  border-radius: var(--v3-radius-sm);
  background: var(--v3-status-info-soft);
  color: var(--v3-status-info);
  padding: 2px 8px;
  font-size: var(--v3-font-size-xs);
  font-weight: 700;
}
.session-bar__flag {
  border-radius: var(--v3-radius-sm);
  background: var(--v3-status-warning-soft);
  color: var(--v3-status-warning);
  padding: 2px 8px;
  font-size: var(--v3-font-size-xs);
}
</style>
