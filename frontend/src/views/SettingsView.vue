<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { BarChart3, Bot, CalendarClock, Database, Palette, Plus, RefreshCw, Server, ShieldCheck, SlidersHorizontal } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'

import { api } from '../api'
import type { FuyaoCapabilityStatus, FuyaoStatus, ModelProvider } from '../api/types'
import EmptyState from '../components/EmptyState.vue'
import MetricTile from '../components/MetricTile.vue'
import PageHeader from '../components/PageHeader.vue'
import SectionCard from '../components/SectionCard.vue'
import StatusIndicator from '../components/StatusIndicator.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { usePortfolioContext } from '../composables/portfolio'
import SettingsOperationsView from './SettingsOperationsView.vue'
import GovernanceView from './GovernanceView.vue'
import SystemView from './SystemView.vue'

type SettingSection = 'data' | 'ai' | 'automation' | 'strategy' | 'system' | 'appearance'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const THEME_KEY = 'advisor_theme'
const activeSection = ref<SettingSection>(sectionFromQuery(route.query.section))
const theme = ref<'light' | 'dark'>(localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light')
const providers = ref<ModelProvider[]>([])
const fuyaoStatus = ref<FuyaoStatus | null>(null)
const fuyaoProbeLoading = ref(false)
const loading = ref(false)
const loadError = ref<unknown>(null)
const createOpen = ref(false)
const creating = ref(false)
const newPortfolioName = ref('')

const {
  portfolios,
  selectedPortfolioId,
  selectedPortfolio,
  loading: portfoliosLoading,
  error: portfolioError,
  loadPortfolios,
  setSelectedPortfolio,
} = usePortfolioContext()

const sections: Array<{ key: SettingSection; label: string; description: string; icon: typeof Database }> = [
  { key: 'data', label: '数据与行情', description: '组合与数据入口', icon: Database },
  { key: 'ai', label: 'AI 模型', description: '供应商与模型用途', icon: Bot },
  { key: 'automation', label: '自动分析', description: '计划与通知', icon: CalendarClock },
  { key: 'strategy', label: '策略参数', description: '高级治理与审批', icon: SlidersHorizontal },
  { key: 'system', label: '系统状态', description: '高级诊断与就绪度', icon: Server },
  { key: 'appearance', label: '外观', description: '亮色与暗色', icon: Palette },
]

function sectionFromQuery(value: unknown): SettingSection {
  const key = String(value || '')
  return ['data', 'ai', 'automation', 'strategy', 'system', 'appearance'].includes(key) ? key as SettingSection : 'data'
}

const dataStatus = computed<'ok' | 'setup' | 'degraded'>(() => {
  if (portfolioError.value || loadError.value) return 'degraded'
  if (!portfoliosLoading.value && !portfolios.value.length) return 'setup'
  return 'ok'
})
const dataStatusLabel = computed(() => ({ ok: '数据入口正常', setup: '需要配置', degraded: '需要检查' }[dataStatus.value]))
const currentSnapshotText = computed(() => selectedPortfolio.value?.latest_snapshot_time || '尚未确认')
const fuyaoCapabilityLabels: Record<string, string> = {
  quotes: '行情',
  calendar: '交易日历',
  historical: '历史行情',
  market_dumps: '市场导出',
  corporate_actions: '复权事件',
  financials: '财务',
  valuation: '估值',
  index: '指数',
  fund: '基金 / ETF',
  special_data: '特色数据',
}
const fuyaoCapabilities = computed(() => Object.entries(fuyaoStatus.value?.capabilities || {}).map(([key, value]) => ({ key, label: fuyaoCapabilityLabels[key] || key, value })))

function fuyaoStatusType(status: FuyaoCapabilityStatus | string | undefined): 'success' | 'warning' | 'error' | 'info' | 'default' {
  const value = typeof status === 'string' ? status : status?.status
  if (value === '已连接' || value === '已配置') return 'success'
  if (value === '上游异常') return 'error'
  if (value === '未授权' || value === '限流' || value === '数据未就绪' || value === '未配置') return 'warning'
  return 'info'
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    await loadPortfolios()
    providers.value = await api.listProviders()
    fuyaoStatus.value = await api.getFuyaoStatus().catch(() => null)
  } catch (reason) {
    loadError.value = reason
  } finally {
    loading.value = false
  }
}

