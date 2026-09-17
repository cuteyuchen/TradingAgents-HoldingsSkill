<script setup lang="ts">
import { X } from 'lucide-vue-next'

defineProps<{ candidates: Array<Record<string, any>> }>()
const emit = defineEmits<{ close: []; select: [candidate: Record<string, any>] }>()
</script>

<template>
  <div class="dialog-mask" data-testid="v3-security-candidate-dialog" role="dialog" aria-modal="true" aria-label="选择证券">
    <div class="dialog">
      <header class="dialog__header">
        <h2>选择证券</h2>
        <button type="button" class="dialog__close" aria-label="关闭" @click="emit('close')">
          <X :size="16" />
        </button>
      </header>
      <div class="dialog__body">
        <table v-if="candidates.length" class="dialog__table">
          <thead>
            <tr><th>代码</th><th>名称</th><th>类型</th><th>交易所</th><th /></tr>
          </thead>
          <tbody>
            <tr v-for="candidate in candidates" :key="String(candidate.security_id || candidate.canonical_code || candidate.code)">
              <td>{{ candidate.canonical_code || candidate.code || '—' }}</td>
              <td>{{ candidate.display_name || candidate.name || '—' }}</td>
              <td>{{ candidate.asset_type || candidate.security_type || '—' }}</td>
              <td>{{ candidate.exchange || '—' }}</td>
              <td>
                <button type="button" class="pick" data-testid="v3-candidate-pick" @click="emit('select', candidate)">选择</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="dialog__empty">没有可选择的证券候选</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-mask {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  background: rgba(15, 23, 32, 0.45);
  padding: 16px;
}
.dialog {
  width: min(640px, 100%);
  border-radius: var(--v3-radius-md);
  background: var(--v3-surface);
  border: 1px solid var(--v3-border);
  overflow: hidden;
}
.dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--v3-border-subtle);
  padding: 12px 16px;
}
.dialog__header h2 {
  margin: 0;
  font-size: var(--v3-font-size-lg);
}
.dialog__close {
  border: 0;
  background: none;
  cursor: pointer;
  color: var(--v3-text-muted);
}
.dialog__body { padding: 12px 16px 16px; }
.dialog__table {
  width: 100%;
  border-collapse: collapse;
}
.dialog__table th,
.dialog__table td {
  border-bottom: 1px solid var(--v3-border-subtle);
  padding: 8px 6px;
  text-align: left;
  font-size: var(--v3-font-size-sm);
}
.dialog__table th { color: var(--v3-text-muted); }
.pick {
  border: 1px solid var(--v3-primary);
  border-radius: var(--v3-radius-sm);
  background: var(--v3-primary-soft);
  color: var(--v3-primary);
  padding: 4px 10px;
  cursor: pointer;
}
.dialog__empty {
  margin: 0;
  color: var(--v3-text-muted);
  text-align: center;
  padding: 24px 0;
}
</style>
