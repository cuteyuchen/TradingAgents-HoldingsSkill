<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Bell, Bot, CalendarClock, CheckCircle2, KeyRound, Play, Plus, TestTube2, Trash2 } from 'lucide-vue-next'
import { useDialog, useMessage } from 'naive-ui'

import { api } from '../api'
import type { ModelProfile, ModelProvider, NotificationChannel, Portfolio, Schedule } from '../api/types'

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const providers = ref<ModelProvider[]>([])
const profiles = ref<ModelProfile[]>([])
const portfolios = ref<Portfolio[]>([])
const schedules = ref<Schedule[]>([])
const notifications = ref<NotificationChannel[]>([])
const activeTab = ref('models')
const providerOpen = ref(false)
const profileOpen = ref(false)
const scheduleOpen = ref(false)
const notificationOpen = ref(false)
const saving = ref(false)
const testingId = ref<number | null>(null)

const providerForm = reactive({ provider: 'openai_compatible', display_name: '', base_url: '', api_key: '', enabled: true })
const profileForm = reactive({ provider_id: null as number | null, purpose: 'vision', model_name: '', temperature: 0.2, max_tokens: 4096, timeout: 90, is_default: true })
const scheduleForm = reactive({ portfolio_id: null as number | null, name: '开盘后分析', timezone: 'Asia/Shanghai', hour: 9, minute: 35, checkpoint: '09:35', mode: 'deep', stale_snapshot_days: 3, notify: true, enabled: true })
const notificationForm = reactive({ type: 'dingtalk', name: '', webhook: '', secret: '', enabled: true })

const providerOptions = [
  { label: 'OpenAI', value: 'openai' }, { label: 'OpenAI Compatible', value: 'openai_compatible' },
  { label: 'DeepSeek', value: 'deepseek' }, { label: '通义千问 Qwen', value: 'qwen' },
  { label: '智谱 GLM', value: 'glm' }, { label: 'MiniMax', value: 'minimax' },
  { label: 'Anthropic', value: 'anthropic' }, { label: 'Google Gemini', value: 'gemini' },
  { label: 'OpenRouter', value: 'openrouter' }, { label: 'Ollama', value: 'ollama' },
]
const purposeOptions = [
  { label: '识图模型', value: 'vision' }, { label: '分析模型（快速）', value: 'analysis' }, { label: '深度裁决模型', value: 'deep_analysis' },
]
const purposeLabels: Record<string, string> = { vision: '识图', analysis: '快速分析', deep_analysis: '深度裁决' }
const providerById = computed(() => Object.fromEntries(providers.value.map(item => [item.id, item])))

function fmt(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }

async function load() {
  loading.value = true
  try {
    const values = await Promise.all([api.listProviders(), api.listProfiles(), api.listPortfolios(), api.listSchedules(), api.listNotifications()])
    providers.value = values[0]; profiles.value = values[1]; portfolios.value = values[2]; schedules.value = values[3]; notifications.value = values[4]
  } catch (e) { message.error((e as Error).message) } finally { loading.value = false }
}

async function createProvider() {
  if (!providerForm.display_name.trim()) return
  saving.value = true
  try {
    await api.createProvider({ ...providerForm, base_url: providerForm.base_url || null, api_key: providerForm.api_key || null })
    providerOpen.value = false
    Object.assign(providerForm, { provider: 'openai_compatible', display_name: '', base_url: '', api_key: '', enabled: true })
    message.success('模型供应商已保存')
    await load()
  } catch (e) { message.error((e as Error).message) } finally { saving.value = false }
}

async function createProfile() {
  if (!profileForm.provider_id || !profileForm.model_name.trim()) return
  saving.value = true
  try {
    await api.createProfile({
      provider_id: profileForm.provider_id, purpose: profileForm.purpose, model_name: profileForm.model_name.trim(),
      parameters: { temperature: profileForm.temperature, max_tokens: profileForm.max_tokens, timeout: profileForm.timeout }, is_default: profileForm.is_default,
    })
    profileOpen.value = false
    profileForm.model_name = ''
    message.success('模型用途配置已保存')
    await load()
  } catch (e) { message.error((e as Error).message) } finally { saving.value = false }
}

