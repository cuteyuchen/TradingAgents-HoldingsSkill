<script setup lang="ts">
import { computed } from 'vue'
import ClaimTable from './ClaimTable.vue'
import type { Claim } from '../api/types'

const props = defineProps<{ claims: Claim[] }>()

// Group claims by round for the vertical timeline (optimization #10).
const rounds = computed(() => {
  const map = new Map<number, Claim[]>()
  for (const c of props.claims) {
    const r = c.round ?? 0
    if (!map.has(r)) map.set(r, [])
    map.get(r)!.push(c)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0])
})

const unresolved = computed(() => props.claims.filter((c) => c.status === 'unresolved'))
</script>

<template>
  <div>
    <div v-for="[r, cs] in rounds" :key="r" class="round">
      <div class="round-head">第 {{ r }} 轮 - {{ r === 1 ? '建立核心论点' : r === 2 ? '攻防核心论点' : '收敛结论' }}</div>
      <ClaimTable :claims="cs" />
    </div>
    <div v-if="unresolved.length" class="unres-box">
      <strong>未解决论点（裁决官必须处理）：</strong>
      <ul>
        <li v-for="c in unresolved" :key="c.claim_id">
          <code>{{ c.claim_id }}</code> {{ c.claim }}
        </li>
      </ul>
    </div>
    <div v-if="!claims.length" class="muted">无辩论记录</div>
  </div>
</template>

<style scoped>
.round { margin-bottom: 16px; }
.round-head { font-weight: 700; font-size: 13px; color: var(--app-primary); margin-bottom: 6px; }
.unres-box { background: color-mix(in srgb, var(--app-warning) 12%, transparent); border-left: 3px solid var(--app-warning); padding: 10px 14px; font-size: 13px; }
.unres-box ul { margin: 6px 0 0; padding-left: 18px; }
</style>
