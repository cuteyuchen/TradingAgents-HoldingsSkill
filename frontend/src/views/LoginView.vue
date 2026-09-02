<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Activity, LockKeyhole } from 'lucide-vue-next'
import { useMessage } from 'naive-ui'

import { api, saveSession } from '../api'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const mode = ref<'login' | 'register'>('login')
const email = ref('')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

const title = computed(() => (mode.value === 'login' ? '进入投资驾驶舱' : '建立本地安全入口'))

async function submit() {
  error.value = ''
  if (!email.value.trim() || !password.value) {
    error.value = '请填写邮箱和密码'
    return
  }
  if (mode.value === 'register' && password.value !== confirmPassword.value) {
    error.value = '两次密码输入不一致'
    return
  }
  loading.value = true
  try {
    const tokens = mode.value === 'login'
      ? await api.login({ email: email.value.trim(), password: password.value, device_info: navigator.userAgent })
      : await api.register({ email: email.value.trim(), username: username.value.trim() || undefined, password: password.value })
    saveSession(tokens)
    window.dispatchEvent(new CustomEvent('advisor-session-changed'))
    message.success(mode.value === 'login' ? '登录成功' : '账户创建成功')
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.replace(redirect)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function switchMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  error.value = ''
}
</script>

<template>
  <div class="login-page">
    <section class="intro-panel">
      <div class="intro-content">
        <div class="intro-badge"><Activity :size="18" /> 投资驾驶舱</div>
        <h1>每天打开，先看清楚<br><span>今天该不该动</span></h1>
        <p>从最近确认的持仓快照出发，查看市场、组合、最终建议和模拟跟随记录。技术证据会在需要时展开。</p>
        <div class="feature-grid">
          <div><strong>今日市场</strong><span>先看状态、质量与数据新鲜度</span></div>
          <div><strong>我的组合</strong><span>只围绕已确认持仓做判断</span></div>
          <div><strong>最终建议</strong><span>候选之前，先看组合决策</span></div>
          <div><strong>模拟跟随</strong><span>记录结果，但不会发送真实订单</span></div>
        </div>
      </div>
    </section>

    <section class="form-panel">
      <div class="login-card panel-card">
        <div class="login-icon"><LockKeyhole :size="25" /></div>
        <div>
          <h2>{{ title }}</h2>
          <p>{{ mode === 'login' ? '登录后进入你的个人投资工作区。' : '首次使用时创建本地登录账户。' }}</p>
        </div>
        <n-alert v-if="route.query.expired" type="warning" :show-icon="false">登录状态已过期，请重新登录。</n-alert>
        <n-form label-placement="top" @submit.prevent="submit">
          <n-form-item v-if="mode === 'register'" label="显示名称">
            <n-input v-model:value="username" placeholder="可选" size="large" :input-props="{ autocomplete: 'name' }" />
          </n-form-item>
          <n-form-item label="邮箱">
            <n-input v-model:value="email" type="email" placeholder="name@example.com" size="large" :input-props="{ autocomplete: 'email' }" @keyup.enter="submit" />
          </n-form-item>
          <n-form-item label="密码">
            <n-input v-model:value="password" type="password" show-password-on="mousedown" placeholder="至少 8 位" size="large" :input-props="{ autocomplete: mode === 'register' ? 'new-password' : 'current-password' }" @keyup.enter="submit" />
          </n-form-item>
          <n-form-item v-if="mode === 'register'" label="确认密码">
            <n-input v-model:value="confirmPassword" type="password" show-password-on="mousedown" size="large" :input-props="{ autocomplete: 'new-password' }" @keyup.enter="submit" />
          </n-form-item>
          <n-alert v-if="error" type="error" :show-icon="false" class="mb-3">{{ error }}</n-alert>
          <n-button type="primary" block size="large" :loading="loading" @click="submit">
            {{ mode === 'login' ? '登录' : '创建账户并登录' }}
          </n-button>
        </n-form>
        <button class="mode-switch" type="button" @click="switchMode">
          {{ mode === 'login' ? '首次使用？创建账户' : '已有账户？返回登录' }}
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.login-page { display: grid; min-height: 100dvh; grid-template-columns: minmax(0, 1.15fr) minmax(420px, .85fr); }
.intro-panel { display: grid; place-items: center; overflow: hidden; padding: 60px; background: #e8f0f8; color: #1b2530; }
.intro-content { position: relative; z-index: 1; width: min(680px, 100%); }
.intro-badge { display: inline-flex; align-items: center; gap: 8px; border: 1px solid #b9cfe5; border-radius: 999px; background: rgba(255,255,255,.72); padding: 8px 13px; color: #245ea8; font-size: 12px; font-weight: 800; }
h1 { margin: 28px 0 18px; font-size: 54px; line-height: 1.08; letter-spacing: 0; }
h1 span { color: #245ea8; }
.intro-content > p { max-width: 590px; color: #536170; font-size: 17px; line-height: 1.8; }
.feature-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 40px; }
.feature-grid div { display: grid; gap: 4px; border: 1px solid #cbd9e7; border-radius: 8px; background: rgba(255,255,255,.7); padding: 16px; }
.feature-grid strong { color: #1b2530; }
.feature-grid span { color: #66727f; font-size: 12px; }
.form-panel { display: grid; place-items: center; background: var(--app-bg); padding: 32px; }
.login-card { display: grid; width: min(430px, 100%); gap: 18px; padding: 30px; }
.login-icon { display: grid; width: 52px; height: 52px; place-items: center; border-radius: 14px; background: var(--app-primary-soft); color: var(--app-primary); }
h2 { margin: 0; font-size: 25px; }
.login-card p { margin: 5px 0 0; color: var(--app-text-muted); }
.mode-switch { border: 0; background: transparent; color: var(--app-primary); cursor: pointer; font: inherit; font-weight: 700; }
:global(.theme-dark) .intro-panel { background: #182028; color: #eef3f7; }
:global(.theme-dark) .intro-badge { border-color: #465562; background: rgba(29,39,49,.88); color: #8fc0ff; }
:global(.theme-dark) h1 span { color: #8fc0ff; }
:global(.theme-dark) .intro-content > p { color: #a8b4bf; }
:global(.theme-dark) .feature-grid div { border-color: #2f3b46; background: #1d2731; }
:global(.theme-dark) .feature-grid strong { color: #eef3f7; }
:global(.theme-dark) .feature-grid span { color: #a8b4bf; }
@media (max-width: 900px) { .login-page { grid-template-columns: 1fr; } .intro-panel { display: none; } .form-panel { min-height: 100dvh; } }
</style>