async function probeFuyao() {
  if (!fuyaoStatus.value?.configured || fuyaoProbeLoading.value) return
  fuyaoProbeLoading.value = true
  try {
    fuyaoStatus.value = await api.getFuyaoStatus(true)
    message.success('Fuyao 能力状态已更新')
  } catch (reason) {
    message.error((reason as Error).message)
  } finally {
    fuyaoProbeLoading.value = false
  }
}

async function createPortfolio() {
  const name = newPortfolioName.value.trim()
  if (!name || creating.value) return
  creating.value = true
  try {
    const created = await api.createPortfolio({ name, is_default: portfolios.value.length === 0 })
    portfolios.value.push(created)
    setSelectedPortfolio(created.id)
    newPortfolioName.value = ''
    createOpen.value = false
    message.success('组合已创建')
  } catch (reason) {
    message.error((reason as Error).message)
  } finally {
    creating.value = false
  }
}

function selectSection(section: SettingSection) {
  activeSection.value = section
  void router.replace({ name: 'settings', query: section === 'data' ? {} : { section } })
}

function setTheme(value: 'light' | 'dark') {
  theme.value = value
  localStorage.setItem(THEME_KEY, value)
  window.dispatchEvent(new CustomEvent('advisor-theme-changed', { detail: { theme: value } }))
}

watch(() => route.query.section, (value) => { activeSection.value = sectionFromQuery(value) })
onMounted(() => void load())
</script>

