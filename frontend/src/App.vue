<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BarChart3,
  BellRing,
  BriefcaseBusiness,
  Camera,
  FlaskConical,
  Activity,
  ExternalLink,
  LayoutDashboard,
  LogOut,
  Moon,
  Settings,
  ShieldCheck,
  Sun,
  Upload,
} from 'lucide-vue-next'
import { darkTheme, dateZhCN, lightTheme, zhCN, type GlobalTheme, type GlobalThemeOverrides } from 'naive-ui'

import { api, clearSession, hasSession } from './api'
import type { User } from './api/types'
import { usePortfolioContext } from './composables/portfolio'
import { fmtDateTime } from './utils/ui'

const route = useRoute()
const router = useRouter()
const THEME_KEY = 'advisor_theme'
type ThemePref = 'light' | 'dark'

const themePref = ref<ThemePref>((localStorage.getItem(THEME_KEY) as ThemePref) || 'dark')
const user = ref<User | null>(null)
const loadingUser = ref(false)
const isLogin = computed(() => route.name === 'login')
const {
  portfolios,
  selectedPortfolioId,
  selectedPortfolio,
  loading: loadingPortfolios,
  error: portfolioError,
  loadPortfolios,
  setSelectedPortfolio,
} = usePortfolioContext()
const theme = computed<GlobalTheme>(() => (themePref.value === 'dark' ? darkTheme : lightTheme))
const themeOverrides = computed<GlobalThemeOverrides>(() => ({
  common: {
    primaryColor: themePref.value === 'dark' ? '#60A5FA' : '#1769aa',
    primaryColorHover: themePref.value === 'dark' ? '#93C5FD' : '#2683cf',
    primaryColorPressed: themePref.value === 'dark' ? '#3B82F6' : '#0f568f',
    borderRadius: '10px',
    fontFamily: 'Inter, "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
}))

const navigation = [
  { name: 'dashboard', label: '总览', icon: LayoutDashboard },
  { name: 'upload', label: '今日持仓', icon: Upload },
  { name: 'reports', label: '分析报告', icon: BarChart3 },
  { name: 'shadow', label: 'Shadow', icon: ShieldCheck },
  { name: 'research', label: '历史研究', icon: FlaskConical },
  { name: 'governance', label: '参数治理', icon: ShieldCheck },
  { name: 'system', label: '系统运维', icon: Activity },
  { name: 'settings', label: '系统设置', icon: Settings },
]

async function loadUser() {
  if (!hasSession() || isLogin.value || loadingUser.value) return
  loadingUser.value = true
  try {
    const [userResult] = await Promise.all([api.me(), loadPortfolios()])
    user.value = userResult
  } catch {
    user.value = null
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
    // A local logout must still work when the server is unavailable.
  }
  clearSession()
  user.value = null
  await router.replace({ name: 'login' })
}

const onSessionChanged = () => void loadUser()

onMounted(() => {
  void loadUser()
  window.addEventListener('advisor-session-changed', onSessionChanged)
})
onUnmounted(() => window.removeEventListener('advisor-session-changed', onSessionChanged))
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
                <div class="brand-mark"><BellRing :size="22" /></div>
                <div>
                  <strong>持仓投研决策系统</strong>
                  <span>TradingAgents Holdings</span>
                </div>
              </div>
              <nav class="top-nav" aria-label="主导航">
                <router-link v-for="item in navigation" :key="item.name" :to="{ name: item.name }" class="nav-link">
                  <component :is="item.icon" :size="17" />
                  <span>{{ item.label }}</span>
                </router-link>
              </nav>
              <div class="user-actions">
                <div class="user-copy">
                  <strong>{{ user?.username || user?.email || '用户' }}</strong>
                  <span>{{ user?.email }}</span>
                </div>
                <n-button quaternary circle :aria-label="themePref === 'dark' ? '切换亮色' : '切换暗色'" @click="toggleTheme">
                  <template #icon><Sun v-if="themePref === 'dark'" :size="18" /><Moon v-else :size="18" /></template>
                </n-button>
                <n-button quaternary circle aria-label="退出登录" @click="logout">
                  <template #icon><LogOut :size="18" /></template>
                </n-button>
              </div>
            </header>
            <div class="portfolio-context-bar" aria-label="当前组合上下文">
              <div class="context-copy">
                <BriefcaseBusiness :size="17" aria-hidden="true" />
                <div>
                  <span>当前 Portfolio</span>
                  <strong>{{ selectedPortfolio?.name || '尚未选择组合' }}</strong>
                </div>
                <small v-if="selectedPortfolio?.latest_snapshot_time">最近确认：{{ fmtDateTime(selectedPortfolio.latest_snapshot_time) }}</small>
              </div>
              <n-select
                v-if="portfolios.length"
                :value="selectedPortfolioId"
                class="global-portfolio-select"
                :options="portfolios.map((item) => ({ label: item.name, value: item.id }))"
                :loading="loadingPortfolios"
                aria-label="选择当前 Portfolio"
                @update:value="setSelectedPortfolio"
              />
              <n-button v-if="!portfolios.length" secondary @click="router.push({ name: 'upload' })">
                <template #icon><Camera :size="15" /></template>
                导入第一份持仓
              </n-button>
              <n-button v-else secondary @click="router.push({ name: 'upload', query: { portfolio: selectedPortfolioId } })">
                <template #icon><Camera :size="15" /></template>
                更新持仓
              </n-button>
              <n-button text type="primary" @click="router.push({ name: 'system' })">
                系统状态 <ExternalLink :size="14" />
              </n-button>
            </div>
            <n-alert v-if="portfolioError" class="context-error" type="warning" :show-icon="false">
              Portfolio 列表暂时无法读取，请打开系统状态页重试。
            </n-alert>
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
.app-root { min-height: 100dvh; background: var(--app-bg); color: var(--app-text); }
.topbar {
  position: sticky; top: 0; z-index: 50; display: grid; grid-template-columns: minmax(230px, 1fr) auto minmax(230px, 1fr);
  align-items: center; min-height: 68px; padding: 0 max(20px, calc((100vw - 1500px) / 2));
  border-bottom: 1px solid var(--app-border); background: color-mix(in srgb, var(--app-surface-strong) 88%, transparent);
  backdrop-filter: blur(18px);
}
.brand { display: flex; align-items: center; gap: 11px; }
.brand-mark { display: grid; width: 38px; height: 38px; place-items: center; border-radius: 11px; background: var(--app-primary-soft); color: var(--app-primary); }
.brand div:last-child { display: grid; }
.brand strong { font-size: 15px; }
.brand span, .user-copy span { color: var(--app-text-muted); font-size: 11px; }
.top-nav { display: flex; align-items: center; gap: 4px; padding: 5px; border: 1px solid var(--app-border-soft); border-radius: 12px; background: var(--app-surface); }
.top-nav { max-width: min(760px, 52vw); overflow-x: auto; scrollbar-width: thin; }
.nav-link { display: inline-flex; align-items: center; gap: 7px; min-height: 38px; padding: 0 13px; border-radius: 9px; color: var(--app-text-muted); text-decoration: none; font-size: 13px; font-weight: 700; }
.nav-link:hover, .nav-link.router-link-active { background: var(--app-primary-soft); color: var(--app-primary); }
.user-actions { display: flex; justify-content: flex-end; align-items: center; gap: 5px; }
.user-copy { display: grid; margin-right: 5px; text-align: right; }
.user-copy strong { max-width: 170px; overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
.app-content { width: min(1480px, 100%); margin: 0 auto; padding: 24px; }
.skip-link { position: fixed; z-index: 100; top: -50px; left: 14px; border-radius: 6px; background: var(--app-primary); padding: 8px 12px; color: white; text-decoration: none; }
.skip-link:focus { top: 10px; }
.portfolio-context-bar { display: flex; width: min(1480px, calc(100% - 48px)); align-items: center; gap: 12px; margin: 14px auto 0; border: 1px solid var(--app-border); border-radius: 8px; background: var(--app-surface); padding: 10px 14px; box-shadow: var(--app-shadow); }
.context-copy { display: flex; min-width: 0; align-items: center; gap: 9px; color: var(--app-primary); }
.context-copy > div { display: grid; min-width: 0; }
.context-copy span { color: var(--app-text-muted); font-size: 10px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }
.context-copy strong { max-width: 220px; overflow: hidden; color: var(--app-text); text-overflow: ellipsis; white-space: nowrap; }
.context-copy small { margin-left: 8px; color: var(--app-text-muted); font-size: 11px; }
.global-portfolio-select { width: min(240px, 28vw); margin-left: auto; }
.context-error { width: min(1480px, calc(100% - 48px)); margin: 10px auto 0; }
@media (max-width: 980px) {
  .topbar { grid-template-columns: 1fr auto; padding: 0 12px; }
  .top-nav { position: fixed; z-index: 60; right: 12px; bottom: 12px; left: 12px; justify-content: space-around; box-shadow: var(--app-shadow-strong); }
  .nav-link { flex: 1; justify-content: center; padding: 0 6px; }
  .user-copy { display: none; }
  .app-content { padding: 16px 12px 88px; }
  .portfolio-context-bar { width: calc(100% - 24px); align-items: flex-start; flex-wrap: wrap; margin-top: 10px; }
  .global-portfolio-select { width: min(280px, 100%); margin-left: 0; }
  .context-copy { flex: 1 1 100%; }
  .context-copy small { margin-left: 0; }
  .context-error { width: calc(100% - 24px); }
}
@media (max-width: 560px) {
  .brand span { display: none; }
  .nav-link span { font-size: 11px; }
}
</style>
