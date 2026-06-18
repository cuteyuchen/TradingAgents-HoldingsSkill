<script setup>
import { ref } from 'vue'
import { api, getToken, setToken } from './api'

const token = ref(getToken())
function saveToken() {
  setToken(token.value)
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">📊 持仓投研决策看板</div>
      <nav class="nav">
        <router-link to="/runs">决策列表</router-link>
        <router-link to="/holdings">持仓追踪</router-link>
        <router-link to="/candidates">候选跟踪</router-link>
        <router-link to="/watchlist">自选股</router-link>
      </nav>
      <input
        class="token"
        v-model="token"
        @change="saveToken"
        placeholder="Bearer Token"
        title="ADVISOR_TOKEN（写入 localStorage）"
      />
    </header>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<style>
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; background: #f5f6f8; color: #1f2329; }
.app { min-height: 100vh; }
.topbar {
  display: flex; align-items: center; gap: 24px;
  padding: 0 24px; height: 56px; background: #1f2329; color: #fff;
}
.brand { font-weight: 600; font-size: 16px; }
.nav { display: flex; gap: 16px; flex: 1; }
.nav a { color: #c9cdd4; text-decoration: none; font-size: 14px; padding: 6px 4px; }
.nav a.router-link-active { color: #fff; border-bottom: 2px solid #4e83f0; }
.token { background: #2a2f36; border: 1px solid #3a4048; color: #fff; padding: 6px 10px; border-radius: 4px; width: 220px; font-size: 12px; }
.content { padding: 24px; max-width: 1400px; margin: 0 auto; }
.card { background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.card h3 { margin: 0 0 12px; font-size: 15px; color: #1f2329; border-left: 3px solid #4e83f0; padding-left: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #eef0f3; }
th { color: #86909c; font-weight: 500; background: #fafbfc; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.grade-A { background: #e8f7ea; color: #00a854; }
.grade-B { background: #fff7e6; color: #d48806; }
.grade-C { background: #fff1f0; color: #cf1322; }
.grade-D, .grade-F { background: #fdeff0; color: #a8071a; }
.pos { color: #cf1322; }
.neg { color: #00a854; }
.muted { color: #86909c; }
button { cursor: pointer; padding: 6px 14px; border-radius: 4px; border: 1px solid #d9dce0; background: #fff; font-size: 13px; }
button:hover { background: #f2f3f5; }
</style>
