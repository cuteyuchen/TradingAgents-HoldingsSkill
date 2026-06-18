<script setup lang="ts">
import type { Claim } from '../api/types'

defineProps<{ claims: Claim[] }>()

const speakerLabel = (s: string): string =>
  ({ bull: '多头', bear: '空头', aggressive: '激进', conservative: '保守', neutral: '中立' }[s] || s)

const speakerClass = (s: string): string =>
  ({
    bull: 'spk-bull', bear: 'spk-bear',
    aggressive: 'spk-bull', conservative: 'spk-bear', neutral: 'spk-neutral',
  }[s] || 'spk-neutral')

const statusLabel = (s: string): string =>
  ({ open: '待回应', addressed: '已回应', resolved: '已定论', unresolved: '未解决' }[s] || s)

const statusClass = (s: string): string =>
  ({
    open: 'st-open', addressed: 'st-addr', resolved: 'st-res', unresolved: 'st-unres',
  }[s] || 'st-open')
</script>

<template>
  <table>
    <thead>
      <tr>
        <th style="width: 70px">Claim ID</th>
        <th style="width: 60px">方</th>
        <th>论点</th>
        <th>证据</th>
        <th style="width: 70px">置信度</th>
        <th style="width: 80px">状态</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="c in claims" :key="c.claim_id" :class="{ 'claim-unres': c.status === 'unresolved' }">
        <td><code>{{ c.claim_id }}</code></td>
        <td><span class="tag" :class="speakerClass(c.speaker)">{{ speakerLabel(c.speaker) }}</span></td>
        <td>{{ c.claim }}</td>
        <td class="evidence">
          <span v-for="(e, i) in (c.evidence || [])" :key="i">• {{ e }} </span>
        </td>
        <td>{{ c.confidence != null ? c.confidence.toFixed(2) : '—' }}</td>
        <td><span class="tag" :class="statusClass(c.status)">{{ statusLabel(c.status) }}</span></td>
      </tr>
      <tr v-if="!claims.length"><td colspan="6" class="muted">无 claim 记录</td></tr>
    </tbody>
  </table>
</template>

<style scoped>
.evidence { color: #86909c; font-size: 12px; }
.claim-unres { background: #fffbe6; }
.spk-bull { background: #fdecec; color: #cf1322; }
.spk-bear { background: #e8f7ea; color: #00a854; }
.spk-neutral { background: #eef2ff; color: #4e83f0; }
.st-open { background: #f2f3f5; color: #86909c; }
.st-addr { background: #eef2ff; color: #4e83f0; }
.st-res { background: #e8f7ea; color: #00a854; }
.st-unres { background: #fff1f0; color: #cf1322; }
</style>
