<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronDown } from 'lucide-vue-next'
import { darkTheme, dateZhCN, lightTheme, zhCN, type GlobalTheme, type GlobalThemeOverrides } from 'naive-ui'

import { api, hasSession } from './api'
import type { FuyaoStatus, LiveValidationReadiness, SystemHealth } from './api/types'
import { clearPortfolioContext, usePortfolioContext } from './composables/portfolio'
// V3 主题唯一权威（light/dark/system），Legacy 只消费 resolved 结果
import { resolvedTheme } from './v3/composables/useV3Theme'
import V3AppShell from './v3/layouts/V3AppShell.vue'

const route = useRoute()
const router = useRouter()

/** Legacy 壳层展示用：始终是 light 或 dark */
const themePref = resolvedTheme
const loadingUser = ref(false)
const loadingSystemStatus = ref(false)
const systemStatusError = ref(false)
const systemHealth = ref<SystemHealth | null>(null)
const liveReadiness = ref<LiveValidationReadiness | null>(null)
const fuyaoStatus = ref<FuyaoStatus | null>(null)
let systemStatusRequest: Promise<void> | null = null
const isLogin = computed(() => route.name === 'login')
const {
  portfolios,
  selectedPortfolioId,
  loading: loadingPortfolios,
  error: portfolioError,
  loadPortfolios,
  setSelectedPortfolio,
} = usePortfolioContext()

