import { createRouter, createWebHistory } from 'vue-router'

import { hasSession } from './api'
// RouteMeta 类型扩展见 src/v3/types/router-meta.d.ts（由 tsconfig include）

/** Foundation showcase 仅 development 或 VITE_ENABLE_V3_FOUNDATION=true 可访问 */
function foundationEnabled(): boolean {
  const flag = import.meta.env.VITE_ENABLE_V3_FOUNDATION
  return import.meta.env.DEV || flag === 'true' || flag === '1'
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true, uiSystem: 'legacy' } },
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: () => import('./views/DashboardView.vue'), meta: { uiSystem: 'legacy' } },
    { path: '/holdings', name: 'holdings', component: () => import('./views/HoldingsView.vue'), meta: { uiSystem: 'legacy' } },
    { path: '/analysis', name: 'analysis', component: () => import('./views/AnalysisView.vue'), meta: { uiSystem: 'legacy' } },
    { path: '/simulation', name: 'simulation', component: () => import('./views/SimulationView.vue'), meta: { uiSystem: 'legacy' } },
    { path: '/history', name: 'history', component: () => import('./views/HistoryView.vue'), meta: { uiSystem: 'legacy' } },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue'), meta: { uiSystem: 'legacy' } },
    { path: '/upload', name: 'upload-alias', redirect: (to) => ({ name: 'holdings', query: { ...to.query, action: 'update' } }) },
    { path: '/reports', name: 'reports-alias', redirect: (to) => ({ name: 'analysis', query: { ...to.query } }) },
    { path: '/shadow', name: 'shadow-alias', redirect: (to) => ({ name: 'simulation', query: { ...to.query } }) },
    { path: '/research', name: 'research-alias', redirect: (to) => ({ name: 'history', query: { ...to.query, tab: 'research' } }) },
    { path: '/governance', name: 'governance-alias', redirect: (to) => ({ name: 'settings', query: { ...to.query, section: 'strategy' } }) },
    { path: '/system', name: 'system-alias', redirect: (to) => ({ name: 'settings', query: { ...to.query, section: 'system' } }) },
    // V3 Foundation — UI-0 showcase，不迁移业务页面
    {
      path: '/v3/foundation',
      name: 'v3-foundation',
      component: () => import('./v3/views/V3FoundationView.vue'),
      meta: { uiSystem: 'v3', public: true, foundation: true },
      beforeEnter: () => foundationEnabled() || { name: 'dashboard' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !hasSession()) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.name === 'login' && hasSession()) return { name: 'dashboard' }
  return true
})

window.addEventListener('advisor-auth-expired', () => {
  void router.replace({ name: 'login', query: { expired: '1' } })
})

export default router
