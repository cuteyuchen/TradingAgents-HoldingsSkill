import { h, type VNodeChild } from 'vue'
import { NCode, NTag } from 'naive-ui'

export const emptyText = '—'

export function fmtDateTime(value?: string | null): string {
  return value ? value.slice(0, 16).replace('T', ' ') : emptyText
}

export function fmtPct(value?: number | null): string {
  return value == null ? emptyText : `${(value * 100).toFixed(2)}%`
}

export function pctClass(value?: number | null): string {
  return value == null ? '' : value >= 0 ? 'pos' : 'neg'
}

export function renderPct(value?: number | null): VNodeChild {
  return h('span', { class: pctClass(value) }, fmtPct(value))
}

export function renderCode(value?: string | null): VNodeChild {
  return value ? h(NCode, { code: value, inline: true }) : emptyText
}

export function renderGrade(grade?: string | null): VNodeChild {
  if (!grade) return emptyText
  const type = ['A'].includes(grade) ? 'success' : grade === 'B' ? 'warning' : 'error'
  return h(NTag, { size: 'small', round: true, bordered: false, type }, { default: () => grade })
}

export function speakerLabel(s: string): string {
  return ({ bull: '多头', bear: '空头', aggressive: '激进', conservative: '保守', neutral: '中立' }[s] || s)
}

export function renderSpeaker(s: string): VNodeChild {
  const type = s === 'bull' || s === 'aggressive' ? 'error' : s === 'bear' || s === 'conservative' ? 'success' : 'info'
  return h(NTag, { size: 'small', round: true, bordered: false, type }, { default: () => speakerLabel(s) })
}

export function statusLabel(s: string): string {
  return ({ open: '待回应', addressed: '已回应', resolved: '已定论', unresolved: '未解决', accepted: '已采纳' }[s] || s)
}

export function renderStatus(s?: string | null): VNodeChild {
  if (!s) return emptyText
  const type = s === 'resolved' || s === 'accepted' || s === '正常' || s === '启用'
    ? 'success'
    : s === 'unresolved' || s === '已停用' || s === '降级'
      ? 'error'
      : s === 'addressed'
        ? 'warning'
        : 'info'
  return h(NTag, { size: 'small', round: true, bordered: false, type }, { default: () => statusLabel(s) })
}

export function renderMuted(value?: string | number | null): VNodeChild {
  return h('span', { class: 'muted' }, value ?? emptyText)
}
