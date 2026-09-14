<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronLeft, Copy } from 'lucide-vue-next'
import V3InstrumentDetail from './V3InstrumentDetail.vue'
import V3PageHeader from '../components/V3PageHeader.vue'
import { useV3Notify } from '../composables/useV3Notify'

const route = useRoute()
const router = useRouter()
const notify = useV3Notify()

const code = computed(() => String(route.params.code || ''))

function goBack() {
  if (window.history.length > 1) router.back()
  else void router.push({ name: 'dashboard' })
}

async function copyCode() {
  if (!code.value) return
  try {
    await navigator.clipboard.writeText(code.value)
    notify.success(`已复制 ${code.value}`)
  } catch {
    notify.info(code.value)
  }
}
</script>

<template>
  <div class="detail-page" data-testid="instrument-detail-page">
    <V3PageHeader
      eyebrow="市场"
      title="标的详情"
      description="统一身份 / 行情 / 时间语义 / 质量 / capability / provenance"
    >
      <template #actions>
        <button type="button" class="page-btn" data-testid="detail-back" @click="goBack">
          <ChevronLeft :size="16" aria-hidden="true" />
          返回
        </button>
        <button
          type="button"
          class="page-btn"
          aria-label="复制标的代码"
          data-testid="detail-copy-code"
          @click="copyCode"
        >
          <Copy :size="15" aria-hidden="true" />
          复制代码
        </button>
      </template>
    </V3PageHeader>

    <V3InstrumentDetail :code="code" />
  </div>
</template>

<style scoped>
.detail-page {
  min-width: 0;
  width: 100%;
  max-width: 100%;
  overflow-x: hidden;
  contain: inline-size;
}
.page-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--v3-border);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-surface);
  color: var(--v3-text-secondary);
  padding: 6px 12px;
  font-size: var(--v3-font-size-sm);
  font-weight: 600;
  cursor: pointer;
}
.page-btn:hover {
  border-color: var(--v3-primary);
  color: var(--v3-primary);
}
</style>