async function testProfile(id: number) {
  testingId.value = id
  try {
    const result = await api.testProfile(id)
    message.success(`${result.message}${result.latency_ms ? `，${result.latency_ms} ms` : ''}`)
    await load()
  } catch (e) { message.error((e as Error).message) } finally { testingId.value = null }
}

async function setDefault(profile: ModelProfile) {
  try { await api.updateProfile(profile.id, { is_default: true }); await load(); message.success('默认模型已切换') } catch (e) { message.error((e as Error).message) }
}

async function createSchedule() {
  if (!scheduleForm.portfolio_id || !scheduleForm.name.trim()) return
  saving.value = true
  try {
    scheduleForm.checkpoint = `${String(scheduleForm.hour).padStart(2, '0')}:${String(scheduleForm.minute).padStart(2, '0')}`
    await api.createSchedule({ ...scheduleForm })
    scheduleOpen.value = false
    message.success('自动分析计划已创建')
    await load()
  } catch (e) { message.error((e as Error).message) } finally { saving.value = false }
}

async function toggleSchedule(row: Schedule) {
  try { await api.updateSchedule(row.id, { enabled: !row.enabled }); await load() } catch (e) { message.error((e as Error).message) }
}

async function runSchedule(row: Schedule) {
  testingId.value = row.id
  try { const job = await api.runScheduleNow(row.id); message.success(`已创建任务 #${job.id}`) } catch (e) { message.error((e as Error).message) } finally { testingId.value = null }
}

async function createNotification() {
  if (!notificationForm.name.trim() || !notificationForm.webhook.trim()) return
  saving.value = true
  try {
    await api.createNotification({ ...notificationForm, secret: notificationForm.secret || null })
    notificationOpen.value = false
    Object.assign(notificationForm, { type: 'dingtalk', name: '', webhook: '', secret: '', enabled: true })
    message.success('通知渠道已保存')
    await load()
  } catch (e) { message.error((e as Error).message) } finally { saving.value = false }
}

async function testNotification(row: NotificationChannel) {
  testingId.value = row.id
  try { const result = await api.testNotification(row.id); message.success(result.message || '测试消息已发送'); await load() } catch (e) { message.error((e as Error).message) } finally { testingId.value = null }
}

function confirmDelete(label: string, action: () => Promise<void>) {
  dialog.warning({ title: '确认删除', content: `删除“${label}”后无法恢复。`, positiveText: '删除', negativeText: '取消', async onPositiveClick() { try { await action(); message.success('已删除'); await load() } catch (e) { message.error((e as Error).message) } } })
}

onMounted(load)
</script>

