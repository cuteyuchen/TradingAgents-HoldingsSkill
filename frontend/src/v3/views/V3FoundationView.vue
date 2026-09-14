<script setup lang="ts">
/**
 * V3 Foundation Showcase
 * DEVELOPMENT SHOWCASE ONLY — 页面数据均为演示数据，不可冒充真实行情。
 */
import { ref } from 'vue'
import { useV3Dialog } from '../composables/useV3Dialog'
import { useV3Notify } from '../composables/useV3Notify'
import { useV3Theme } from '../composables/useV3Theme'
import V3ChartContainer from '../components/V3ChartContainer.vue'
import V3DataTimestamp from '../components/V3DataTimestamp.vue'
import V3DataTable from '../components/V3DataTable.vue'
import V3DetailDrawer from '../components/V3DetailDrawer.vue'
import V3EmptyState from '../components/V3EmptyState.vue'
import V3ErrorState from '../components/V3ErrorState.vue'
import V3LoadingState from '../components/V3LoadingState.vue'
import V3Metric from '../components/V3Metric.vue'
import V3PageHeader from '../components/V3PageHeader.vue'
import V3Section from '../components/V3Section.vue'
import V3StatusBadge from '../components/V3StatusBadge.vue'
import V3Tabs from '../components/V3Tabs.vue'

const { themePref, resolvedTheme, setThemePref } = useV3Theme()
const notify = useV3Notify()
const dialog = useV3Dialog()

const drawerOpen = ref(false)
const activeTab = ref('overview')
const loadingDemo = ref(false)
const errorDemo = ref(true)

const tabs = [
  { name: 'overview', label: '概览' },
  { name: 'quotes', label: '行情' },
  { name: 'analysis', label: '分析' },
  { name: 'history', label: '历史' },
]

const columns = [
  { name: 'code', label: '代码', field: 'code', align: 'left' as const },
  { name: 'name', label: '名称', field: 'name', align: 'left' as const },
  { name: 'price', label: '现价', field: 'price', align: 'right' as const },
  { name: 'change', label: '涨跌幅', field: 'change', align: 'right' as const },
  { name: 'status', label: '状态', field: 'status', align: 'center' as const },
]

/** 演示数据 — 非真实行情 */
const showcaseRows = [
  { id: 1, code: '000001.SH', name: '上证指数（演示）', price: '3120.50', change: '+0.85%', status: 'FRESH' },
  { id: 2, code: '399001.SZ', name: '深证成指（演示）', price: '9845.20', change: '-0.42%', status: 'FRESH' },
  { id: 3, code: '600519.SH', name: '贵州茅台（演示）', price: '1688.00', change: '+1.12%', status: 'STALE' },
]

async function handleConfirm(): Promise<void> {
  const ok = await dialog.confirm({
    title: '确认操作',
    message: '这是 Foundation 演示确认框，不会触发任何真实交易。',
  })
  if (ok) notify.success('已确认（演示）')
  else notify.info('已取消（演示）')
}

function toggleLoading(): void {
  loadingDemo.value = true
  window.setTimeout(() => {
    loadingDemo.value = false
  }, 1600)
}
</script>

