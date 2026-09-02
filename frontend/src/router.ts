import { createRouter, createWebHistory } from 'vue-router'

import { hasSession } from './api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
    { path: '/holdings', name: 'holdings', component: () => import('./views/HoldingsView.vue') },
    { path: '/analysis', name: 'analysis', component: () => import('./views/AnalysisView.vue') },
    { path: '/simulation', name: 'simulation', component: () => import('./views/SimulationView.vue') },
    { path: '/history', name: 'history', component: () => import('./views/HistoryView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
    { path: '/upload', name: 'upload-alias', redirect: (to) => ({ name: 'holdings', query: { ...to.query, action: 'update' } }) },
    { path: '/reports', name: 'reports-alias', redirect: (to) => ({ name: 'analysis', query: { ...to.query } }) },
    { path: '/shadow', name: 'shadow-alias', redirect: (to) => ({ name: 'simulation', query: { ...to.query } }) },
    { path: '/research', name: 'research-alias', redirect: (to) => ({ name: 'history', query: { ...to.query, tab: 'research' } }) },
    { path: '/governance', name: 'governance-alias', redirect: (to) => ({ name: 'settings', query: { ...to.query, section: 'strategy' } }) },
    { path: '/system', name: 'system-alias', redirect: (to) => ({ name: 'settings', query: { ...to.query, section: 'system' } }) },
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