<template>
  <section class="page-stack">
    <div class="page-heading"><div><p class="eyebrow">SYSTEM CONFIGURATION</p><h1>系统设置</h1><p>模型密钥、Webhook 和加签 Secret 均加密保存，页面不会回显明文。</p></div></div>

    <n-tabs v-model:value="activeTab" type="line" animated>
      <n-tab-pane name="models" tab="模型配置">
        <div class="settings-stack">
          <section class="panel-card">
            <div class="section-title"><div><h2>模型供应商</h2><p>支持官方接口、OpenAI Compatible 和本地 Ollama</p></div><n-button type="primary" @click="providerOpen = true"><template #icon><Plus :size="16" /></template>新增供应商</n-button></div>
            <div v-if="providers.length" class="card-grid">
              <article v-for="row in providers" :key="row.id" class="setting-card">
                <div class="setting-icon"><KeyRound :size="19" /></div><div><strong>{{ row.display_name }}</strong><p>{{ row.provider }} · {{ row.base_url || '使用默认地址' }}</p></div>
                <n-tag :bordered="false" :type="row.enabled ? 'success' : 'default'">{{ row.enabled ? '启用' : '停用' }}</n-tag>
                <div class="secret-state">API Key：{{ row.has_api_key ? row.api_key_masked : '未配置' }}</div>
                <n-button quaternary circle type="error" @click="confirmDelete(row.display_name, () => api.deleteProvider(row.id))"><template #icon><Trash2 :size="16" /></template></n-button>
              </article>
            </div><n-empty v-else description="先配置模型供应商和 API Key" />
          </section>

          <section class="panel-card">
            <div class="section-title"><div><h2>模型用途</h2><p>识图与分析可以使用不同供应商和模型</p></div><n-button secondary :disabled="!providers.length" @click="profileOpen = true"><template #icon><Plus :size="16" /></template>新增模型用途</n-button></div>
            <div v-if="profiles.length" class="profile-list">
              <article v-for="row in profiles" :key="row.id" class="profile-row">
                <div class="setting-icon"><Bot :size="19" /></div><div><strong>{{ purposeLabels[row.purpose] }}</strong><p>{{ providerById[row.provider_id]?.display_name }} / {{ row.model_name }}</p></div>
                <n-tag v-if="row.is_default" :bordered="false" type="success">默认</n-tag><n-tag v-else :bordered="false">备用</n-tag>
                <span class="health" :class="row.last_health_status || ''"><CheckCircle2 :size="14" />{{ row.last_health_status || '未测试' }}</span>
                <n-button v-if="!row.is_default" text type="primary" @click="setDefault(row)">设为默认</n-button>
                <n-button secondary :loading="testingId === row.id" @click="testProfile(row.id)"><template #icon><TestTube2 :size="15" /></template>测试</n-button>
                <n-button quaternary circle type="error" @click="confirmDelete(row.model_name, () => api.deleteProfile(row.id))"><template #icon><Trash2 :size="16" /></template></n-button>
              </article>
            </div><n-empty v-else description="至少配置一个默认识图模型和一个默认分析模型" />
          </section>
        </div>
      </n-tab-pane>

      <n-tab-pane name="schedules" tab="自动分析">
        <section class="panel-card">
          <div class="section-title"><div><h2>每日分析计划</h2><p>到点后校验 A 股交易日，并使用最近一次已确认持仓</p></div><n-button type="primary" :disabled="!portfolios.length" @click="scheduleOpen = true"><template #icon><Plus :size="16" /></template>新增计划</n-button></div>
          <div v-if="schedules.length" class="schedule-list">
            <article v-for="row in schedules" :key="row.id" class="schedule-row">
              <div class="setting-icon"><CalendarClock :size="19" /></div><div><strong>{{ row.name }}</strong><p>{{ portfolios.find(p => p.id === row.portfolio_id)?.name }} · {{ row.timezone }}</p></div>
              <div class="schedule-time">{{ String(row.hour).padStart(2, '0') }}:{{ String(row.minute).padStart(2, '0') }}<span>{{ row.mode === 'deep' ? '深度分析' : '快速分析' }}</span></div>
              <div class="schedule-meta"><span>下次：{{ fmt(row.next_run_at) }}</span><span>连续失败：{{ row.consecutive_failures }}/{{ row.max_consecutive_failures }}</span></div>
              <n-switch :value="row.enabled" @update:value="toggleSchedule(row)" />
              <n-button secondary :loading="testingId === row.id" @click="runSchedule(row)"><template #icon><Play :size="15" /></template>立即执行</n-button>
              <n-button quaternary circle type="error" @click="confirmDelete(row.name, () => api.deleteSchedule(row.id))"><template #icon><Trash2 :size="16" /></template></n-button>
            </article>
          </div><n-empty v-else description="尚未配置自动分析计划" />
        </section>
      </n-tab-pane>

      <n-tab-pane name="notifications" tab="钉钉 / 企微">
        <section class="panel-card">
          <div class="section-title"><div><h2>通知渠道</h2><p>分析完成后发送摘要和完整报告链接；发送失败不影响报告</p></div><n-button type="primary" @click="notificationOpen = true"><template #icon><Plus :size="16" /></template>新增渠道</n-button></div>
          <div v-if="notifications.length" class="card-grid">
            <article v-for="row in notifications" :key="row.id" class="setting-card">
              <div class="setting-icon"><Bell :size="19" /></div><div><strong>{{ row.name }}</strong><p>{{ row.type === 'dingtalk' ? '钉钉机器人' : '企业微信机器人' }} · {{ row.webhook_masked }}</p></div>
              <n-tag :bordered="false" :type="row.last_test_status === 'ok' ? 'success' : row.last_test_status === 'failed' ? 'error' : 'default'">{{ row.last_test_status || '未测试' }}</n-tag>
              <div class="secret-state">加签：{{ row.has_secret ? '已配置' : '未配置' }}</div>
              <n-button secondary :loading="testingId === row.id" @click="testNotification(row)"><template #icon><TestTube2 :size="15" /></template>测试发送</n-button>
              <n-button quaternary circle type="error" @click="confirmDelete(row.name, () => api.deleteNotification(row.id))"><template #icon><Trash2 :size="16" /></template></n-button>
            </article>
          </div><n-empty v-else description="尚未配置通知渠道" />
        </section>
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="providerOpen" preset="card" title="新增模型供应商" class="modal-card">
      <n-form label-placement="top"><n-form-item label="供应商类型"><n-select v-model:value="providerForm.provider" :options="providerOptions" /></n-form-item><n-form-item label="显示名称"><n-input v-model:value="providerForm.display_name" placeholder="例如：我的 DeepSeek" /></n-form-item><n-form-item label="Base URL"><n-input v-model:value="providerForm.base_url" placeholder="可留空使用内置默认地址" /></n-form-item><n-form-item label="API Key"><n-input v-model:value="providerForm.api_key" type="password" show-password-on="mousedown" placeholder="本地无鉴权模型可留空" /></n-form-item><n-button type="primary" block :loading="saving" @click="createProvider">保存供应商</n-button></n-form>
    </n-modal>
    <n-modal v-model:show="profileOpen" preset="card" title="新增模型用途" class="modal-card">
      <n-form label-placement="top"><n-form-item label="供应商"><n-select v-model:value="profileForm.provider_id" :options="providers.map(p => ({ label: p.display_name, value: p.id }))" /></n-form-item><n-form-item label="用途"><n-select v-model:value="profileForm.purpose" :options="purposeOptions" /></n-form-item><n-form-item label="模型名称"><n-input v-model:value="profileForm.model_name" placeholder="例如 gpt-4.1-mini / qwen-vl-max" /></n-form-item><div class="form-grid"><n-form-item label="Temperature"><n-input-number v-model:value="profileForm.temperature" :min="0" :max="2" :step="0.1" /></n-form-item><n-form-item label="Max Tokens"><n-input-number v-model:value="profileForm.max_tokens" :min="256" :max="128000" /></n-form-item><n-form-item label="超时（秒）"><n-input-number v-model:value="profileForm.timeout" :min="10" :max="600" /></n-form-item></div><n-form-item label="设为该用途默认模型"><n-switch v-model:value="profileForm.is_default" /></n-form-item><n-button type="primary" block :loading="saving" @click="createProfile">保存模型用途</n-button></n-form>
    </n-modal>
    <n-modal v-model:show="scheduleOpen" preset="card" title="新增自动分析计划" class="modal-card">
      <n-form label-placement="top"><n-form-item label="持仓组合"><n-select v-model:value="scheduleForm.portfolio_id" :options="portfolios.map(p => ({ label: p.name, value: p.id }))" /></n-form-item><n-form-item label="计划名称"><n-input v-model:value="scheduleForm.name" /></n-form-item><div class="form-grid"><n-form-item label="小时"><n-input-number v-model:value="scheduleForm.hour" :min="0" :max="23" /></n-form-item><n-form-item label="分钟"><n-input-number v-model:value="scheduleForm.minute" :min="0" :max="59" /></n-form-item><n-form-item label="模式"><n-select v-model:value="scheduleForm.mode" :options="[{ label: '深度', value: 'deep' }, { label: '快速', value: 'quick' }]" /></n-form-item></div><n-form-item label="时区"><n-input v-model:value="scheduleForm.timezone" /></n-form-item><n-form-item label="持仓超过多少天视为过期"><n-input-number v-model:value="scheduleForm.stale_snapshot_days" :min="0" :max="30" /></n-form-item><n-button type="primary" block :loading="saving" @click="createSchedule">保存计划</n-button></n-form>
    </n-modal>
    <n-modal v-model:show="notificationOpen" preset="card" title="新增通知渠道" class="modal-card">
      <n-form label-placement="top"><n-form-item label="类型"><n-select v-model:value="notificationForm.type" :options="[{ label: '钉钉机器人', value: 'dingtalk' }, { label: '企业微信机器人', value: 'wecom' }]" /></n-form-item><n-form-item label="渠道名称"><n-input v-model:value="notificationForm.name" placeholder="例如：交易提醒群" /></n-form-item><n-form-item label="Webhook"><n-input v-model:value="notificationForm.webhook" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" /></n-form-item><n-form-item label="加签 Secret（可选）"><n-input v-model:value="notificationForm.secret" type="password" show-password-on="mousedown" /></n-form-item><n-button type="primary" block :loading="saving" @click="createNotification">保存渠道</n-button></n-form>
    </n-modal>
  </section>
