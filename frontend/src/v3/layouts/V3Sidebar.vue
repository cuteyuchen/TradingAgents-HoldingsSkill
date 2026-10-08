<script setup lang="ts">
/**
 * V3 Sidebar：桌面常驻可折叠；移动端 Overlay Drawer。
 * selected 来自 router，不维护第二份菜单状态。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  Activity,
  BarChart3,
  BriefcaseBusiness,
  History,
  LayoutDashboard,
  PanelLeftClose,
  PanelLeftOpen,
  Palette,
} from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    collapsed?: boolean
    mobile?: boolean
  }>(),
  { collapsed: false, mobile: false },
)

const emit = defineEmits<{
  'update:collapsed': [value: boolean]
  navigate: [name: string]
}>()

const route = useRoute()

/** 导航项：selected 完全由当前 route.name 决定 */
const navigation = [
  { name: 'dashboard', label: '首页', icon: LayoutDashboard },
  { name: 'holdings', label: '持仓', icon: BriefcaseBusiness },
  { name: 'analysis', label: '分析', icon: BarChart3 },
  { name: 'simulation', label: '模拟', icon: Activity },
  { name: 'history', label: '历史', icon: History },
]

/** Foundation 仅 development / flag 开启时展示 */
const foundationEnabled = computed(() => {
  const flag = import.meta.env.VITE_ENABLE_V3_FOUNDATION
  return import.meta.env.DEV || flag === 'true' || flag === '1'
})

const items = computed(() => {
  const list = [...navigation]
  if (foundationEnabled.value) {
    list.push({ name: 'v3-foundation', label: 'Foundation', icon: Palette })
  }
  return list
})

function isActive(name: string): boolean {
  return route.name === name
}
</script>

<template>
  <nav
    class="v3-sidebar"
    :class="{
      'v3-sidebar--collapsed': collapsed,
      'v3-sidebar--mobile': mobile,
    }"
    aria-label="V3 主导航"
    data-testid="v3-sidebar"
  >
    <div class="v3-sidebar__brand">
      <span class="v3-sidebar__mark" aria-hidden="true">投</span>
      <span v-if="!collapsed || mobile" class="v3-sidebar__brand-text">投资驾驶舱</span>
    </div>

    <ul class="v3-sidebar__nav">
      <li v-for="item in items" :key="item.name">
        <button
          type="button"
          class="v3-sidebar__link"
          :class="{ 'v3-sidebar__link--active': isActive(item.name) }"
          :aria-current="isActive(item.name) ? 'page' : undefined"
          :aria-label="item.label"
          :title="item.label"
          :data-testid="`v3-nav-${item.name}`"
          @click="emit('navigate', item.name)"
        >
          <component :is="item.icon" :size="18" aria-hidden="true" />
          <span v-if="!collapsed || mobile" class="v3-sidebar__label">{{ item.label }}</span>
        </button>
      </li>
    </ul>

    <div class="v3-sidebar__footer">
      <button
        v-if="!mobile"
        type="button"
        class="v3-sidebar__collapse"
        :aria-label="collapsed ? '展开侧边栏' : '折叠侧边栏'"
        data-testid="v3-sidebar-collapse"
        @click="emit('update:collapsed', !collapsed)"
      >
        <PanelLeftClose v-if="!collapsed" :size="16" aria-hidden="true" />
        <PanelLeftOpen v-else :size="16" aria-hidden="true" />
        <span v-if="!collapsed">折叠</span>
      </button>
    </div>
  </nav>
</template>

<style scoped>
.v3-sidebar {
  display: flex;
  flex-direction: column;
  width: var(--v3-sidebar-width);
  height: 100%;
  background: var(--v3-surface);
  border-right: 1px solid var(--v3-border);
  transition: width var(--v3-duration-base) var(--v3-ease);
}
.v3-sidebar--collapsed {
  width: var(--v3-sidebar-collapsed-width);
}
.v3-sidebar--mobile {
  width: 260px;
}

.v3-sidebar__brand {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
  height: var(--v3-topbar-height);
  padding: 0 var(--v3-space-3);
  border-bottom: 1px solid var(--v3-border-subtle);
  flex-shrink: 0;
}
.v3-sidebar__mark {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: var(--v3-radius-sm);
  background: var(--v3-primary-soft);
  color: var(--v3-primary);
  font-size: 13px;
  font-weight: 800;
  flex-shrink: 0;
}
.v3-sidebar__brand-text {
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.v3-sidebar__nav {
  list-style: none;
  margin: 0;
  padding: var(--v3-space-3) var(--v3-space-2);
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  overflow-y: auto;
}

.v3-sidebar__link {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
  width: 100%;
  min-height: 40px;
  padding: 0 var(--v3-space-3);
  border: none;
  border-radius: var(--v3-radius-sm);
  background: transparent;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-md);
  font-weight: 600;
  cursor: pointer;
  text-align: left;
  transition: background var(--v3-duration-fast) var(--v3-ease), color var(--v3-duration-fast) var(--v3-ease);
}
.v3-sidebar__link:hover {
  background: var(--v3-surface-hover);
  color: var(--v3-text);
}
.v3-sidebar__link--active {
  background: var(--v3-primary-soft);
  color: var(--v3-primary);
}
.v3-sidebar--collapsed .v3-sidebar__link {
  justify-content: center;
  padding-inline: 0;
}

.v3-sidebar__label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.v3-sidebar__footer {
  padding: var(--v3-space-2);
  border-top: 1px solid var(--v3-border-subtle);
  flex-shrink: 0;
}
.v3-sidebar__collapse {
  display: flex;
  align-items: center;
  gap: var(--v3-space-2);
  width: 100%;
  min-height: 36px;
  padding: 0 var(--v3-space-3);
  border: none;
  border-radius: var(--v3-radius-sm);
  background: transparent;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
  cursor: pointer;
}
.v3-sidebar__collapse:hover {
  background: var(--v3-surface-hover);
  color: var(--v3-text);
}
.v3-sidebar--collapsed .v3-sidebar__collapse {
  justify-content: center;
  padding-inline: 0;
}
</style>
