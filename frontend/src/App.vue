<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import {
  Activity,
  FileText,
  LockKeyhole,
  LogOut,
  Moon,
  Monitor,
  Sun,
} from 'lucide-vue-next'
import { darkTheme, dateZhCN, lightTheme, zhCN, type GlobalTheme, type GlobalThemeOverrides } from 'naive-ui'
import { api, clearToken, getToken, setToken } from './api'

type ThemePref = 'system' | 'light' | 'dark'

const THEME_KEY = 'advisor_theme'

const navItems = [
  { to: '/archives', label: '分析归档', icon: FileText },
]

const themeOptions = [
  { label: '跟随系统', value: 'system' },
  { label: '亮色', value: 'light' },
  { label: '暗色', value: 'dark' },
]

const tokenInput = ref(getToken())
const authChecked = ref(false)
const authenticated = ref(false)
const loginLoading = ref(false)
const loginError = ref('')
const themePref = ref<ThemePref>((localStorage.getItem(THEME_KEY) as ThemePref) || 'system')
const systemDark = ref(false)

const media = window.matchMedia('(prefers-color-scheme: dark)')
const updateSystemTheme = () => {
  systemDark.value = media.matches
}

const resolvedTheme = computed<'light' | 'dark'>(() => {
  if (themePref.value === 'system') return systemDark.value ? 'dark' : 'light'
  return themePref.value
})

const naiveTheme = computed<GlobalTheme>(() => (resolvedTheme.value === 'dark' ? darkTheme : lightTheme))
const themeIcon = computed(() => {
  if (themePref.value === 'system') return Monitor
  return resolvedTheme.value === 'dark' ? Moon : Sun
})

const themeOverrides = computed<GlobalThemeOverrides>(() => {
  const dark = resolvedTheme.value === 'dark'
  return {
    common: {
      primaryColor: dark ? '#60A5FA' : '#0C5CAB',
      primaryColorHover: dark ? '#93C5FD' : '#0a6fd0',
      primaryColorPressed: dark ? '#3B82F6' : '#0a4a8a',
      primaryColorSuppl: dark ? '#60A5FA' : '#0C5CAB',
      textColor1: dark ? '#fafafa' : '#172033',
      textColor2: dark ? '#d7deea' : '#344054',
      textColor3: dark ? '#a3aebf' : '#667085',
      borderColor: dark ? 'rgba(148, 163, 184, 0.28)' : '#dfe6f1',
      borderRadius: '8px',
      fontFamily:
        '"IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif',
    },
  }
})

async function verifyCurrentToken() {
  const existing = getToken()
  if (!existing) {
    authenticated.value = false
    authChecked.value = true
    return
  }
  try {
    await api.verifyToken()
    tokenInput.value = existing
    authenticated.value = true
  } catch {
    clearToken()
    authenticated.value = false
  } finally {
    authChecked.value = true
  }
}

async function login() {
  const token = tokenInput.value.trim()
  loginError.value = ''
  if (!token) {
    loginError.value = '请输入访问密码'
    return
  }
  loginLoading.value = true
  setToken(token)
  try {
    await api.verifyToken()
    authenticated.value = true
  } catch (e) {
    clearToken()
    loginError.value = (e as Error).message
  } finally {
    loginLoading.value = false
  }
}

function logout() {
  clearToken()
  tokenInput.value = ''
  authenticated.value = false
}

function saveTheme(value: ThemePref) {
  themePref.value = value
  localStorage.setItem(THEME_KEY, value)
}

onMounted(() => {
  updateSystemTheme()
  media.addEventListener('change', updateSystemTheme)
  void verifyCurrentToken()
})

onUnmounted(() => {
  media.removeEventListener('change', updateSystemTheme)
})
</script>