<template>
  <div class="v3-foundation" data-testid="v3-foundation">
    <V3PageHeader
      eyebrow="V3 UI Foundation"
      title="Foundation Showcase"
      description="Quasar + V3 Design System 基础能力演示。本页数据均为 development showcase data，不可冒充真实市场行情。"
    >
      <template #status>
        <V3StatusBadge label="DEVELOPMENT SHOWCASE" tone="warning" />
      </template>
      <template #actions>
        <q-btn color="primary" no-caps unelevated data-testid="v3-foundation-notify" @click="notify.success('Notify 抽象可用', 'useV3Notify')">
          触发 Notify
        </q-btn>
        <q-btn outline no-caps color="primary" data-testid="v3-foundation-dialog" @click="handleConfirm">
          打开 Dialog
        </q-btn>
        <q-btn outline no-caps color="primary" data-testid="v3-foundation-drawer-open" @click="drawerOpen = true">
          打开 DetailDrawer
        </q-btn>
      </template>
    </V3PageHeader>

    <!-- Theme -->
    <V3Section title="Light / Dark / System" description="单一 theme authority，持久化到 localStorage advisor_theme。" compact class="v3-foundation__block">
      <div class="v3-foundation__row" data-testid="v3-theme-switcher">
        <q-btn-toggle
          :model-value="themePref"
          toggle-color="primary"
          no-caps
          :options="[
            { label: 'Light', value: 'light' },
            { label: 'Dark', value: 'dark' },
            { label: 'System', value: 'system' },
          ]"
          data-testid="v3-theme-options"
          @update:model-value="(v) => setThemePref(v as 'light' | 'dark' | 'system')"
        />
        <span class="v3-foundation__meta">resolved: <strong data-testid="v3-resolved-theme">{{ resolvedTheme }}</strong></span>
      </div>
    </V3Section>

    <!-- Metrics + Market colors -->
    <V3Section title="Metrics · A 股涨跌色" description="上涨=红，下跌=绿。trend 使用 market token，不使用 success/error。" class="v3-foundation__block">
      <div class="v3-foundation__metrics">
        <V3Metric label="上证指数（演示）" value="3120.50" secondary="+26.30" trend="up" />
        <V3Metric label="深证成指（演示）" value="9845.20" secondary="-41.50" trend="down" />
        <V3Metric label="平盘示意" value="1000.00" secondary="0.00" trend="flat" />
        <V3Metric label="组合市值（演示）" value="1,250,000" secondary="持仓 8 只" />
      </div>
      <div class="v3-foundation__swatches" data-testid="v3-market-colors">
        <span class="v3-swatch v3-swatch--up">market-up 涨</span>
        <span class="v3-swatch v3-swatch--down">market-down 跌</span>
        <span class="v3-swatch v3-swatch--flat">market-flat 平</span>
      </div>
    </V3Section>

    <!-- Risk / Status -->
    <V3Section title="Risk / Status 语义" description="风险与错误状态独立于涨跌色，禁止 risk-high = market-up。" class="v3-foundation__block">
      <div class="v3-foundation__badges" data-testid="v3-status-colors">
        <V3StatusBadge label="neutral" tone="neutral" />
        <V3StatusBadge label="info" tone="info" />
        <V3StatusBadge label="success" tone="success" />
        <V3StatusBadge label="warning" tone="warning" />
        <V3StatusBadge label="danger" tone="danger" />
        <V3StatusBadge label="blocked" tone="blocked" />
      </div>
      <div class="v3-foundation__badges" data-testid="v3-risk-colors">
        <span class="v3-risk-chip v3-risk-chip--low">risk-low</span>
        <span class="v3-risk-chip v3-risk-chip--medium">risk-medium</span>
        <span class="v3-risk-chip v3-risk-chip--high">risk-high</span>
        <span class="v3-risk-chip v3-risk-chip--unknown">risk-unknown</span>
      </div>
    </V3Section>

    <!-- Timestamp -->
    <V3Section title="数据时间戳" description="行情时间与策略分析时间必须分开表达。" compact class="v3-foundation__block">
      <V3DataTimestamp
        quote-at="2026-09-11T15:00:00+08:00"
        analysis-at="2026-09-11T15:10:00+08:00"
        quote-state="已收盘"
      />
    </V3Section>

    <!-- Buttons -->
    <V3Section title="Buttons" compact class="v3-foundation__block">
      <div class="v3-foundation__row">
        <q-btn color="primary" no-caps unelevated>Primary</q-btn>
        <q-btn outline color="primary" no-caps>Outline</q-btn>
        <q-btn flat color="primary" no-caps>Flat</q-btn>
        <q-btn unelevated color="negative" no-caps data-testid="v3-btn-danger">Danger</q-btn>
        <q-btn unelevated color="positive" no-caps>Success</q-btn>
        <q-btn unelevated color="warning" no-caps>Warning</q-btn>
      </div>
    </V3Section>

    <!-- Tabs -->
    <V3Section title="Tabs" class="v3-foundation__block">
      <V3Tabs v-model="activeTab" :items="tabs">
        <div data-testid="v3-tabs-panel">
          当前面板：<strong>{{ activeTab }}</strong>
          <p class="v3-foundation__meta">为后续 InstrumentDetail 的行情 / 盘口 / 资金 / 分析 / 新闻 / 历史预留。</p>
        </div>
      </V3Tabs>
    </V3Section>

    <!-- Table -->
    <V3Section title="DataTable" description="通用基础表格（演示数据）。" class="v3-foundation__block">
      <V3DataTable :columns="columns" :rows="showcaseRows" />
    </V3Section>

    <!-- States -->
    <V3Section title="Loading / Empty / Error" class="v3-foundation__block">
      <div class="v3-foundation__states">
        <div class="v3-foundation__state-box">
          <div class="v3-foundation__state-label">Loading（skeleton）</div>
          <V3LoadingState v-if="loadingDemo" variant="metric" :rows="2" />
          <div v-else class="v3-foundation__row">
            <q-btn outline no-caps dense @click="toggleLoading">播放 Loading</q-btn>
          </div>
        </div>
        <div class="v3-foundation__state-box">
          <div class="v3-foundation__state-label">Empty</div>
          <V3EmptyState title="暂无分析记录" description="运行一次分析后将在此展示结果。" />
        </div>
        <div class="v3-foundation__state-box">
          <div class="v3-foundation__state-label">Error</div>
          <V3ErrorState
            v-if="errorDemo"
            title="演示：加载失败"
            description="AbortError 不应弹 Toast，此处仅为错误态展示。"
            @retry="errorDemo = false"
          />
          <div v-else class="v3-foundation__row">
            <q-btn outline no-caps dense @click="errorDemo = true">重置 Error</q-btn>
            <V3StatusBadge label="已恢复" tone="success" />
          </div>
        </div>
      </div>
    </V3Section>

    <!-- Chart container -->
    <V3Section title="Chart Container" description="只提供壳，不自造 K 线。" class="v3-foundation__block">
      <V3ChartContainer title="价格走势（占位）" :height="180">
        <div class="v3-foundation__chart-placeholder" data-testid="v3-chart-placeholder">
          <span>Chart slot — 后续可接 ECharts / Lightweight Charts</span>
        </div>
      </V3ChartContainer>
    </V3Section>

    <!-- Typography -->
    <V3Section title="Typography · tabular-nums" class="v3-foundation__block">
      <div class="v3-foundation__type">
        <p class="v3-foundation__h">页面标题 H1</p>
        <p class="v3-foundation__h2">区块标题 H2</p>
        <p>正文：专业、克制、高信息密度的数据工作台。</p>
        <p class="v3-number text-market-up">价格 1234.56　涨跌 +1.23%</p>
        <p class="v3-number text-market-down">价格 987.65　涨跌 -0.88%</p>
        <p class="v3-number">金额 1,250,000.00　仓位 35.00%</p>
      </div>
    </V3Section>

    <!-- Detail Drawer -->
    <V3DetailDrawer
      v-model="drawerOpen"
      title="演示标的详情"
      subtitle="000001.SH（演示）"
      status-label="演示数据"
    >
      <template #tabs>
        <V3Tabs v-model="activeTab" :items="tabs" dense />
      </template>
      <V3Section title="抽屉内容占位" compact>
        <p class="v3-foundation__meta">
          UI-0 不接真实 Instrument 数据。下一阶段 V3-UI-1 将在此接入 Unified InstrumentDetail。
        </p>
        <div class="v3-foundation__metrics">
          <V3Metric label="现价（演示）" value="3120.50" trend="up" />
          <V3Metric label="涨跌幅（演示）" value="+0.85%" trend="up" />
        </div>
      </V3Section>
    </V3DetailDrawer>
  </div>
