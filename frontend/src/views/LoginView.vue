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

const title = computed(() => (mode.value === 'login' ? '登录持仓投研系统' : '创建账户'))

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
        <div class="intro-badge"><Activity :size="18" /> TradingAgents Holdings</div>
        <h1>把每日持仓，变成<br><span>可追踪的决策记录</span></h1>
        <p>上传券商截图，确认真实持仓，结合最新行情、技术数据和历史分析，生成结构化的组合建议。</p>
        <div class="feature-grid">
          <div><strong>独立模型</strong><span>识图与分析模型分别配置</span></div>
          <div><strong>历史记忆</strong><span>识别持仓变化和建议反转</span></div>
          <div><strong>自动执行</strong><span>开盘后按计划分析并通知</span></div>
          <div><strong>完整留痕</strong><span>截图、数据和报告可回溯</span></div>
        </div>
      </div>
    </section>

    <section class="form-panel">
      <div class="login-card panel-card">
        <div class="login-icon"><LockKeyhole :size="25" /></div>
        <div>
          <h2>{{ title }}</h2>
          <p>{{ mode === 'login' ? '使用你的账户进入系统。' : '首次部署可创建管理员账户。' }}</p>
        </div>
        <n-alert v-if="route.query.expired" type="warning" :show-icon="false">登录状态已过期，请重新登录。</n-alert>
        <n-form label-placement="top" @submit.prevent="submit">
          <n-form-item v-if="mode === 'register'" label="显示名称">
            <n-input v-model:value="username" placeholder="可选" size="large" />
          </n-form-item>
          <n-form-item label="邮箱">
            <n-input v-model:value="email" placeholder="name@example.com" size="large" @keyup.enter="submit" />
          </n-form-item>
          <n-form-item label="密码">
            <n-input v-model:value="password" type="password" show-password-on="mousedown" placeholder="至少 8 位" size="large" @keyup.enter="submit" />
          </n-form-item>
          <n-form-item v-if="mode === 'register'" label="确认密码">
            <n-input v-model:value="confirmPassword" type="password" show-password-on="mousedown" size="large" @keyup.enter="submit" />
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
.intro-panel { position: relative; display: grid; place-items: center; overflow: hidden; padding: 60px; background: radial-gradient(circle at 20% 20%, rgba(59,130,246,.3), transparent 35%), linear-gradient(145deg, #07111f, #0d2440 55%, #111827); color: white; }
.intro-panel::after { position: absolute; right: -18%; bottom: -38%; width: 680px; height: 680px; border: 1px solid rgba(147,197,253,.12); border-radius: 50%; content: ''; box-shadow: 0 0 0 80px rgba(147,197,253,.03), 0 0 0 160px rgba(147,197,253,.02); }
.intro-content { position: relative; z-index: 1; width: min(680px, 100%); }
.intro-badge { display: inline-flex; align-items: center; gap: 8px; border: 1px solid rgba(147,197,253,.25); border-radius: 999px; background: rgba(15,23,42,.45); padding: 8px 13px; color: #bfdbfe; font-size: 12px; font-weight: 800; }
h1 { margin: 28px 0 18px; font-size: clamp(38px, 5vw, 68px); line-height: 1.08; letter-spacing: -.045em; }
h1 span { color: #93c5fd; }
.intro-content > p { max-width: 590px; color: #cbd5e1; font-size: 17px; line-height: 1.8; }
.feature-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 40px; }
.feature-grid div { display: grid; gap: 4px; border: 1px solid rgba(148,163,184,.16); border-radius: 14px; background: rgba(15,23,42,.38); padding: 16px; backdrop-filter: blur(10px); }
.feature-grid strong { color: #eff6ff; }
.feature-grid span { color: #94a3b8; font-size: 12px; }
.form-panel { display: grid; place-items: center; background: var(--app-bg); padding: 32px; }
.login-card { display: grid; width: min(430px, 100%); gap: 18px; padding: 30px; }
.login-icon { display: grid; width: 52px; height: 52px; place-items: center; border-radius: 14px; background: var(--app-primary-soft); color: var(--app-primary); }
h2 { margin: 0; font-size: 25px; }
.login-card p { margin: 5px 0 0; color: var(--app-text-muted); }
.mode-switch { border: 0; background: transparent; color: var(--app-primary); cursor: pointer; font: inherit; font-weight: 700; }
@media (max-width: 900px) { .login-page { grid-template-columns: 1fr; } .intro-panel { display: none; } .form-panel { min-height: 100dvh; } }
</style>
