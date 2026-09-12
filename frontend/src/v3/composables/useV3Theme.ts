/**
 * V3 主题唯一权威：light / dark / system
 * 复用既有 localStorage key `advisor_theme`，不建立第二套状态。
 * 同步 Quasar Dark 与 body class（Legacy CSS 变量依赖 theme-dark）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Dark } from 'quasar'

export type ThemePref = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const THEME_KEY = 'advisor_theme'
const MEDIA = '(prefers-color-scheme: dark)'

function readStoredPref(): ThemePref {
  const raw = localStorage.getItem(THEME_KEY)
  if (raw === 'dark' || raw === 'light' || raw === 'system') return raw
  return 'light'
}

function systemPrefersDark(): boolean {
  return window.matchMedia(MEDIA).matches
}

function resolveTheme(pref: ThemePref): ResolvedTheme {
  if (pref === 'system') return systemPrefersDark() ? 'dark' : 'light'
  return pref
}

function applyTheme(resolved: ResolvedTheme): void {
  Dark.set(resolved === 'dark')
  const root = document.documentElement
  root.classList.toggle('theme-dark', resolved === 'dark')
  root.classList.toggle('theme-light', resolved === 'light')
  document.body.classList.toggle('body--dark', resolved === 'dark')
  // 广播给 Legacy App.vue（它只认识 light/dark）
  window.dispatchEvent(
    new CustomEvent('advisor-theme-changed', { detail: { theme: resolved } }),
  )
}

/** 单例状态，保证只有一个 theme authority */
export const themePref = ref<ThemePref>(readStoredPref())
export const resolvedTheme = ref<ResolvedTheme>(resolveTheme(themePref.value))
let mediaQuery: MediaQueryList | null = null

function onSystemChange(): void {
  if (themePref.value !== 'system') return
  resolvedTheme.value = resolveTheme('system')
  applyTheme(resolvedTheme.value)
}

/** 模块级设置主题，App.vue 等可直接调用 */
export function setThemePref(pref: ThemePref): void {
  themePref.value = pref
  localStorage.setItem(THEME_KEY, pref)
  resolvedTheme.value = resolveTheme(pref)
  applyTheme(resolvedTheme.value)
}

export function cycleTheme(): void {
  const next: ThemePref =
    themePref.value === 'light' ? 'dark' : themePref.value === 'dark' ? 'system' : 'light'
  setThemePref(next)
}

export function useV3Theme() {
  const isDark = computed(() => resolvedTheme.value === 'dark')
  const isSystem = computed(() => themePref.value === 'system')

  function initTheme(): void {
    mediaQuery = window.matchMedia(MEDIA)
    mediaQuery.addEventListener('change', onSystemChange)
    applyTheme(resolvedTheme.value)
  }

  function disposeTheme(): void {
    mediaQuery?.removeEventListener('change', onSystemChange)
    mediaQuery = null
  }

  // 模块级 watch：任意实例挂载后都保持同步
  watch(themePref, (pref) => {
    resolvedTheme.value = resolveTheme(pref)
    applyTheme(resolvedTheme.value)
  })

  return {
    themePref,
    resolvedTheme,
    isDark,
    isSystem,
    setThemePref,
    cycleTheme,
    initTheme,
    disposeTheme,
  }
}

/** 在 App 入口调用一次，确保主题在首帧生效 */
export function bootstrapV3Theme(): void {
  themePref.value = readStoredPref()
  resolvedTheme.value = resolveTheme(themePref.value)
  applyTheme(resolvedTheme.value)
}

export function useV3ThemeLifecycle(): void {
  const { initTheme, disposeTheme } = useV3Theme()
  onMounted(() => initTheme())
  onBeforeUnmount(() => disposeTheme())
}
