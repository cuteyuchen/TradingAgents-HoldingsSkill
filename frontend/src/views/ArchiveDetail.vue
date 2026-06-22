<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import MarkdownIt from 'markdown-it'
import type { DataTableColumns } from 'naive-ui'
import { api } from '../api'
import type { ArchiveDetail } from '../api/types'
import { emptyText, fmtDateTime } from '../utils/ui'

type HoldingRow = Record<string, unknown>

const props = defineProps<{ id: string }>()

/*********************** 页面状态 *********************/
const archive = ref<ArchiveDetail | null>(null)
const err = ref('')
const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

/*********************** Markdown 渲染 *********************/
const renderedAdvice = computed(() => markdown.render(archive.value?.advice_md || ''))
const tocEntries = computed(() => {
  const source = archive.value?.advice_md || ''
  return source
    .split('\n')
    .filter((line) => line.startsWith('## '))
    .map((line) => line.replace(/^##\s+/, '').trim())
    .filter(Boolean)
})

/*********************** 持仓数据 *********************/
const holdingRows = computed<HoldingRow[]>(() => {
  const holdings = archive.value?.holdings
  if (Array.isArray(holdings)) return holdings.filter((item): item is HoldingRow => !!item && typeof item === 'object')
  if (
    holdings &&
    typeof holdings === 'object' &&
    Array.isArray((holdings as Record<string, unknown>).holdings)
  ) {
    return ((holdings as Record<string, unknown>).holdings as unknown[]).filter(
      (item): item is HoldingRow => !!item && typeof item === 'object',
    )
  }
  return []
})

const rawHoldingsJson = computed(() => JSON.stringify(archive.value?.holdings ?? null, null, 2))

const textCell = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return emptyText
  return String(value)
}

const holdingColumns: DataTableColumns<HoldingRow> = [
  { title: '标的', key: 'name', minWidth: 150, render: (row) => textCell(row.name) },
  { title: '代码', key: 'code', width: 110, render: (row) => textCell(row.code) },
  { title: '总持仓', key: 'qty', width: 110, render: (row) => textCell(row.qty) },
  { title: '可用', key: 'available_qty', width: 110, render: (row) => textCell(row.available_qty) },
  { title: '不可用', key: 'unavailable_qty', width: 110, render: (row) => textCell(row.unavailable_qty) },
  { title: '现价', key: 'price', width: 100, render: (row) => textCell(row.price) },
  { title: '成本', key: 'cost', width: 100, render: (row) => textCell(row.cost) },
  { title: '可用说明', key: 'availability_note', minWidth: 260, render: (row) => textCell(row.availability_note) },
]

/*********************** 数据加载 *********************/
onMounted(async () => {
  try {
    archive.value = await api.getArchive(props.id)
  } catch (e) {
    err.value = (e as Error).message
  }
})
</script>

<template>
  <n-alert v-if="err" type="error" :show-icon="false">{{ err }}</n-alert>
  <n-card v-else-if="!archive">
    <n-skeleton text :repeat="4" />
  </n-card>
  <div v-else class="archive-detail">
    <n-card class="summary-card" :title="archive.title || '持仓分析归档'">
      <n-descriptions :column="4" label-placement="left" bordered size="small">
        <n-descriptions-item label="时间">{{ fmtDateTime(archive.timestamp) }}</n-descriptions-item>
        <n-descriptions-item label="检查点">{{ archive.checkpoint || '—' }}</n-descriptions-item>
        <n-descriptions-item label="来源">{{ archive.holdings_source || '—' }}</n-descriptions-item>
        <n-descriptions-item label="数据质量">{{ archive.data_quality_grade || '—' }}</n-descriptions-item>
      </n-descriptions>
    </n-card>

    <n-card v-if="archive.screenshot" title="持仓截图">
      <n-image
        v-if="archive.screenshot.data_url"
        class="screenshot-image"
        :src="archive.screenshot.data_url"
        object-fit="contain"
      />
      <n-empty v-else description="截图文件不可用" />
    </n-card>

    <n-card title="建议过程">
      <div class="advice-layout">
        <aside v-if="tocEntries.length" class="advice-toc">
          <div class="toc-title">分区</div>
          <div v-for="entry in tocEntries" :key="entry" class="toc-item">{{ entry }}</div>
        </aside>
        <article class="markdown-body" v-html="renderedAdvice" />
      </div>
    </n-card>

    <n-card title="解析持仓">
      <n-alert type="info" :show-icon="false" class="mb-3">
        可用数量只表示当前可卖/可交易数量，不可用数量可能来自挂单、冻结或 T+1 限制，不按已减仓处理。
      </n-alert>
      <n-data-table
        v-if="holdingRows.length"
        :columns="holdingColumns"
        :data="holdingRows"
        :bordered="false"
        :single-line="false"
        :scroll-x="1050"
      />
      <n-empty v-else description="持仓 JSON 不是列表结构，查看下方原始数据。" />
      <n-collapse class="mt-3">
        <n-collapse-item title="原始 holdings.json" name="raw-holdings">
          <pre class="raw-json">{{ rawHoldingsJson }}</pre>
        </n-collapse-item>
      </n-collapse>
    </n-card>
  </div>
</template>

<style scoped>
.archive-detail {
  display: grid;
  gap: 20px;
  min-width: 0;
}

.archive-detail :deep(.n-card),
.archive-detail :deep(.n-card-header),
.archive-detail :deep(.n-card-header__main),
.archive-detail :deep(.n-card-content) {
  min-width: 0;
}

.summary-card {
  border-color: color-mix(in srgb, var(--app-primary) 34%, var(--app-border));
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--app-primary) 10%, transparent), transparent 42%),
    var(--app-surface);
  box-shadow: var(--app-shadow-strong);
}

.screenshot-image {
  width: min(100%, 920px);
  overflow: hidden;
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  background: var(--app-surface-strong);
}

.screenshot-image :deep(img) {
  display: block;
  max-width: 100%;
  max-height: 680px;
  object-fit: contain;
}

.advice-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
  min-width: 0;
}

.advice-toc {
  position: sticky;
  top: 88px;
  display: grid;
  gap: 8px;
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-surface-strong) 76%, transparent);
  padding: 12px;
}

.toc-title {
  color: var(--app-text);
  font-weight: 800;
}

.toc-item {
  color: var(--app-text-muted);
  line-height: 1.5;
}

.markdown-body {
  min-width: 0;
  color: var(--app-text);
  font-size: 15px;
  line-height: 1.82;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 1.2em 0 0.55em;
  color: var(--app-text);
  line-height: 1.35;
}

.markdown-body :deep(h1) {
  margin-top: 0;
  font-size: 24px;
}

.markdown-body :deep(h2) {
  border-top: 1px solid var(--app-border-soft);
  padding-top: 18px;
  font-size: 20px;
}

.markdown-body :deep(h3) {
  font-size: 17px;
}

.markdown-body :deep(p),
.markdown-body :deep(ul),
.markdown-body :deep(ol),
.markdown-body :deep(table) {
  margin: 0 0 14px;
}

.markdown-body :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--app-border-soft);
  padding: 8px 10px;
  vertical-align: top;
}

.markdown-body :deep(code) {
  border-radius: 4px;
  background: color-mix(in srgb, var(--app-primary) 10%, transparent);
  padding: 2px 5px;
}

.raw-json {
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--app-border-soft);
  border-radius: 6px;
  background: color-mix(in srgb, var(--app-surface-strong) 78%, transparent);
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.7;
}

@media (max-width: 860px) {
  .advice-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .advice-toc {
    position: static;
  }
}
</style>
