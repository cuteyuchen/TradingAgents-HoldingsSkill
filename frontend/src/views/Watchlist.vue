<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../api'
import type { HealthStatus, WatchlistItem } from '../api/types'

const message = useMessage()
const list = ref<WatchlistItem[]>([])
const health = ref<HealthStatus[]>([])
const err = ref('')
const submitting = ref(false)
const form = ref<WatchlistItem>({ code: '', name: '', cadence: '10:00', enabled: true })
const cadenceOptions = [
  { label: '09:25 开盘竞价', value: '09:25' },
  { label: '10:00 早盘确认', value: '10:00' },
  { label: '12:00 午间复盘', value: '12:00' },
  { label: '14:30 尾盘风控', value: '14:30' },
]

const healthByCode = computed(() => {
  const map = new Map<string, HealthStatus>()
  for (const h of health.value) {
    if (h.code && (!map.has(h.code) || h.degraded)) map.set(h.code, h)
  }
  return map
})

async function load() {
  err.value = ''
  try {
    const [w, h] = await Promise.all([api.listWatchlist(), api.health()])
    list.value = w
    health.value = h
  } catch (e) {
    err.value = (e as Error).message
  }
}

async function add() {
  const code = form.value.code.trim().toUpperCase()
  if (!code) {
    message.error('请输入股票或 ETF 代码')
    return
  }
  submitting.value = true
  err.value = ''
  try {
    await api.addWatchlist({
      ...form.value,
      code,
      name: form.value.name?.trim() || undefined,
    })
    form.value = { code: '', name: '', cadence: '10:00', enabled: true }
    await load()
    message.success('自选股已保存')
  } catch (e) {
    err.value = (e as Error).message
    message.error(err.value)
  } finally {
    submitting.value = false
  }
}

async function remove(code: string) {
  try {
    await api.removeWatchlist(code)
    await load()
    message.success('已删除自选股')
  } catch (e) {
    err.value = (e as Error).message
    message.error(err.value)
  }
}

onMounted(load)
</script>

<template>
  <div class="card">
    <h3>自选股 / 定时分析</h3>
    <n-alert v-if="err" type="error" :show-icon="false" class="mb-3">{{ err }}</n-alert>
    <n-form class="watch-form" label-placement="top">
      <n-form-item label="代码" required>
        <n-input v-model:value="form.code" placeholder="如 600519 或 513040" @keyup.enter="add" />
      </n-form-item>
      <n-form-item label="名称">
        <n-input v-model:value="form.name" placeholder="可选" @keyup.enter="add" />
      </n-form-item>
      <n-form-item label="检查点">
        <n-select v-model:value="form.cadence" :options="cadenceOptions" />
      </n-form-item>
      <n-form-item label="启用">
        <n-switch v-model:value="form.enabled" />
      </n-form-item>
      <n-form-item label="操作">
        <n-button type="primary" block :loading="submitting" @click="add">添加 / 更新</n-button>
      </n-form-item>
    </n-form>

    <table class="data-table">
      <thead><tr><th>代码</th><th>名称</th><th>检查点</th><th>状态</th><th>停用/降级原因</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="(w, i) in list" :key="i">
          <td data-label="代码"><code>{{ w.code }}</code></td>
          <td data-label="名称">{{ w.name || '—' }}</td>
          <td data-label="检查点">{{ w.cadence || '—' }}</td>
          <td data-label="状态"><span class="tag" :class="w.enabled ? 'grade-A' : 'grade-C'">{{ w.enabled ? '启用' : '已停用' }}</span></td>
          <td data-label="停用/降级原因" class="muted">{{ healthByCode.get(w.code)?.note || '—' }}</td>
          <td data-label="操作"><n-button size="small" secondary type="error" @click="remove(w.code)">删除</n-button></td>
        </tr>
        <tr v-if="!list.length"><td colspan="6" class="muted">暂无自选股</td></tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h3>检查点健康度（连续失败降级）</h3>
    <table class="data-table">
      <thead><tr><th>代码</th><th>检查点</th><th>连续失败</th><th>状态</th><th>最近成功</th><th>最近失败</th><th>原因</th></tr></thead>
      <tbody>
        <tr v-for="(h, i) in health" :key="i" :class="{ degraded: h.degraded }">
          <td data-label="代码">{{ h.code || '全局' }}</td>
          <td data-label="检查点">{{ h.checkpoint }}</td>
          <td data-label="连续失败">{{ h.consecutive_failures }}</td>
          <td data-label="状态"><span class="tag" :class="h.degraded ? 'grade-C' : 'grade-A'">{{ h.degraded ? '降级' : '正常' }}</span></td>
          <td data-label="最近成功" class="muted">{{ h.last_success_at ? h.last_success_at.slice(5, 16).replace('T', ' ') : '—' }}</td>
          <td data-label="最近失败" class="muted">{{ h.last_failure_at ? h.last_failure_at.slice(5, 16).replace('T', ' ') : '—' }}</td>
          <td data-label="原因" class="muted">{{ h.note || '—' }}</td>
        </tr>
        <tr v-if="!health.length"><td colspan="7" class="muted">暂无健康记录</td></tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.watch-form {
  display: grid;
  grid-template-columns: 140px 1fr 190px 90px 140px;
  gap: 12px;
  align-items: end;
  margin-bottom: 16px;
}

.watch-form :deep(.n-form-item) {
  margin-bottom: 0;
}

.degraded {
  background: color-mix(in srgb, var(--app-warning) 12%, transparent);
}

@media (max-width: 900px) {
  .watch-form {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 640px) {
  .watch-form {
    grid-template-columns: 1fr;
  }
}
</style>
