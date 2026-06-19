import { h, type VNodeChild } from 'vue'
import { NCode, NTag } from 'naive-ui'

export const emptyText = '—'

export function fmtDateTime(value?: string | null): string {
  return value ? value.slice(0, 16).replace('T', ' ') : emptyText
}

export function fmtPct(value?: number | null): string {
  return value == null ? emptyText : `${(value * 100).toFixed(2)}%`
}

export function fmtMoney(value?: number | null): string {
  return value == null ? emptyText : value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function pctClass(value?: number | null): string {
  return value == null ? '' : value >= 0 ? 'pos' : 'neg'
}

export function renderPct(value?: number | null): VNodeChild {
  return h('span', { class: pctClass(value) }, fmtPct(value))
}

export function renderPnl(value?: number | null, amount?: number | null): VNodeChild {
  if (amount == null) return renderPct(value)
  return h('div', { class: 'pnl-cell' }, [
    h('span', { class: pctClass(value) }, fmtPct(value)),
    h('span', { class: 'muted pnl-amount' }, `金额 ${fmtMoney(amount)}`),
  ])
}

export function renderCode(value?: string | null): VNodeChild {
  return value ? h(NCode, { code: value, inline: true }) : emptyText
}

export function instrumentLabel(name?: string | null, code?: string | null): string {
  if (name && code) return `${name}（${code}）`
  return name || code || emptyText
}

export function renderInstrument(name?: string | null, code?: string | null): VNodeChild {
  if (!name && !code) return emptyText
  return h('span', { class: 'instrument-label' }, [
    name ? h('span', { class: 'instrument-name' }, name) : null,
    name && code ? ' ' : null,
    code ? renderCode(code) : null,
  ])
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

export function ratingLabel(value?: string | null): string {
  if (!value) return emptyText
  return ({
    Buy: '买入',
    Overweight: '增配',
    Hold: '持有',
    Underweight: '低配/减仓',
    Sell: '卖出',
  }[value] || value)
}

export function actionLabel(value?: string | null): string {
  if (!value) return emptyText
  return ({
    Buy: '买入',
    Add: '加仓',
    Hold: '持有',
    Reduce: '减仓',
    Sell: '卖出',
    Watch: '观察',
    Rotate: '轮动',
  }[value] || value)
}

export function qualityCheckLabel(value?: string | null): string {
  if (!value) return emptyText
  return ({
    pass: '通过',
    passed: '通过',
    fail: '失败',
    failed: '失败',
    partial: '部分通过',
    caution: '谨慎',
    warning: '警示',
  }[value] || value)
}

export function analystLabel(value?: string | null): string {
  if (!value) return emptyText
  return ({
    technical: '技术',
    vpa: '量价/VPA',
    'technical/VPA': '技术/量价',
    capital: '资金',
    news: '新闻',
    fundamentals: '基本面',
    'capital/news/fundamentals': '资金/新闻/基本面',
    sentiment: '情绪',
    policy: '政策',
    quality_gate: '质量门控',
  }[value] || value)
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
