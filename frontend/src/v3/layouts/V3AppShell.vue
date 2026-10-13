<script setup lang="ts">
/**
 * V3 App Shell：QLayout + Sidebar + Topbar + PageContainer
 * 只负责全局导航/主题/响应式，不绑定业务数据。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, clearSession, hasSession } from '@/api'
import { useV3ThemeLifecycle } from '../composables/useV3Theme'
import V3Sidebar from './V3Sidebar.vue'
import V3Topbar from './V3Topbar.vue'

/***********************导航与响应式布局*********************/
const router = useRouter()
useV3ThemeLifecycle()

const collapsed = ref(localStorage.getItem('v3_sidebar_collapsed') === '1')
const mobileDrawer = ref(false)
const isMobile = ref(false)

let mediaQuery: MediaQueryList | null = null

function onResize(): void {
  isMobile.value = window.matchMedia('(max-width: 1023px)').matches
  if (!isMobile.value) mobileDrawer.value = false
}

function syncCollapsed(value: boolean): void {
  collapsed.value = value
  localStorage.setItem('v3_sidebar_collapsed', value ? '1' : '0')
}

const pageTitle = computed(() => {
  const title = router.currentRoute.value.meta?.title
  return typeof title === 'string' ? title : String(router.currentRoute.value.name ?? '')
})

function navigate(name: string): void {
  mobileDrawer.value = false
  void router.push({ name })
}

/***********************本地会话即时退出*********************/
async function logout(): Promise<void> {
  // 先捕获旧凭证发起撤销，再即时清空本地会话，避免等网络期间旧刷新写回。
  const revoke = hasSession() ? api.logout().catch(() => undefined) : Promise.resolve()
  clearSession()
  await router.replace({ name: 'login' })
  await revoke
}

function openSettings(): void {
  void router.push({ name: 'settings' })
}

/***********************布局生命周期*********************/
onMounted(() => {
  onResize()
  mediaQuery = window.matchMedia('(max-width: 1023px)')
  mediaQuery.addEventListener('change', onResize)
})
onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', onResize)
  mediaQuery = null
})
</script>

<template>
  <q-layout view="lHh Lpr lFf" class="v3-app-shell v3-root" data-testid="v3-app-shell">
    <!-- 桌面常驻 Sidebar -->
    <q-drawer
      v-if="!isMobile"
      side="left"
      :model-value="true"
      :width="collapsed ? 64 : 224"
      :breakpoint="0"
      bordered
      class="v3-app-shell__drawer"
      data-testid="v3-desktop-sidebar"
    >
      <V3Sidebar
        :collapsed="collapsed"
        data-testid="v3-sidebar"
        @update:collapsed="syncCollapsed"
        @navigate="navigate"
      />
    </q-drawer>

    <!-- 移动端 Overlay Drawer -->
    <q-drawer
      v-if="isMobile"
      v-model="mobileDrawer"
      side="left"
      overlay
      bordered
      :width="260"
      class="v3-app-shell__drawer v3-app-shell__drawer--mobile"
      data-testid="v3-mobile-drawer"
    >
      <V3Sidebar mobile data-testid="v3-sidebar" @navigate="navigate" />
    </q-drawer>

    <q-header elevated class="v3-app-shell__header">
      <V3Topbar
        :page-title="pageTitle"
        :show-menu="isMobile"
        @open-menu="mobileDrawer = true"
        @open-settings="openSettings"
        @logout="logout"
      >
        <template #center><slot name="topbar-center" /></template>
        <template #status>
          <slot name="topbar-status" />
        </template>
      </V3Topbar>
    </q-header>

    <q-page-container class="v3-app-shell__container">
      <q-page class="v3-app-shell__page">
        <a class="v3-skip-link" href="#v3-main-content">跳到主要内容</a>
        <main id="v3-main-content" class="v3-app-shell__main" data-testid="v3-main-content">
          <slot />
        </main>
      </q-page>
    </q-page-container>
  </q-layout>
</template>

<style scoped>
.v3-app-shell {
  background: var(--v3-bg);
  color: var(--v3-text);
}
.v3-app-shell__drawer {
  background: var(--v3-surface);
}
.v3-app-shell__header {
  background: var(--v3-surface);
  color: var(--v3-text);
}
.v3-app-shell__container {
  background: var(--v3-bg);
}
.v3-app-shell__page {
  min-height: calc(100dvh - var(--v3-topbar-height));
  background: transparent;
}
.v3-app-shell__main {
  width: min(var(--v3-content-max), calc(100% - 40px));
  margin: 0 auto;
  padding: var(--v3-space-5) 0 var(--v3-space-8);
}
@media (max-width: 768px) {
  .v3-app-shell__main {
    width: calc(100% - 24px);
    padding-top: var(--v3-space-4);
  }
}
</style>