</template>

<style scoped>
.v3-foundation {
  display: flex;
  flex-direction: column;
  gap: var(--v3-space-5);
}
.v3-foundation__block { margin: 0; }
.v3-foundation__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--v3-space-2);
}
.v3-foundation__meta {
  margin: var(--v3-space-2) 0 0;
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
}
.v3-foundation__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--v3-space-4);
}
.v3-foundation__swatches {
  display: flex;
  flex-wrap: wrap;
  gap: var(--v3-space-2);
  margin-top: var(--v3-space-4);
}
.v3-swatch {
  border-radius: var(--v3-radius-sm);
  padding: 6px 12px;
  font-size: var(--v3-font-size-sm);
  font-weight: 600;
}
.v3-swatch--up {
  color: var(--v3-market-up);
  background: var(--v3-market-up-soft);
  border: 1px solid color-mix(in srgb, var(--v3-market-up) 30%, transparent);
}
.v3-swatch--down {
  color: var(--v3-market-down);
  background: var(--v3-market-down-soft);
  border: 1px solid color-mix(in srgb, var(--v3-market-down) 30%, transparent);
}
.v3-swatch--flat {
  color: var(--v3-market-flat);
  background: var(--v3-market-flat-soft);
  border: 1px solid var(--v3-border);
}
.v3-foundation__badges {
  display: flex;
  flex-wrap: wrap;
  gap: var(--v3-space-2);
  margin-bottom: var(--v3-space-3);
}
.v3-risk-chip {
  border-radius: var(--v3-radius-sm);
  padding: 4px 10px;
  font-size: var(--v3-font-size-xs);
  font-weight: 700;
}
.v3-risk-chip--low { color: var(--v3-risk-low); background: var(--v3-risk-low-soft); }
.v3-risk-chip--medium { color: var(--v3-risk-medium); background: var(--v3-risk-medium-soft); }
.v3-risk-chip--high { color: var(--v3-risk-high); background: var(--v3-risk-high-soft); }
.v3-risk-chip--unknown { color: var(--v3-risk-unknown); background: var(--v3-risk-unknown-soft); }
.v3-foundation__states {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--v3-space-4);
}
.v3-foundation__state-box {
  border: 1px dashed var(--v3-border);
  border-radius: var(--v3-radius-md);
  padding: var(--v3-space-3);
  min-height: 140px;
}
.v3-foundation__state-label {
  margin-bottom: var(--v3-space-2);
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-xs);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.v3-foundation__chart-placeholder {
  display: grid;
  place-items: center;
  width: 100%;
  min-height: 160px;
  border: 1px dashed var(--v3-border-strong);
  border-radius: var(--v3-radius-sm);
  color: var(--v3-text-muted);
  font-size: var(--v3-font-size-sm);
  background: var(--v3-surface-muted);
}
.v3-foundation__type p { margin: 0 0 var(--v3-space-2); }
.v3-foundation__h { font-size: var(--v3-font-size-3xl); font-weight: 700; }
.v3-foundation__h2 { font-size: var(--v3-font-size-xl); font-weight: 700; }
</style>
