<script setup lang="ts">
import type { QualityGate } from '../api/types'

defineProps<{ gates: QualityGate[] }>()

const gradeClass = (g?: string | null): string => (g ? `grade-${g}` : '')
</script>

<template>
  <table class="data-table">
    <thead>
      <tr>
        <th>分析师</th>
        <th style="width: 90px">硬检查</th>
        <th style="width: 90px">模型复审</th>
        <th style="width: 70px">评级</th>
        <th>关键缺失</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(g, i) in gates" :key="i">
        <td data-label="分析师">{{ g.analyst }}</td>
        <td data-label="硬检查">{{ g.hard_check || '—' }}</td>
        <td data-label="模型复审">{{ g.llm_review || '—' }}</td>
        <td data-label="评级"><span v-if="g.grade" class="tag" :class="gradeClass(g.grade)">{{ g.grade }}</span><span v-else>—</span></td>
        <td data-label="关键缺失" class="muted">{{ g.gaps || '—' }}</td>
      </tr>
      <tr v-if="!gates.length"><td colspan="5" class="muted">无质量门控记录</td></tr>
    </tbody>
  </table>
</template>
