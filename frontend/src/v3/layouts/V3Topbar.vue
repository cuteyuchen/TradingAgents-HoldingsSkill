<script setup lang="ts">
/**
 * V3 Topbar：基础结构 + 预留扩展。
 * 不绑定 Dashboard/Holdings/Analysis 业务数据。
 */
import { computed } from 'vue'
import { LogOut, Menu, Moon, Settings, Sun, SunMoon } from 'lucide-vue-next'
import { useV3Theme } from '../composables/useV3Theme'

const props = withDefaults(
  defineProps<{
    pageTitle?: string
    showMenu?: boolean
  }>(),
  { pageTitle: '', showMenu: false },
)

const emit = defineEmits<{
  openMenu: []
  openSettings: []
  logout: []
}>()

const { themePref, setThemePref } = useV3Theme()

const themeIcon = computed(() => {
  if (themePref.value === 'dark') return Moon
  if (themePref.value === 'system') return SunMoon
  return Sun
})

const themeLabel = computed(() => {
  if (themePref.value === 'dark') return '当前：暗色，点击切换到跟随系统'
  if (themePref.value === 'system') return '当前：跟随系统，点击切换到亮色'
  return '当前：亮色，点击切换到暗色'
})

function cycleTheme(): void {
  const next = themePref.value === 'light' ? 'dark' : themePref.value === 'dark' ? 'system' : 'light'
  setThemePref(next)
}
</script>

<template>
  <header class="v3-topbar" data-testid="v3-topbar">
    <div class="v3-topbar__left">
      <button
        v-if="showMenu"
        type="button"
        class="v3-topbar__icon-btn"
        aria-label="打开导航菜单"
        data-testid="v3-topbar-menu"
        @click="emit('openMenu')"
      >
        <Menu :size="20" aria-hidden="true" />
      </button>
      <h2 v-if="pageTitle" class="v3-topbar__title" data-testid="v3-topbar-title">{{ pageTitle }}</h2>
      <slot name="title" />
    </div>

    <div class="v3-topbar__center">
      <slot name="center" />
    </div>

    <div class="v3-topbar__right">
      <slot name="status" />
      <button
        type="button"
        class="v3-topbar__icon-btn"
        :aria-label="themeLabel"
        :title="themeLabel"
        data-testid="v3-theme-toggle"
        @click="cycleTheme"
      >
        <component :is="themeIcon" :size="18" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="v3-topbar__icon-btn"
        aria-label="设置"
        title="设置"
        data-testid="v3-topbar-settings"
        @click="emit('openSettings')"
      >
        <Settings :size="18" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="v3-topbar__icon-btn"
        aria-label="退出登录"
        title="退出登录"
        data-testid="v3-topbar-logout"
        @click="emit('logout')"
      >
        <LogOut :size="17" aria-hidden="true" />
      </button>
    </div>
  </header>
</template>

<style scoped>
.v3-topbar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  height: var(--v3-topbar-height);
  padding: 0 var(--v3-space-4);
  background: var(--v3-surface);
  border-bottom: 1px solid var(--v3-border);
  gap: var(--v3-space-3);
}
.v3-topbar__left {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
  min-width: 0;
}
.v3-topbar__center {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
}
.v3-topbar__right {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  min-width: 0;
}
.v3-topbar__title {
  margin: 0;
  font-size: var(--v3-font-size-lg);
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.v3-topbar__icon-btn {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: var(--v3-radius-sm);
  background: transparent;
  color: var(--v3-text-muted);
  cursor: pointer;
}
.v3-topbar__icon-btn:hover {
  background: var(--v3-surface-hover);
  color: var(--v3-text);
}
@media (max-width: 768px) {
  .v3-topbar {
    grid-template-columns: auto 1fr;
    padding-inline: var(--v3-space-2);
  }
  .v3-topbar__center { display: none; }
  .v3-topbar__right { grid-column: 2; }
}
</style>
