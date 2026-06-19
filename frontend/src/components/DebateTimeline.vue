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
  <n-timeline v-if="claims.length" class="debate-timeline">
    <n-timeline-item
      v-for="[r, cs] in rounds"
      :key="r"
      type="info"
      :title="`第 ${r} 轮 - ${r === 1 ? '建立核心论点' : r === 2 ? '攻防核心论点' : '收敛结论'}`"
    >
      <ClaimTable :claims="cs" />
    </n-timeline-item>
  </n-timeline>
  <n-alert v-if="unresolved.length" type="warning" :show-icon="false" class="mt-3">
    <strong>未解决论点（裁决官必须处理）：</strong>
    <n-list class="mt-2" size="small">
      <n-list-item v-for="c in unresolved" :key="c.claim_id">
        <n-code :code="c.claim_id" inline /> {{ c.claim }}
      </n-list-item>
    </n-list>
  </n-alert>
  <n-empty v-if="!claims.length" description="无辩论记录" />
</template>

<style scoped>
.debate-timeline {
  margin-top: 4px;
}

:deep(.n-timeline-item-content__title) {
  color: var(--app-primary);
  font-weight: 800;
}
</style>
