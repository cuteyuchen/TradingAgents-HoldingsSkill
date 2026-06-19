<script setup lang="ts">
import { h } from 'vue'
import type { DataTableColumns } from 'naive-ui'
import type { Claim } from '../api/types'
import { emptyText, renderCode, renderSpeaker, renderStatus } from '../utils/ui'

defineProps<{ claims: Claim[] }>()

const columns: DataTableColumns<Claim> = [
  { title: '论点ID', key: 'claim_id', width: 92, render: (row) => renderCode(row.claim_id) },
  { title: '方', key: 'speaker', width: 82, render: (row) => renderSpeaker(row.speaker) },
  { title: '论点', key: 'claim', minWidth: 240 },
  {
    title: '证据',
    key: 'evidence',
    minWidth: 280,
    render: (row) =>
      row.evidence?.length
        ? h('div', { class: 'evidence-list' }, row.evidence.map((e) => h('span', { class: 'evidence-item' }, e)))
        : emptyText,
  },
  { title: '置信度', key: 'confidence', width: 88, render: (row) => (row.confidence != null ? row.confidence.toFixed(2) : emptyText) },
  { title: '状态', key: 'status', width: 96, render: (row) => renderStatus(row.status) },
]

const rowClassName = (row: Claim): string => row.status === 'unresolved' ? 'claim-unresolved-row' : ''
</script>

<template>
  <n-data-table
    :columns="columns"
    :data="claims"
    :bordered="false"
    :single-line="false"
    :scroll-x="920"
    :row-class-name="rowClassName"
    size="small"
  />
</template>

<style scoped>
.evidence-list {
  display: grid;
  gap: 6px;
  color: var(--app-text-muted);
  font-size: 13px;
  line-height: 1.6;
}

.evidence-item::before {
  color: var(--app-primary);
  content: "• ";
}

:deep(.claim-unresolved-row td) {
  background: color-mix(in srgb, var(--app-warning) 12%, transparent);
}
</style>
