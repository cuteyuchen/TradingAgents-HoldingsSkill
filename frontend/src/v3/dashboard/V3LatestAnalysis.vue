<script setup lang="ts">
import V3EmptyState from '../components/V3EmptyState.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import type { V3LatestAnalysisVM } from './dashboard-types'

defineProps<{
  analysis: V3LatestAnalysisVM
}>()

const emit = defineEmits<{ goAnalysis: [] }>()
</script>

<template>
  <section class="latest" data-testid="v3-latest-analysis" aria-labelledby="latest-analysis-title">
    <header class="latest__header">
      <h2 id="latest-analysis-title" class="latest__title">最新分析</h2>
      <V3StatusBadge
        v-if="analysis.analysisInProgress"
        label="分析进行中"
        tone="warning"
        data-testid="v3-analysis-running"
      />
      <V3StatusBadge
        v-else-if="analysis.isPrevious"
        label="上一版结论"
        tone="neutral"
      />
      <V3StatusBadge
        v-else-if="analysis.available"
        label="已完成"
        tone="info"
      />
      <V3StatusBadge v-else label="暂无分析" tone="neutral" />
    </header>

    <V3EmptyState
      v-if="!analysis.available && !analysis.analysisInProgress"
      title="暂无有效分析"
      description="今日尚无已完成的分析运行。"
      data-testid="v3-analysis-missing"
    >
      <template #actions>
        <button type="button" class="link-btn" @click="emit('goAnalysis')">查看分析</button>
      </template>
    </V3EmptyState>
    <div v-else class="latest__body">
      <p v-if="analysis.analysisInProgress && !analysis.available" class="latest__running" data-testid="v3-analysis-in-progress-text">
        分析进行中…
      </p>
      <dl class="latest__metrics">
        <div>
          <dt>分析时间</dt>
          <dd class="v3-number" data-testid="v3-analysis-finished-at">{{ analysis.finishedAt || '—' }}</dd>
        </div>
        <div>
          <dt>模式 / 运行</dt>
          <dd>{{ analysis.mode || '—' }}</dd>
        </div>
        <div>
          <dt>组合结论</dt>
          <dd data-testid="v3-analysis-conclusion">{{ analysis.conclusion || '—' }}</dd>
        </div>
        <div>
          <dt>质量 / 置信</dt>
          <dd>
            {{ analysis.quality || '—' }}
            <span v-if="analysis.confidence !== null"> / {{ analysis.confidence }}</span>
          </dd>
        </div>
      </dl>
      <button type="button" class="link-btn" @click="emit('goAnalysis')">查看分析</button>
    </div>
  </section>
</template>

<style scoped>
.latest__header {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
  margin-bottom: var(--v3-space-3);
}
.latest__title { margin: 0; font-size: var(--v3-font-size-xl); font-weight: 700; }
.latest__running { color: var(--v3-status-warning); font-weight: 600; }
.latest__metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--v3-space-3);
  margin: 0 0 var(--v3-space-3);
}
.latest__metrics dt { color: var(--v3-text-muted); font-size: var(--v3-font-size-xs); }
.latest__metrics dd { margin: 0; font-weight: 600; }
.link-btn {
  border: 0;
  background: none;
  color: var(--v3-primary);
  cursor: pointer;
  font-weight: 600;
  padding: 0;
}
</style>