<template>
  <section class="settings-workbench">
    <PageHeader title="设置" description="把数据、模型和自动化安排好，日常页面只保留和投资决策有关的信息。">
      <template #actions><StatusIndicator :status="dataStatus" :label="dataStatusLabel" /><n-button secondary :loading="loading" @click="load">刷新</n-button></template>
    </PageHeader>

    <div class="settings-layout">
      <aside class="settings-sidebar" aria-label="设置分区">
        <button v-for="item in sections" :key="item.key" class="settings-nav-item" :class="{ active: activeSection === item.key }" @click="selectSection(item.key)">
          <component :is="item.icon" :size="17" aria-hidden="true" />
          <span><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span>
        </button>
      </aside>

      <main class="settings-main">
        <template v-if="activeSection === 'data'">
          <SectionCard title="数据与行情" description="这里确认你的个人工作区是否有可用于分析的组合快照；行情质量和新鲜度会在首页与系统状态中显示。">
            <div class="metric-grid four">
              <MetricTile label="持仓组合" :value="portfolios.length" />
              <MetricTile label="当前组合" :value="selectedPortfolio?.name || '未选择'" />
              <MetricTile label="最近确认快照" :value="currentSnapshotText" />
              <MetricTile label="可用模型供应商" :value="providers.filter((item) => item.enabled).length" />
            </div>
          </SectionCard>
          <SectionCard title="同花顺金融数据" description="Fuyao 是主要 production financial data provider；能力不可用时，核心行情仍按配置回退，状态不会被伪装为健康。">
            <template #actions><n-button secondary size="small" :loading="fuyaoProbeLoading" :disabled="!fuyaoStatus?.configured" @click="probeFuyao"><template #icon><RefreshCw :size="14" /></template>探测能力</n-button></template>
            <div class="fuyao-summary"><div><span>连接状态</span><n-tag size="small" :bordered="false" :type="fuyaoStatusType(fuyaoStatus?.connection_status)">{{ fuyaoStatus?.connection_status || '未读取' }}</n-tag></div><div><span>配置状态</span><n-tag size="small" :bordered="false" :type="fuyaoStatus?.configured ? 'success' : 'warning'">{{ fuyaoStatus?.configured ? '已配置' : '未配置' }}</n-tag></div></div>
            <div class="capability-grid"><div v-for="item in fuyaoCapabilities" :key="item.key"><span>{{ item.label }}</span><n-tag size="small" :bordered="false" :type="fuyaoStatusType(item.value)">{{ item.value.status || '未知' }}</n-tag></div><p v-if="!fuyaoCapabilities.length" class="muted">暂未读取能力状态。</p></div>
            <p class="fuyao-note">API Key 仅在后端运行时使用；此处不会显示、保存或回传密钥。</p>
          </SectionCard>
          <SectionCard title="我的组合" description="组合仍由后端 Auth/Ownership 保护；这里只提供进入持仓工作流的入口。">
            <template #actions><n-button secondary size="small" @click="createOpen = true"><template #icon><Plus :size="15" /></template>新建组合</n-button></template>
            <div v-if="portfolios.length" class="portfolio-list">
              <div v-for="portfolio in portfolios" :key="portfolio.id" class="portfolio-row">
                <div><strong>{{ portfolio.name }}</strong><small>{{ portfolio.market }} · {{ portfolio.currency }} · 最近快照 {{ portfolio.latest_snapshot_time || '尚未确认' }}</small></div>
                <n-tag size="small" :bordered="false" :type="portfolio.id === selectedPortfolioId ? 'success' : 'default'">{{ portfolio.id === selectedPortfolioId ? '当前' : '可切换' }}</n-tag>
                <n-button secondary size="small" @click="router.push({ name: 'holdings', query: { portfolio: portfolio.id } })">查看持仓</n-button>
              </div>
            </div>
            <EmptyState v-else title="还没有个人投资组合" description="先进入持仓页导入一份确认快照，首页才会开始显示市场、组合和建议。">
              <template #action><n-button type="primary" @click="router.push({ name: 'holdings', query: { action: 'update' } })">开始导入</n-button></template>
            </EmptyState>
          </SectionCard>
          <SectionCard title="数据工作流" description="低质量识别不会自动确认；所有用于分析的持仓都必须经过 Review → Confirm。">
            <div class="flow-summary"><div><strong>1</strong><span>导入券商截图</span></div><div><strong>2</strong><span>核对并修正</span></div><div><strong>3</strong><span>确认快照</span></div><div><strong>4</strong><span>开始分析</span></div></div>
            <div class="section-actions"><n-button type="primary" @click="router.push({ name: 'holdings', query: { action: 'update' } })">更新持仓</n-button><n-button secondary @click="router.push({ name: 'settings', query: { section: 'system' } })">检查系统状态</n-button></div>
          </SectionCard>
        </template>

        <SettingsOperationsView v-else-if="activeSection === 'ai'" initial-tab="models" />
        <SettingsOperationsView v-else-if="activeSection === 'automation'" initial-tab="schedules" />
        <GovernanceView v-else-if="activeSection === 'strategy'" />
        <SystemView v-else-if="activeSection === 'system'" />

        <template v-else>
          <SectionCard title="外观" description="新环境默认使用亮色；已经明确保存的偏好会继续保留。">
            <div class="appearance-choice" role="radiogroup" aria-label="主题">
              <button class="theme-choice" :class="{ selected: theme === 'light' }" role="radio" :aria-checked="theme === 'light'" @click="setTheme('light')"><span class="theme-swatch light-swatch" /><span><strong>亮色</strong><small>清晰、适合日常查看</small></span><n-tag v-if="theme === 'light'" size="small" type="success">当前</n-tag></button>
              <button class="theme-choice" :class="{ selected: theme === 'dark' }" role="radio" :aria-checked="theme === 'dark'" @click="setTheme('dark')"><span class="theme-swatch dark-swatch" /><span><strong>暗色</strong><small>低亮度环境使用</small></span><n-tag v-if="theme === 'dark'" size="small" type="success">当前</n-tag></button>
            </div>
          </SectionCard>
          <SectionCard title="阅读偏好" description="主界面优先展示市场、组合和最终建议；运行时、参数 hash 与 source lineage 默认收进技术详情。">
            <div class="preference-note"><BarChart3 :size="18" /><div><strong>决策优先</strong><p>首页先回答“今天该不该动”，高级证据按需展开。</p></div></div>
            <TechnicalDetails title="外观设置技术详情"><pre>{{ JSON.stringify({ theme, storageKey: THEME_KEY }, null, 2) }}</pre></TechnicalDetails>
          </SectionCard>
        </template>
      </main>
    </div>

    <n-modal v-model:show="createOpen" preset="card" title="新建持仓组合" style="width: min(460px, calc(100vw - 32px))">
      <n-form label-placement="top" @submit.prevent="createPortfolio">
        <n-form-item label="组合名称">
          <n-input v-model:value="newPortfolioName" aria-label="组合名称" placeholder="例如：主账户、ETF 账户" @keyup.enter="createPortfolio" />
        </n-form-item>
        <n-button type="primary" block :loading="creating" :disabled="!newPortfolioName.trim()" @click="createPortfolio">创建组合</n-button>
      </n-form>
    </n-modal>
  </section>
