import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ECharts from 'vue-echarts'
import 'echarts'
import naive from 'naive-ui'

import App from './App.vue'
import RunList from './views/RunList.vue'
import RunDetail from './views/RunDetail.vue'
import Holdings from './views/Holdings.vue'
import Candidates from './views/Candidates.vue'
import Watchlist from './views/Watchlist.vue'
import './styles.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/runs' },
    { path: '/runs', component: RunList },
    { path: '/runs/:id', component: RunDetail, props: true },
    { path: '/holdings', component: Holdings },
    { path: '/holdings/:code', component: Holdings, props: true },
    { path: '/candidates', component: Candidates },
    { path: '/watchlist', component: Watchlist },
  ],
})

const app = createApp(App)
app.use(router)
app.use(naive)
app.component('VChart', ECharts)
app.mount('#app')