const theme = computed<GlobalTheme>(() => (themePref.value === 'dark' ? darkTheme : lightTheme))
const themeOverrides = computed<GlobalThemeOverrides>(() => ({
  common: {
    primaryColor: themePref.value === 'dark' ? '#7db6ff' : '#245ea8',
    primaryColorHover: themePref.value === 'dark' ? '#a7d0ff' : '#3477c2',
    primaryColorPressed: themePref.value === 'dark' ? '#4b91ed' : '#174984',
    borderRadius: '8px',
    fontFamily: 'Inter, "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
}))

const systemStatus = computed<'ok' | 'setup' | 'degraded' | 'loading'>(() => {
  if (portfolioError.value || systemStatusError.value) return 'degraded'
  if (loadingPortfolios.value || loadingSystemStatus.value) return 'loading'
  if (!systemHealth.value || !liveReadiness.value) return 'degraded'
  if (systemHealth.value.status !== 'OK') return 'degraded'
  if (!portfolios.value.length) return 'setup'
  if (fuyaoStatus.value && !fuyaoStatus.value.configured) return 'setup'
  return liveReadiness.value.status === 'READY' ? 'ok' : 'setup'
})
const systemStatusLabel = computed(() => {
  if (systemStatus.value === 'ok') return '正常'
  if (systemStatus.value === 'loading') return '检查中'
  if (systemStatus.value === 'degraded') return '数据受限'
  if (fuyaoStatus.value && !fuyaoStatus.value.configured) return '需要配置'
  if (!portfolios.value.length) return '需要配置'
  // Fuyao 已配置、系统健康，但 Live Readiness 尚未完成：不是系统异常，也不是数据受限
  return '需要完成验证'
})
const systemStatusHint = computed(() => {
  if (systemStatus.value === 'ok') return '系统健康且已具备当前验证条件'
  if (systemStatus.value === 'loading') return '正在检查系统状态'
  if (systemStatus.value === 'degraded') return '系统健康检查失败或核心数据受限'
  if (fuyaoStatus.value && !fuyaoStatus.value.configured) return '同花顺金融数据尚未配置'
  if (!portfolios.value.length) return '尚未创建可用于验证的组合'
  return '系统健康，仍需完成真实持仓与分析验证'
})

async function loadSystemStatus(): Promise<void> {
  if (systemStatusRequest) return systemStatusRequest
  if (systemHealth.value && liveReadiness.value) return

  loadingSystemStatus.value = true
  systemStatusError.value = false
  const request = Promise.all([api.getSystemHealth(), api.getLiveValidationReadiness(), api.getFuyaoStatus()])
    .then(([health, readiness, fuyao]) => {
      systemHealth.value = health
      liveReadiness.value = readiness
      fuyaoStatus.value = fuyao
    })
    .catch(() => {
      systemHealth.value = null
      liveReadiness.value = null
      fuyaoStatus.value = null
      systemStatusError.value = true
    })
  systemStatusRequest = request

  try {
    await request
  } finally {
    loadingSystemStatus.value = false
    if (systemStatusRequest === request) systemStatusRequest = null
  }
}

function resetSystemStatus() {
  systemHealth.value = null
  liveReadiness.value = null
  fuyaoStatus.value = null
  systemStatusError.value = false
}

async function loadUser() {
  if (!hasSession() || isLogin.value || loadingUser.value) return
  loadingUser.value = true
  try {
    await Promise.all([api.me(), loadPortfolios(), loadSystemStatus()])
  } catch {
    // The request layer owns session expiry; the shell stays quiet here.
  } finally {
    loadingUser.value = false
  }
}

function openSystemStatus() {
  void router.push({ name: 'settings', query: { section: 'system' } })
}

const onSessionChanged = () => {
  clearPortfolioContext()
  resetSystemStatus()
  void loadUser()
}
const onSessionExpired = () => {
  void router.replace({ name: 'login', query: { expired: '1' } }).finally(() => clearPortfolioContext())
}
const onMarketConfigChanged = async () => {
  if (systemStatusRequest) await systemStatusRequest
  resetSystemStatus()
  await loadSystemStatus()
}

onMounted(() => {
  void loadUser()
  window.addEventListener('advisor-session-changed', onSessionChanged)
  window.addEventListener('advisor-auth-expired', onSessionExpired)
  window.addEventListener('advisor-market-config-changed', onMarketConfigChanged)
})
onUnmounted(() => {
  window.removeEventListener('advisor-session-changed', onSessionChanged)
  window.removeEventListener('advisor-auth-expired', onSessionExpired)
  window.removeEventListener('advisor-market-config-changed', onMarketConfigChanged)
})
watch(() => route.name, () => void loadUser())
</script>

<template>
  <n-config-provider :theme="theme" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-dialog-provider>
        <n-global-style />
        <div class="app-root" :class="`theme-${themePref}`">
          <router-view v-if="isLogin" />
          <V3AppShell v-else>
            <template #topbar-center>
              <q-select
                v-if="portfolios.length > 1"
                :model-value="selectedPortfolioId"
                class="global-portfolio-select"
                outlined
                dense
                emit-value
                map-options
                hide-dropdown-icon
                :options="portfolios.map((item) => ({ label: item.name, value: item.id }))"
                :loading="loadingPortfolios"
                aria-label="选择组合"
                @update:model-value="setSelectedPortfolio"
              >
                <template #append><ChevronDown :size="16" aria-hidden="true" /></template>
              </q-select>
            </template>
            <template #topbar-status>
              <button
                type="button"
                class="system-status-button"
                :class="`system-status-${systemStatus}`"
                :aria-label="`${systemStatusHint}，查看系统状态`"
                @click="openSystemStatus"
              >
                <span class="status-dot" aria-hidden="true" />
                <span class="system-status-label">{{ systemStatusLabel }}</span>
              </button>
            </template>
            <router-view />
          </V3AppShell>
        </div>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.app-root { min-height: 100dvh; background: var(--page-bg); color: var(--text); }
.global-portfolio-select { width: 132px; }
.system-status-button { display: inline-flex; align-items: center; gap: 6px; min-height: 36px; border: 0; border-radius: 6px; padding: 0 8px; background: transparent; color: var(--text-muted); font-size: 12px; cursor: pointer; }
.system-status-button:hover { background: var(--surface-muted); }
.system-status-button.system-status-ok { color: var(--negative); }
.system-status-button.system-status-setup { color: var(--warning); }
.system-status-button.system-status-degraded { color: var(--danger); }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
@media (max-width: 760px) { .system-status-label { display: none; } }
</style>