</template>

<style scoped>
.settings-workbench { display: grid; gap: 18px; }
.settings-layout { display: grid; grid-template-columns: 230px minmax(0, 1fr); align-items: start; gap: 22px; }
.settings-sidebar { position: sticky; top: 82px; display: grid; gap: 4px; }
.settings-nav-item { display: grid; grid-template-columns: 22px minmax(0, 1fr); align-items: center; gap: 9px; width: 100%; border: 1px solid transparent; border-radius: 7px; background: transparent; padding: 11px 10px; color: var(--text-muted); text-align: left; cursor: pointer; }
.settings-nav-item:hover, .settings-nav-item.active { border-color: var(--border); background: var(--surface); color: var(--primary); }
.settings-nav-item > span { display: grid; gap: 2px; min-width: 0; }
.settings-nav-item strong { color: inherit; font-size: 13px; }
.settings-nav-item small { color: var(--text-muted); font-size: 11px; }
.settings-main { display: grid; gap: 18px; min-width: 0; }
.metric-grid { display: grid; gap: 14px; }
.metric-grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.portfolio-list { display: grid; gap: 8px; }
.portfolio-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 12px; border-top: 1px solid var(--border); padding: 12px 0; }
.portfolio-row:first-child { border-top: 0; padding-top: 0; }
.portfolio-row > div { display: grid; gap: 3px; min-width: 0; }
.portfolio-row small { color: var(--text-muted); font-size: 11px; }
.flow-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.flow-summary > div { display: grid; gap: 5px; border-left: 2px solid var(--primary); padding: 6px 10px; }
.flow-summary strong { color: var(--primary); font-size: 22px; }
.flow-summary span { color: var(--text-muted); font-size: 12px; }
.section-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
.fuyao-summary { display: flex; flex-wrap: wrap; gap: 12px 28px; margin-bottom: 16px; }.fuyao-summary > div { display: flex; align-items: center; gap: 8px; }.fuyao-summary span, .capability-grid span { color: var(--text-muted); font-size: 12px; }.capability-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }.capability-grid > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; border-top: 1px solid var(--border); padding: 10px 0; }.capability-grid p { grid-column: 1 / -1; margin: 0; }.fuyao-note { margin: 16px 0 0; color: var(--text-muted); font-size: 12px; }
.appearance-choice { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.theme-choice { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; align-items: center; gap: 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 13px; color: var(--text); text-align: left; cursor: pointer; }
.theme-choice.selected { border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary-soft); }
.theme-choice > span:nth-child(2) { display: grid; gap: 3px; }
.theme-choice small { color: var(--text-muted); font-size: 11px; }
.theme-swatch { display: block; width: 42px; height: 32px; border: 1px solid var(--border-strong); border-radius: 5px; }
.light-swatch { background: linear-gradient(135deg, #ffffff 0 68%, #e9f1fb 68%); }
.dark-swatch { background: linear-gradient(135deg, #182028 0 68%, #1c3855 68%); }
.preference-note { display: flex; align-items: flex-start; gap: 10px; color: var(--primary); }
.preference-note strong { color: var(--text); }
.preference-note p { margin: 4px 0 0; color: var(--text-muted); }
@media (max-width: 900px) { .settings-layout { grid-template-columns: 1fr; }.settings-sidebar { position: static; grid-template-columns: repeat(3, minmax(0, 1fr)); }.metric-grid.four { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 620px) { .settings-sidebar { grid-template-columns: repeat(2, minmax(0, 1fr)); }.appearance-choice, .flow-summary, .capability-grid { grid-template-columns: 1fr; }.portfolio-row { grid-template-columns: 1fr auto; }.portfolio-row > .n-button { grid-column: 1 / -1; width: 100%; }.metric-grid.four { grid-template-columns: 1fr; } }
</style>
