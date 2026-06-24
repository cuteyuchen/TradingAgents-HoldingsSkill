<script setup lang="ts">
import { computed, h, onMounted, ref, shallowRef } from 'vue'
import MarkdownIt from 'markdown-it'
import { FileText, Image, RotateCw, Trash2 } from 'lucide-vue-next'
import { NButton, NPopconfirm, type DataTableColumns, useMessage } from 'naive-ui'
import { api } from '../api'
import type { ArchiveDetail, ArchiveSummary } from '../api/types'
import { emptyText, fmtDateTime, renderGrade } from '../utils/ui'

type HoldingRow = Record<string, unknown>

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

/*********************** 页面状态 *********************/
const message = useMessage()
const archives = ref<ArchiveSummary[]>([])
const selectedArchiveId = shallowRef<number | null>(null)
const archiveDetail = ref<ArchiveDetail | null>(null)
const loadingArchives = shallowRef(false)
const loadingDetail = shallowRef(false)
const deletingId = shallowRef<number | null>(null)
const listError = shallowRef('')
const detailError = shallowRef('')

/*********************** 归档列表 *********************/
const selectedArchive = computed(() => archives.value.find((item) => item.id === selectedArchiveId.value) || null)

async function loadArchivesData(keepSelection = true) {
  loadingArchives.value = true
  listError.value = ''
  try {
    const rows = await api.listArchives()
    archives.value = rows
    const nextId = keepSelection && selectedArchiveId.value && rows.some((item) => item.id === selectedArchiveId.value)
      ? selectedArchiveId.value
      : rows[0]?.id ?? null
    if (nextId) {
      await selectArchive(nextId)
    } else {
      selectedArchiveId.value = null
      archiveDetail.value = null
    }
  } catch (e) {
    listError.value = (e as Error).message
  } finally {
    loadingArchives.value = false
  }
}

async function selectArchive(id: number) {
  selectedArchiveId.value = id
  loadingDetail.value = true
  detailError.value = ''
  try {
    archiveDetail.value = await api.getArchive(id)
  } catch (e) {
    archiveDetail.value = null
    detailError.value = (e as Error).message
  } finally {
    loadingDetail.value = false
  }
}

async function removeArchive(id: number) {
  deletingId.value = id
  listError.value = ''
  try {
    await api.deleteArchive(id)
    archives.value = archives.value.filter((item) => item.id !== id)
    message.success('归档已删除')
    if (selectedArchiveId.value === id) {
      const nextId = archives.value[0]?.id ?? null
      selectedArchiveId.value = nextId
      archiveDetail.value = null
      if (nextId) await selectArchive(nextId)
    }
  } catch (e) {
    listError.value = (e as Error).message
    message.error(listError.value)
  } finally {
    deletingId.value = null
  }
}

