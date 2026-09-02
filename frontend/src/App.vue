<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity,
  BarChart3,
  BellRing,
  BriefcaseBusiness,
  History,
  LayoutDashboard,
  LogOut,
  Moon,
  Settings,
  Sun,
} from 'lucide-vue-next'
import { darkTheme, dateZhCN, lightTheme, zhCN, type GlobalTheme, type GlobalThemeOverrides } from 'naive-ui'

import { api, clearSession, hasSession } from './api'
import type { FuyaoStatus, LiveValidationReadiness, SystemHealth } from './api/types'
import { clearPortfolioContext, usePortfolioContext } from './composables/portfolio'

const route = useRoute()
const router = useRouter()
const THEME_KEY = 'advisor_theme'
type ThemePref = 'light' | 'dark'

const themePref = ref<ThemePref>(localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light')
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

const navigation = [
  { name: 'dashboard', label: '首页', icon: LayoutDashboard },
  { name: 'holdings', label: '持仓', icon: BriefcaseBusiness },
  { name: 'analysis', label: '分析', icon: BarChart3 },
  { name: 'simulation', label: '模拟', icon: Activity },
  { name: 'history', label: '历史', icon: History },
]

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
const systemStatusLabel = computed(() => ({ ok: '正常', setup: '需要配置', degraded: '数据受限', loading: '检查中' }[systemStatus.value]))
const systemStatusHint = computed(() => ({ ok: '系统健康且已具备当前验证条件', setup: '系统尚未满足当前验证条件或 Fuyao 尚未配置', degraded: '系统健康检查失败或核心数据受限', loading: '正在检查系统状态' }[systemStatus.value]))

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

function toggleTheme() {
  themePref.value = themePref.value === 'dark' ? 'light' : 'dark'
  localStorage.setItem(THEME_KEY, themePref.value)
}

async function logout() {
  try {
    await api.logout()
  } catch {
    // Local logout remains available when the backend is unavailable.
  }
  clearSession()
  await router.replace({ name: 'login' })
  clearPortfolioContext()
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
const onThemeChanged = (event: Event) => {
  const value = (event as CustomEvent<{ theme?: string }>).detail?.theme
  if (value !== 'light' && value !== 'dark') return
  themePref.value = value
  localStorage.setItem(THEME_KEY, value)
}

onMounted(() => {
  void loadUser()
  window.addEventListener('advisor-session-changed', onSessionChanged)
  window.addEventListener('advisor-auth-expired', onSessionExpired)
  window.addEventListener('advisor-theme-changed', onThemeChanged)
})
onUnmounted(() => {
  window.removeEventListener('advisor-session-changed', onSessionChanged)
  window.removeEventListener('advisor-auth-expired', onSessionExpired)
  window.removeEventListener('advisor-theme-changed', onThemeChanged)
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
          <template v-else>
            <a class="skip-link" href="#main-content">跳到主要内容</a>
            <header class="topbar">
              <div class="brand">
                <div class="brand-mark"><BellRing :size="20" /></div>
                <strong>投资驾驶舱</strong>
              </div>

              <div class="shell-nav-wrap">
                <n-select
                  v-if="portfolios.length > 1"
                  :value="selectedPortfolioId"
                  class="global-portfolio-select"
                  :options="portfolios.map((item) => ({ label: item.name, value: item.id }))"
                  :loading="loadingPortfolios"
                  aria-label="选择组合"
                  @update:value="setSelectedPortfolio"
                />
                <nav class="top-nav" aria-label="主导航">
                  <router-link v-for="item in navigation" :key="item.name" :to="{ name: item.name }" class="nav-link">
                    <component :is="item.icon" :size="16" aria-hidden="true" />
                    <span>{{ item.label }}</span>
                  </router-link>
                </nav>
              </div>

              <div class="shell-actions">
                <n-button
                  class="system-status-button"
                  quaternary
                  :class="`system-status-${systemStatus}`"
                  :aria-label="`${systemStatusHint}，查看系统状态`"
                  @click="openSystemStatus"
                >
                  <span class="status-dot" aria-hidden="true" />
                  <span>{{ systemStatusLabel }}</span>
                </n-button>
                <n-button
                  v-if="!portfolios.length"
                  class="header-setup-button"
                  secondary
                  size="small"
                  @click="router.push({ name: 'settings' })"
                >去配置</n-button>
                <router-link class="icon-link" :to="{ name: 'settings' }" aria-label="设置" title="设置">
                  <Settings :size="18" aria-hidden="true" />
                </router-link>
                <n-button quaternary circle :aria-label="themePref === 'dark' ? '切换亮色' : '切换暗色'" @click="toggleTheme">
                  <template #icon><Sun v-if="themePref === 'dark'" :size="18" /><Moon v-else :size="18" /></template>
                </n-button>
                <n-button quaternary circle aria-label="退出登录" title="退出登录" @click="logout">
                  <template #icon><LogOut :size="17" /></template>
                </n-button>
              </div>
            </header>
            <main id="main-content" class="app-content">
              <router-view />
            </main>
          </template>
        </div>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.app-root { min-height: 100dvh; background: var(--page-bg); color: var(--text); }
.topbar {
  position: sticky;
  top: 0;
  z-index: 50;
  display: grid;
  grid-template-columns: minmax(170px, 1fr) auto minmax(260px, 1fr);
  align-items: center;
  min-height: 60px;
  padding: 0 max(18px, calc((100vw - 1360px) / 2));
  border-bottom: 1px solid var(--border);
  background: color-mix(in srgb, var(--surface) 96%, transparent);
  backdrop-filter: blur(14px);
}
.brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
.brand-mark { display: grid; width: 32px; height: 32px; place-items: center; border: 1px solid var(--border); border-radius: 8px; background: var(--primary-soft); color: var(--primary); }
.brand strong { font-size: 16px; letter-spacing: 0; white-space: nowrap; }
.shell-nav-wrap { display: flex; align-items: center; justify-content: center; gap: 12px; min-width: 0; }
.top-nav { display: flex; align-items: center; gap: 2px; min-width: 0; }
.nav-link { display: inline-flex; align-items: center; gap: 6px; min-height: 38px; padding: 0 11px; border-radius: 7px; color: var(--text-muted); text-decoration: none; font-size: 13px; font-weight: 700; white-space: nowrap; transition: background .18s ease, color .18s ease; }
.nav-link:hover, .nav-link.router-link-active { background: var(--primary-soft); color: var(--primary); }
.global-portfolio-select { width: 132px; }
.shell-actions { display: flex; align-items: center; justify-content: flex-end; gap: 3px; min-width: 0; }
.system-status-button { min-height: 36px; color: var(--text-muted); font-size: 12px; }
.system-status-button.system-status-ok { color: var(--positive); }
.system-status-button.system-status-setup { color: var(--warning); }
.system-status-button.system-status-degraded { color: var(--danger); }
.system-status-button.system-status-loading { color: var(--text-muted); }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 14%, transparent); }
.icon-link { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 7px; color: var(--text-muted); text-decoration: none; }
.icon-link:hover, .icon-link.router-link-active { background: var(--primary-soft); color: var(--primary); }
.header-setup-button { white-space: nowrap; }
.app-content { width: min(1360px, calc(100% - 40px)); margin: 0 auto; padding: 22px 0 48px; }
.skip-link { position: fixed; z-index: 100; top: -50px; left: 14px; border-radius: 6px; background: var(--primary); padding: 8px 12px; color: white; text-decoration: none; }
.skip-link:focus { top: 10px; }
@media (max-width: 1100px) {
  .topbar { grid-template-columns: auto minmax(0, 1fr) auto; padding-inline: 12px; }
  .shell-nav-wrap { justify-content: flex-start; overflow-x: auto; }
  .global-portfolio-select { flex: 0 0 120px; }
  .nav-link { padding-inline: 9px; }
  .app-content { width: min(100% - 24px, 1360px); }
}
@media (max-width: 760px) {
  .topbar { grid-template-columns: auto 1fr; row-gap: 4px; min-height: 60px; padding-block: 6px; }
  .brand { grid-column: 1; }
  .shell-actions { grid-column: 2; grid-row: 1; }
  .shell-nav-wrap { grid-column: 1 / -1; grid-row: 2; justify-content: flex-start; width: 100%; }
  .top-nav { width: 100%; justify-content: space-between; }
  .nav-link { flex: 1; justify-content: center; min-height: 34px; padding-inline: 5px; font-size: 12px; }
  .global-portfolio-select { flex-basis: 110px; }
  .system-status-button span:last-child, .header-setup-button, .icon-link { display: none; }
  .app-content { padding-top: 16px; }
}
@media (max-width: 430px) {
  .brand strong { font-size: 14px; }
  .nav-link span { font-size: 11px; }
  .shell-actions { gap: 0; }
}
</style>