</template>

<style scoped>
.page-stack, .settings-stack { display: grid; gap: 18px; }.eyebrow { margin: 0 0 5px; color: var(--app-primary); font-size: 11px; font-weight: 900; letter-spacing: .13em; }h1 { margin: 0; font-size: clamp(28px, 4vw, 42px); letter-spacing: -.035em; }.page-heading p:not(.eyebrow), .section-title p { margin: 6px 0 0; color: var(--app-text-muted); }.panel-card { padding: 20px; }.section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 17px; }.section-title h2 { margin: 0; font-size: 17px; }.section-title p { font-size: 12px; }.card-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }.setting-card { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; border: 1px solid var(--app-border-soft); border-radius: 12px; padding: 14px; }.setting-icon { display: grid; width: 40px; height: 40px; place-items: center; border-radius: 10px; background: var(--app-primary-soft); color: var(--app-primary); }.setting-card p, .profile-row p, .schedule-row p { margin: 4px 0 0; color: var(--app-text-muted); font-size: 11px; }.secret-state { grid-column: 2 / 3; color: var(--app-text-muted); font-size: 11px; }.setting-card > .n-button:last-child { grid-column: 3; grid-row: 2; }.profile-list, .schedule-list { display: grid; gap: 8px; }.profile-row { display: grid; grid-template-columns: auto minmax(180px, 1fr) auto auto auto auto auto; align-items: center; gap: 10px; border: 1px solid var(--app-border-soft); border-radius: 11px; padding: 12px; }.health { display: inline-flex; align-items: center; gap: 4px; color: var(--app-text-muted); font-size: 11px; }.health.ok { color: #22c55e; }.health.failed { color: #ef4444; }.schedule-row { display: grid; grid-template-columns: auto minmax(170px, 1fr) auto minmax(220px, 1fr) auto auto auto; align-items: center; gap: 12px; border: 1px solid var(--app-border-soft); border-radius: 11px; padding: 13px; }.schedule-time { display: grid; font-size: 20px; font-weight: 900; }.schedule-time span, .schedule-meta span { color: var(--app-text-muted); font-size: 10px; font-weight: 400; }.schedule-meta { display: grid; gap: 3px; }.modal-card { width: min(560px, 94vw); }.form-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
@media (max-width: 1000px) { .card-grid { grid-template-columns: 1fr; }.profile-row, .schedule-row { grid-template-columns: auto 1fr auto; }.profile-row > *, .schedule-row > * { min-width: 0; }.schedule-meta, .secret-state { grid-column: 2 / 4; } }
@media (max-width: 620px) { .section-title { align-items: start; flex-direction: column; }.form-grid { grid-template-columns: 1fr; }.profile-row, .schedule-row { grid-template-columns: 1fr; }.setting-icon { display: none; }.schedule-meta, .secret-state { grid-column: auto; } }
</style>
