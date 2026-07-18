import { createRouter, createWebHistory } from 'vue-router'

import { hasSession } from './api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
    { path: '/upload', name: 'upload', component: () => import('./views/UploadView.vue') },
    { path: '/reports', name: 'reports', component: () => import('./views/ReportsView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
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
