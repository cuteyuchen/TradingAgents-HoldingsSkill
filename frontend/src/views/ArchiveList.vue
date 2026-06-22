<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { FileText, Image, Trash2 } from 'lucide-vue-next'
import { NButton, NPopconfirm, type DataTableColumns, useMessage } from 'naive-ui'
import { api } from '../api'
import type { ArchiveSummary } from '../api/types'
import { fmtDateTime, renderGrade } from '../utils/ui'

/*********************** 页面状态 *********************/
const router = useRouter()
const message = useMessage()
const archives = ref<ArchiveSummary[]>([])
const loading = ref(false)
const deletingId = ref<number | null>(null)
const err = ref('')

/*********************** 数据加载 *********************/
async function loadArchives() {
  loading.value = true
  err.value = ''
  try {
    archives.value = await api.listArchives()
  } catch (e) {
    err.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function removeArchive(id: number) {
  deletingId.value = id
  err.value = ''
  try {
    await api.deleteArchive(id)
    archives.value = archives.value.filter((item) => item.id !== id)
    message.success('归档已删除')
  } catch (e) {
    err.value = (e as Error).message
    message.error(err.value)
  } finally {
    deletingId.value = null
  }
}

/*********************** 表格配置 *********************/
const columns: DataTableColumns<ArchiveSummary> = [
  { title: '时间', key: 'timestamp', minWidth: 180, render: (row) => fmtDateTime(row.timestamp) },
  { title: '标题', key: 'title', minWidth: 220, render: (row) => row.title || '持仓分析归档' },
  { title: '检查点', key: 'checkpoint', width: 110, render: (row) => row.checkpoint || '—' },
  { title: '数据质量', key: 'data_quality_grade', width: 110, render: (row) => renderGrade(row.data_quality_grade) },
  { title: '持仓数', key: 'holdings_count', width: 100 },
  {
    title: '截图',
    key: 'has_screenshot',
    width: 92,
    render: (row) =>
      row.has_screenshot
        ? h(Image, { size: 18, class: 'table-icon' })
        : h(FileText, { size: 18, class: 'table-icon muted-icon' }),
  },
  {
    title: '操作',
    key: 'actions',
    width: 86,
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

const rowProps = (row: ArchiveSummary) => ({
  class: 'clickable',
  onClick: () => router.push(`/archives/${row.id}`),
})

onMounted(loadArchives)
</script>

<template>
  <n-card class="archive-list" title="分析归档">
    <template #header-extra>
      <n-tag type="info" round :bordered="false">共 {{ archives.length }} 条</n-tag>
    </template>

    <n-alert v-if="err" type="error" :show-icon="false" class="mb-3">{{ err }}</n-alert>
    <n-data-table
      v-if="archives.length || loading"
      :columns="columns"
      :data="archives"
      :loading="loading"
      :bordered="false"
      :single-line="false"
      :scroll-x="900"
      :row-props="rowProps"
    />
    <n-empty v-else description="暂无分析归档。先通过 Skill 完成一次建议并上传归档。" class="py-8" />
  </n-card>
</template>

<style scoped>
.archive-list {
  overflow: hidden;
}

.table-icon {
  color: var(--app-primary);
  vertical-align: middle;
}

.muted-icon {
  color: var(--app-text-muted);
}
</style>
