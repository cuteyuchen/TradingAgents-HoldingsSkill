<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BarChart3,
  BellRing,
  LayoutDashboard,
  LogOut,
  Moon,
  Settings,
  Sun,
  Upload,
} from 'lucide-vue-next'
import { darkTheme, dateZhCN, lightTheme, zhCN, type GlobalTheme, type GlobalThemeOverrides } from 'naive-ui'

import { api, clearSession, hasSession } from './api'
import type { User } from './api/types'

const route = useRoute()
const router = useRouter()
const THEME_KEY = 'advisor_theme'
type ThemePref = 'light' | 'dark'

const themePref = ref<ThemePref>((localStorage.getItem(THEME_KEY) as ThemePref) || 'dark')
const user = ref<User | null>(null)
const loadingUser = ref(false)
const isLogin = computed(() => route.name === 'login')
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
  { name: 'settings', label: '系统设置', icon: Settings },
]

async function loadUser() {
  if (!hasSession() || isLogin.value || loadingUser.value) return
  loadingUser.value = true
  try {
    user.value = await api.me()
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
            <main class="app-content">
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
.nav-link { display: inline-flex; align-items: center; gap: 7px; min-height: 38px; padding: 0 13px; border-radius: 9px; color: var(--app-text-muted); text-decoration: none; font-size: 13px; font-weight: 700; }
.nav-link:hover, .nav-link.router-link-active { background: var(--app-primary-soft); color: var(--app-primary); }
.user-actions { display: flex; justify-content: flex-end; align-items: center; gap: 5px; }
.user-copy { display: grid; margin-right: 5px; text-align: right; }
.user-copy strong { max-width: 170px; overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
.app-content { width: min(1480px, 100%); margin: 0 auto; padding: 24px; }
@media (max-width: 980px) {
  .topbar { grid-template-columns: 1fr auto; padding: 0 12px; }
  .top-nav { position: fixed; z-index: 60; right: 12px; bottom: 12px; left: 12px; justify-content: space-around; box-shadow: var(--app-shadow-strong); }
  .nav-link { flex: 1; justify-content: center; padding: 0 6px; }
  .user-copy { display: none; }
  .app-content { padding: 16px 12px 88px; }
}
@media (max-width: 560px) {
  .brand span { display: none; }
  .nav-link span { font-size: 11px; }
}
</style>