/*********************** 建议渲染 *********************/
const renderedAdvice = computed(() => markdown.render(archiveDetail.value?.advice_md || ''))
const tocEntries = computed(() => {
  const source = archiveDetail.value?.advice_md || ''
  return source
    .split('\n')
    .filter((line) => line.startsWith('## '))
    .map((line) => line.replace(/^##\s+/, '').trim())
    .filter(Boolean)
})

/*********************** 持仓数据 *********************/
const holdingRows = computed<HoldingRow[]>(() => {
  const holdings = archiveDetail.value?.holdings
  if (Array.isArray(holdings)) return holdings.filter((item): item is HoldingRow => !!item && typeof item === 'object')
  if (holdings && typeof holdings === 'object' && Array.isArray((holdings as Record<string, unknown>).holdings)) {
    return ((holdings as Record<string, unknown>).holdings as unknown[]).filter(
      (item): item is HoldingRow => !!item && typeof item === 'object',
    )
  }
  return []
})

const rawHoldingsJson = computed(() => JSON.stringify(archiveDetail.value?.holdings ?? null, null, 2))

function textCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return emptyText
  return String(value)
}

/*********************** 表格配置 *********************/
const archiveColumns: DataTableColumns<ArchiveSummary> = [
  { title: '时间', key: 'timestamp', minWidth: 150, render: (row) => fmtDateTime(row.timestamp) },
  { title: '标题', key: 'title', minWidth: 180, render: (row) => row.title || '持仓分析归档' },
  { title: '检查点', key: 'checkpoint', width: 98, render: (row) => row.checkpoint || emptyText },
  { title: '质量', key: 'data_quality_grade', width: 86, render: (row) => renderGrade(row.data_quality_grade) },
  { title: '持仓', key: 'holdings_count', width: 72 },
  {
    title: '截图',
    key: 'has_screenshot',
    width: 72,
    render: (row) =>
      row.has_screenshot
        ? h(Image, { size: 18, class: 'table-icon' })
        : h(FileText, { size: 18, class: 'table-icon muted-icon' }),
  },
  {
    title: '',
    key: 'actions',
    width: 62,
    render: (row) =>
      h('div', { onClick: (event: MouseEvent) => event.stopPropagation() }, [
        h(NPopconfirm, {
          positiveText: '删除',
          negativeText: '取消',
          showIcon: false,
          onPositiveClick: () => removeArchive(row.id),
        }, {
          trigger: () => h(NButton, {
            quaternary: true,
            circle: true,
            type: 'error',
            loading: deletingId.value === row.id,
            disabled: deletingId.value !== null && deletingId.value !== row.id,
            ariaLabel: '删除归档',
          }, { icon: () => h(Trash2, { size: 17 }) }),
          default: () => '删除这条归档？截图、持仓 JSON 和建议 Markdown 会一起删除。',
        }),
      ]),
  },
]

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

function archiveRowProps(row: ArchiveSummary) {
  return {
    class: 'clickable',
    onClick: () => selectArchive(row.id),
  }
}

function archiveRowClassName(row: ArchiveSummary): string {
  return row.id === selectedArchiveId.value ? 'selected-row' : ''
}

onMounted(() => {
  void loadArchivesData(false)
})
</script>

<template>
  <section class="archive-workspace">
    <div class="archive-header">
      <div>
        <h1 class="archive-title">分析归档</h1>
        <p class="archive-subtitle">共 {{ archives.length }} 条</p>
      </div>
      <n-button secondary :loading="loadingArchives" @click="loadArchivesData()">
        <template #icon><RotateCw :size="16" /></template>
        刷新
      </n-button>
    </div>

    <n-alert v-if="listError" type="error" :show-icon="false">{{ listError }}</n-alert>

    <div class="archive-grid">
      <section class="workspace-panel list-panel">
        <n-data-table
          v-if="archives.length || loadingArchives"
          :columns="archiveColumns"
          :data="archives"
          :loading="loadingArchives"
          :bordered="false"
          :single-line="false"
          :scroll-x="820"
          :row-props="archiveRowProps"
          :row-class-name="archiveRowClassName"
          size="small"
        />
        <n-empty v-else description="暂无分析归档" class="empty-state" />
      </section>

      <section class="detail-panel">
        <n-alert v-if="detailError" type="error" :show-icon="false">{{ detailError }}</n-alert>
        <div v-else-if="loadingDetail" class="workspace-panel">
          <n-skeleton text :repeat="6" />
        </div>
        <n-empty v-else-if="!archiveDetail" description="请选择归档" class="workspace-panel empty-state" />

        <template v-else>
          <section class="workspace-panel summary-panel">
            <div>
              <h2 class="detail-title">{{ archiveDetail.title || selectedArchive?.title || '持仓分析归档' }}</h2>
              <p class="detail-meta">
                {{ fmtDateTime(archiveDetail.timestamp) }} · {{ archiveDetail.checkpoint || '未标注检查点' }}
              </p>
            </div>
            <n-descriptions :column="4" label-placement="left" bordered size="small">
              <n-descriptions-item label="来源">{{ archiveDetail.holdings_source || emptyText }}</n-descriptions-item>
              <n-descriptions-item label="质量">{{ archiveDetail.data_quality_grade || emptyText }}</n-descriptions-item>
              <n-descriptions-item label="持仓">{{ holdingRows.length }}</n-descriptions-item>
              <n-descriptions-item label="截图">{{ archiveDetail.screenshot?.data_url ? '已归档' : '无' }}</n-descriptions-item>
            </n-descriptions>
          </section>

          <section class="workspace-panel advice-panel">
            <div class="section-heading">
              <h2>建议过程</h2>
              <n-tag v-if="tocEntries.length" :bordered="false" type="info">{{ tocEntries.length }} 个分区</n-tag>
            </div>
            <div class="advice-layout">
              <aside v-if="tocEntries.length" class="advice-toc">
                <div v-for="entry in tocEntries" :key="entry" class="toc-item">{{ entry }}</div>
              </aside>
              <article class="markdown-body" v-html="renderedAdvice" />
            </div>
          </section>

          <section class="workspace-panel holdings-panel">
            <div class="section-heading">
              <h2>解析持仓</h2>
            </div>
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
              size="small"
            />
            <n-empty v-else description="持仓 JSON 不是列表结构，查看原始数据。" />
            <n-collapse class="mt-3">
              <n-collapse-item title="原始 holdings.json" name="raw-holdings">
                <pre class="raw-json">{{ rawHoldingsJson }}</pre>
              </n-collapse-item>
            </n-collapse>
          </section>

          <section v-if="archiveDetail.screenshot" class="workspace-panel screenshot-panel">
            <div class="section-heading">
              <h2>持仓截图</h2>
            </div>
            <n-image
              v-if="archiveDetail.screenshot.data_url"
              class="screenshot-image"
              :src="archiveDetail.screenshot.data_url"
              object-fit="contain"
            />
            <n-empty v-else description="截图文件不可用" />
          </section>
        </template>
      </section>
    </div>
  </section>
</template>

<style scoped>
.archive-workspace {
  display: grid;
  gap: 18px;
  min-width: 0;
}

.archive-header {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 16px;
}

.archive-title,
.detail-title,
.section-heading h2 {
  margin: 0;
  color: var(--app-text);
  font-weight: 800;
  letter-spacing: 0;
}

.archive-title {
  font-size: 26px;
}

.archive-subtitle,
.detail-meta {
  margin: 6px 0 0;
  color: var(--app-text-muted);
}

.archive-grid {
  display: grid;
  grid-template-columns: minmax(420px, 0.9fr) minmax(0, 1.4fr);
  gap: 18px;
  align-items: start;
  min-width: 0;
}

.workspace-panel {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-surface);
  box-shadow: var(--app-shadow);
  padding: 16px;
}

