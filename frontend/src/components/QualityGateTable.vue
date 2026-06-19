<script setup lang="ts">
import type { DataTableColumns } from 'naive-ui'
import type { QualityGate } from '../api/types'
import { analystLabel, qualityCheckLabel, renderGrade, renderMuted } from '../utils/ui'

defineProps<{ gates: QualityGate[] }>()

const columns: DataTableColumns<QualityGate> = [
  { title: '分析师', key: 'analyst', minWidth: 150, render: (row) => analystLabel(row.analyst) },
  { title: '硬检查', key: 'hard_check', width: 110, render: (row) => qualityCheckLabel(row.hard_check) },
  { title: '模型复审', key: 'llm_review', width: 120, render: (row) => qualityCheckLabel(row.llm_review) },
  { title: '评级', key: 'grade', width: 86, render: (row) => renderGrade(row.grade) },
  { title: '关键缺失', key: 'gaps', minWidth: 260, render: (row) => renderMuted(row.gaps) },
]
</script>

<template>
  <n-data-table
    :columns="columns"
    :data="gates"
    :bordered="false"
    :single-line="false"
    :scroll-x="760"
    size="small"
  />
</template>