<template>
  <n-config-provider
    :theme="naiveTheme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-message-provider>
      <n-global-style />
      <div class="app-shell" :class="resolvedTheme === 'dark' ? 'theme-dark' : 'theme-light'">
        <div v-if="!authChecked" class="login-wrap">
          <n-spin size="large" />
          <div class="muted mt-3">正在校验访问权限…</div>
        </div>

        <div v-else-if="!authenticated" class="login-wrap">
          <section class="login-card panel-card">
            <div class="login-icon" aria-hidden="true">
              <LockKeyhole :size="28" />
            </div>
            <h1>持仓投研决策看板</h1>
            <p>请输入后端配置的 ADVISOR_TOKEN 作为远程访问密码。</p>
            <n-input
              v-model:value="tokenInput"
              type="password"
              show-password-on="mousedown"
              placeholder="访问密码"
              size="large"
              @keyup.enter="login"
            />
            <n-alert v-if="loginError" type="error" :show-icon="false">{{ loginError }}</n-alert>
            <n-button type="primary" size="large" block :loading="loginLoading" @click="login">
              登录看板
            </n-button>
          </section>
        </div>

        <template v-else>
          <header class="topbar">
            <RouterLink to="/archives" class="brand" aria-label="返回分析归档">
              <Activity :size="24" />
              <span>持仓投研决策看板</span>
            </RouterLink>

            <nav class="desktop-nav" aria-label="主导航">
              <RouterLink v-for="item in navItems" :key="item.to" :to="item.to" class="nav-link">
                <component :is="item.icon" :size="17" />
                <span>{{ item.label }}</span>
              </RouterLink>
            </nav>

            <div class="top-actions">
              <component :is="themeIcon" :size="18" class="muted" />
              <n-select
                :value="themePref"
                :options="themeOptions"
                size="small"
                class="theme-select"
                @update:value="saveTheme"
              />
              <n-button quaternary circle aria-label="退出登录" @click="logout">
                <template #icon><LogOut :size="18" /></template>
              </n-button>
            </div>
          </header>

          <main class="content">
            <RouterView />
          </main>

          <nav class="mobile-nav" aria-label="移动端主导航">
            <RouterLink v-for="item in navItems" :key="item.to" :to="item.to" class="mobile-nav-link">
              <component :is="item.icon" :size="19" />
              <span>{{ item.label }}</span>
            </RouterLink>
          </nav>
        </template>
      </div>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.topbar {
  position: sticky;
  z-index: 30;
  top: 0;
  display: flex;
  min-height: 64px;
  align-items: center;
  gap: 20px;
  border-bottom: 1px solid var(--app-border);
  background: color-mix(in srgb, var(--app-surface-strong) 82%, transparent);
  padding: 0 24px;
  box-shadow: 0 1px 0 var(--app-border-soft), 0 14px 40px color-mix(in srgb, var(--app-bg) 72%, transparent);
  backdrop-filter: blur(18px);
}

.brand {
  display: inline-flex;
  min-height: 44px;
  align-items: center;
  gap: 10px;
  color: var(--app-text);
  font-size: 16px;
  font-weight: 800;
  text-decoration: none;
  white-space: nowrap;
}

.brand svg {
  width: 30px;
  height: 30px;
  border: 1px solid color-mix(in srgb, var(--app-primary) 32%, transparent);
  border-radius: 8px;
  background: var(--app-primary-soft);
  padding: 5px;
  color: var(--app-primary);
}

.desktop-nav {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 8px;
}

.nav-link {
  display: inline-flex;
  min-height: 44px;
  align-items: center;
  gap: 8px;
  border-radius: 8px;
  color: var(--app-text-muted);
  padding: 0 12px;
  text-decoration: none;
  transition: background 180ms ease, color 180ms ease, box-shadow 180ms ease;
}

.nav-link:hover {
  background: color-mix(in srgb, var(--app-primary) 10%, transparent);
  color: var(--app-text);
}

.nav-link.router-link-active {
  background: var(--app-primary-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--app-primary) 18%, transparent);
  color: var(--app-text);
}

.nav-link.router-link-active svg {
  color: var(--app-primary);
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-surface-strong) 66%, transparent);
  padding: 2px 4px 2px 10px;
}

.theme-select {
  width: 118px;
}

.content {
  width: min(1440px, 100%);
  margin: 0 auto;
  padding: 24px;
}

.login-wrap {
  display: grid;
  min-height: 100dvh;
  place-items: center;
  padding: 24px;
}

.login-card {
  display: grid;
  width: min(420px, 100%);
  gap: 16px;
  padding: 28px;
  box-shadow: var(--app-shadow-strong);
}

.login-icon {
  display: grid;
  width: 56px;
  height: 56px;
  place-items: center;
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-primary) 18%, transparent);
  color: var(--app-primary);
}

.login-card h1 {
  margin: 0;
  color: var(--app-text);
  font-size: 24px;
  font-weight: 800;
}

.login-card p {
  margin: 0;
  color: var(--app-text-muted);
  line-height: 1.6;
}

.mobile-nav {
  display: none;
}

@media (max-width: 768px) {
  .topbar {
    min-height: 58px;
    gap: 10px;
    padding: 0 12px;
  }

  .desktop-nav {
    display: none;
  }

  .brand span {
    max-width: 8.5em;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .top-actions {
    gap: 4px;
    padding-left: 6px;
  }

  .theme-select {
    width: 88px;
  }

  .content {
    padding: 16px 12px 92px;
  }

  .mobile-nav {
    position: fixed;
    z-index: 40;
    right: 10px;
    bottom: max(10px, env(safe-area-inset-bottom));
    left: 10px;
    display: grid;
    grid-template-columns: 1fr;
    gap: 4px;
    border: 1px solid var(--app-border);
    border-radius: 8px;
    background: color-mix(in srgb, var(--app-surface-strong) 88%, transparent);
    padding: 6px;
    box-shadow: var(--app-shadow-strong);
    backdrop-filter: blur(18px);
  }

  .mobile-nav-link {
    display: grid;
    min-height: 52px;
    place-items: center;
    gap: 2px;
    border-radius: 8px;
    color: var(--app-text-muted);
    font-size: 11px;
    font-weight: 700;
    text-decoration: none;
    transition: background 180ms ease, color 180ms ease;
  }

  .mobile-nav-link.router-link-active {
    background: var(--app-primary-soft);
    color: var(--app-text);
  }

  .mobile-nav-link.router-link-active svg {
    color: var(--app-primary);
  }
}
</style>
