import { h, type VNodeChild } from 'vue'
import { NCode, NTag } from 'naive-ui'
import { statuses, systemStatusLabel } from './systemLocale'

export const emptyText = '—'
export const unavailableText = '不可用'
export const SHANGHAI_TIME_ZONE = 'Asia/Shanghai'

export function parseDate(value?: string | null): Date | null {
  if (!value) return null
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return new Date(`${value}T00:00:00+08:00`)
  // Backend DateTime columns are UTC even when ISO output has no offset.
  const parsed = new Date(/T/.test(value) && !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? `${value}Z` : value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function fmtDate(value?: string | null): string {
  const parsed = parseDate(value)
  if (!parsed) return emptyText
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(parsed).replaceAll('/', '-')
}

export function fmtDateTime(value?: string | null): string {
  const parsed = parseDate(value)
  if (!parsed) return emptyText
  const formatted = new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed)
  return `${formatted.replaceAll('/', '-')} 北京时间`
}

export function fmtTime(value?: string | null): string {
  const parsed = parseDate(value)
  if (!parsed) return emptyText
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed)
}

export function fmtPct(value?: number | null): string {
  return value == null ? emptyText : `${(value * 100).toFixed(2)}%`
}

export function formatPercent(value: unknown, digits = 1): string {
  if (value === null || value === undefined || value === '') return unavailableText
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(digits)}%` : unavailableText
}

export function formatNumber(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === '') return unavailableText
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : unavailableText
}

export function formatCurrency(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === '') return unavailableText
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? `￥${parsed.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
    : unavailableText
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
  return ({ bull: '多头', bear: '空头', aggressive: '激进', conservative: '保守', neutral: '中立' }[s.trim().toLowerCase()] || analystLabel(s))
}

export function renderSpeaker(s: string): VNodeChild {
  const type = s === 'bull' || s === 'aggressive' ? 'error' : s === 'bear' || s === 'conservative' ? 'success' : 'info'
  return h(NTag, { size: 'small', round: true, bordered: false, type }, { default: () => speakerLabel(s) })
}

export function statusLabel(s?: string | null): string {
  return systemStatusLabel(s)
}

export function ratingLabel(value?: string | null): string {
  if (!value) return emptyText
  return ({
    BUY: '买入', STRONG_BUY: '强烈建议买入', OVERWEIGHT: '增配', ADD: '加仓',
    HOLD: '持有', NEUTRAL: '中性', UNDERWEIGHT: '低配／减仓', REDUCE: '减仓',
    SELL: '卖出', STRONG_SELL: '强烈建议卖出', OUTPERFORM: '优于大盘', UNDERPERFORM: '弱于大盘',
  }[value.trim().replaceAll(' ', '_').toUpperCase()] || systemStatusLabel(value))
}

export function actionLabel(value?: string | null): string {
  if (!value) return emptyText
  const labels: Record<string, string> = {
    buy: '买入',
    add: '加仓',
    hold: '持有',
    reduce: '减仓',
    sell: '卖出',
    watch: '观察',
    rotate: '轮动',
    conditional_add: '条件加仓',
    new_position: '新建仓位',
    add_existing: '加仓',
    action: '需要调整', actionable: '建议调整', no_action: '无需操作', hold_only: '仅持有',
    watch_only: '仅观察', watchlist: '持续观察', rotation_watch: '轮动观察',
    conditional_buy: '条件买入', conditional_sell: '条件卖出', conditional_reduce: '条件减仓',
    trim: '减仓', exit: '清仓', close: '平仓', rebalance: '再平衡', wait: '等待条件', waiting: '等待条件',
    overweight: '增配', underweight: '减配', blocked: '受阻', data_gap: '数据不足', veto: '未批准',
  }
  return labels[value.trim().toLowerCase()] || systemStatusLabel(value)
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
  }[value.trim().toLowerCase()] || systemStatusLabel(value))
}

export function analystLabel(value?: string | null): string {
  if (!value) return emptyText
  return ({
    market: '市场分析', portfolio: '组合分析', analyst: '分析师',
    technical: '技术分析', technical_analyst: '技术分析师', market_analyst: '市场分析师',
    vpa: '量价分析', 'technical/vpa': '技术／量价分析',
    capital: '资金分析', capital_analyst: '资金分析师',
    news: '新闻分析', news_analyst: '新闻分析师',
    fundamental: '基本面分析', fundamentals: '基本面分析', fundamentals_analyst: '基本面分析师',
    'capital/news/fundamentals': '资金/新闻/基本面',
    sentiment: '情绪',
    policy: '政策',
    quality_gate: '质量门控',
    sentiment_analyst: '情绪分析师', social: '舆情分析', social_media: '舆情分析',
    bull: '多头研究员', bull_researcher: '多头研究员', bear: '空头研究员', bear_researcher: '空头研究员',
    research_manager: '研究总监', research_verdict: '研究裁决', trader: '交易员', trader_proposal: '交易方案',
    aggressive: '激进风控', aggressive_debator: '激进风控', risky: '激进风控',
    conservative: '保守风控', conservative_debator: '保守风控', safe: '保守风控',
    neutral: '中立风控', neutral_debator: '中立风控', risk_manager: '风控经理',
    portfolio_manager: '组合经理', portfolio_final: '组合最终裁决', investment_debate: '多空辩论', risk_debate: '风控辩论',
  }[value.trim().toLowerCase()] || (/\p{Script=Han}/u.test(value) ? value : '分析角色待确认'))
}

export function riskLevelLabel(value?: string | null): string {
  return ({ LOW: '低风险', MEDIUM: '中风险', HIGH: '高风险', EXTREME: '极高风险', CRITICAL: '极高风险', UNKNOWN: '未知' }[String(value || 'UNKNOWN').trim().toUpperCase()] || '未知')
}

export function candidateStageLabel(value?: string | null): string {
  const normalized = String(value || 'WATCH').trim().toUpperCase()
  return ({ ACTION: '需要调整', ACTIONABLE: '需要调整', WATCH: '持续观察', WATCHLIST: '持续观察', BLOCKED: '门禁受阻', GATE_BLOCKED: '门禁受阻' }[normalized] || systemStatusLabel(normalized))
}

export function ledgerSourceLabel(value?: string | null): string {
  return ({ MANUAL: '手动录入', IMPORT: '流水导入', CSV_IMPORT: '流水导入', LEDGER_IMPORT: '流水导入', BROKER: '券商同步', BROKER_SYNC: '券商同步', BROKER_IMPORT: '流水导入' }[String(value || '').trim().toUpperCase()] || (/\p{Script=Han}/u.test(value || '') ? value! : '来源待确认'))
}

export function ledgerEntryTypeLabel(value?: string | null): string {
  return ({ TRADE: '证券成交', BUY: '买入', SELL: '卖出', CASH_IN: '现金转入', CASH_OUT: '现金转出', DIVIDEND: '股息红利', FEE: '手续费', TAX: '税费', TRANSFER_IN: '证券转入', TRANSFER_OUT: '证券转出', CORPORATE_ACTION: '公司行为', OTHER: '其他' }[String(value || '').trim().toUpperCase()] || systemStatusLabel(value))
}

/** Translate known business enums in prose without altering names, hashes or evidence. */
export function localizedValue(value?: string | null): string {
  if (!value) return emptyText
  return statuses[value.trim().toUpperCase()] || value
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