.list-panel {
  position: sticky;
  top: 84px;
  padding: 8px;
}

.detail-panel {
  display: grid;
  gap: 18px;
  min-width: 0;
}

.summary-panel {
  display: grid;
  gap: 14px;
  border-color: color-mix(in srgb, var(--app-primary) 30%, var(--app-border));
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--app-primary) 10%, transparent), transparent 44%),
    var(--app-surface);
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.section-heading h2,
.detail-title {
  font-size: 18px;
}

.table-icon {
  color: var(--app-primary);
  vertical-align: middle;
}

.muted-icon {
  color: var(--app-text-muted);
}

.advice-layout {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
  min-width: 0;
}

.advice-toc {
  position: sticky;
  top: 94px;
  display: grid;
  gap: 8px;
  border: 1px solid var(--app-border-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--app-surface-strong) 76%, transparent);
  padding: 12px;
}

.toc-item {
  border-left: 2px solid color-mix(in srgb, var(--app-primary) 42%, transparent);
  color: var(--app-text-muted);
  line-height: 1.5;
  padding-left: 8px;
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
  letter-spacing: 0;
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

.screenshot-image {
  width: 100%;
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

.empty-state {
  padding: 36px 16px;
}

:deep(.selected-row td) {
  background: var(--app-row-hover);
}

@media (max-width: 1120px) {
  .archive-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .list-panel,
  .advice-toc {
    position: static;
  }
}

@media (max-width: 720px) {
  .archive-header {
    align-items: stretch;
    flex-direction: column;
  }

  .workspace-panel {
    padding: 12px;
  }

  .advice-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
