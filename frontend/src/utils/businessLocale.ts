import { systemStatusLabel } from './systemLocale'

/** Presentation labels only: raw values remain unchanged in requests and audit data. */
const businessLabels: Record<string, string> = {
  MARKET: '市场评分', CANDIDATE: '候选机会', PORTFOLIO: '投资组合',
  PORTFOLIO_DECISION: '组合决策', MEMORY_DECISION: '历史经验决策', BAR_FACTOR: '行情因子诊断',
  PRODUCTION_REPLAY: '生产决策回放', DETERMINISTIC_RECOMPUTE: '确定性重算', BAR_ONLY_DIAGNOSTIC: '仅行情诊断',
  MARKET_SCORE: '市场评分', CANDIDATE_RUNS: '候选分析记录', PORTFOLIO_SNAPSHOTS: '持仓快照',
  FUNDAMENTALS: '财务基本面', VALUATION: '估值', DAILY_BARS: '日线行情', DECISION_MEMORY: '决策经验',
  SECURITY_MASTER: '证券主数据', SECURITY_LIFECYCLE: '证券生命周期', TRADING_STATUS: '交易状态',
  ST_CLASSIFICATION: '风险警示分类', ETF_METADATA: '基金基础信息', PRICE_BASIS: '价格口径',
  AGGREGATE: '汇总', OVERALL: '整体', HOLDING: '持仓标的', SECURITY: '证券', ACCOUNT: '账户',
  BENCHMARK: '基准', ALL_A_MEDIAN_INDEX: '全A中位数', PORTFOLIO_ACTION: '组合行动', CANDIDATE_ACTION: '候选行动',
  DAILY_BAR: '日线行情', LIVE_QUOTE: '实时行情', SESSION_CLOSE: '当日收盘', PREVIOUS_SESSION_CLOSE: '上一交易日收盘',
  LIVE: '盘中实时', HISTORICAL: '历史行情', PROVIDER_DERIVED: '数据源估算',
  PRE_MARKET: '盘前', PRE_OPEN: '盘前', OPEN_AUCTION: '开盘集合竞价', MORNING: '上午交易',
  LUNCH_BREAK: '午间休市', AFTERNOON: '下午交易', CLOSE_AUCTION: '收盘集合竞价',
  AFTER_CLOSE: '收盘后', POST_CLOSE: '收盘后', NON_TRADING_DAY: '非交易日', DATA_ABNORMAL: '数据异常',
  CHECKPOINT: '检查点', OPENING: '开盘检查', MIDDAY: '午间检查', INTRADAY: '盘中检查',
  MANUAL: '手动', SCHEDULED: '定时计划', TRIGGER: '条件触发', TRIGGERED: '条件触发',
  FAST: '快速', STANDARD: '标准', DEEP: '深度', AUTO: '自动',
  SYSTEM: '系统配置', ENVIRONMENT: '环境配置', NONE: '未配置',
  CALIBRATION: '参数校准', CALIBRATION_REPORT: '参数校准报告', PARAMETER_SET: '参数版本',
  SYSTEM_BOOTSTRAP: '系统初始化', ROLLBACK: '回滚', MANUAL_EXCEPTION: '手工例外',
  DECISION_EDGE_THRESHOLD: '决策优势阈值', OPPORTUNITY_SCORE: '机会得分', ENTRY_SCORE: '入场时机得分',
  PORTFOLIO_FIT_SCORE: '组合契合度得分', RISK_REWARD_RATIO: '盈亏比', CONFIDENCE: '置信度',
  COVERAGE: '数据覆盖率', QUALITY_GATE: '质量门控', MARKET_SNAPSHOT: '市场快照',
  PORTFOLIO_SNAPSHOT: '持仓快照', INVESTMENT_DEBATE: '多空辩论', RESEARCH_VERDICT: '研究裁决',
  TRADER_PROPOSAL: '交易方案', RISK_REVISION: '风控修正', RISK_DEBATE: '风控辩论',
  CANDIDATE_SCREENING: '候选筛选', PORTFOLIO_SYNTHESIS: '组合综合决策',
  EVIDENCE_PACK: '证据汇总', ANALYST_REPORTS: '分析师报告', PREPARING: '准备输入',
  LOADING_INPUTS: '加载输入', REPLAY: '历史回放', RECOMPUTE: '历史重算',
  EVALUATING: '评估中', METRICS: '计算指标', FINALIZING: '汇总结果',
  BULL: '多头市场', BEAR: '空头市场', NEUTRAL: '中性市场', SIDEWAYS: '震荡市场',
  RISK_ON: '风险偏好上升', RISK_OFF: '风险偏好下降',
  HELD_POSITION: '现有持仓', NEW_POSITION: '新增仓位', DATA_QUALITY: '数据质量',
  BAR_FACTOR_DIAGNOSTIC: '行情因子诊断', CANDIDATE_FORWARD_OUTCOME: '候选远期结果',
  MARKET_SCORE_BUCKET: '市场评分分组', MEMORY_OUTCOME: '历史经验结果', PORTFOLIO_SNAPSHOT_DIAGNOSTIC: '持仓快照诊断',
  HARD_CAP_BREACH: '超过仓位硬上限', HARD_CAP_CONSTRAINT: '仓位硬上限约束',
  SECURITY_CODE_MISSING: '缺少证券代码', SECURITY_CLASSIFICATION_UNKNOWN: '证券分类待确认',
  INSUFFICIENT_CASH_DATA: '现金数据不足', HISTORICAL_SNAPSHOT_VALUATION: '使用历史快照估值',
  PRICE_LIMIT_UP: '涨停', PRICE_LIMIT_DOWN: '跌停', HIGH_PORTFOLIO_CORRELATION: '与持仓相关度过高',
  QUOTE_MISSING: '报价缺失', QUOTE_STALE: '报价过期', QUOTE_DEGRADED: '报价受限',
  QUOTE_UNAVAILABLE: '报价不可用', QUOTE_DATA_GAP: '报价数据不足',
  INSTRUMENT_NOT_FOUND: '标的未收录', INSTRUMENT_SUSPENDED: '标的停牌',
  MAJOR_INDEX_UNAVAILABLE: '主要指数不可用', MARKET_DATA_UNAVAILABLE: '市场数据不可用',
  INDEX_VOLUME_SEMANTICS_UNKNOWN: '指数成交量口径待确认', UNIT_SEMANTICS_UNKNOWN: '单位口径待确认',
  PROVIDER_FALLBACK: '使用备用数据源', CACHE_HIT: '使用缓存',
  TOTAL_TURNOVER_NON_POSITIVE: '成交总额异常', CRITICAL_MARKET_DATA_UNAVAILABLE: '关键市场数据不可用',
  CURRENT_MARKET_SCORE_UNAVAILABLE: '当前市场评分不可用', MAJOR_INDICES_SOURCE_UNAVAILABLE: '主要指数数据源不可用',
  NO_ACTIVE_PARAMETER_SET: '尚无启用参数版本', MULTIPLE_ACTIVE_PARAMETER_SETS: '存在多个启用参数版本',
  CONFIG_HASH_MISMATCH: '配置哈希不一致', GOVERNANCE_HEALTH_UNAVAILABLE: '治理健康状态不可用',
  HISTORICAL_PORTFOLIO_SNAPSHOT_MISSING: '缺少历史持仓快照', LEGACY_PRE_GOVERNANCE: '旧版数据尚未启用参数治理',
  STALE_DURABLE_JOBS: '后台任务已过期', PROPOSAL_CREATED: '已创建提案', PROPOSAL_SUBMITTED: '已提交提案',
  PROPOSAL_APPROVED: '已批准提案', PROPOSAL_REJECTED: '已拒绝提案', VERSION_VALIDATED: '已验证版本',
  VERSION_ACTIVATED: '已激活版本', ROLLBACK_PROPOSED: '已提出回滚',
  STOCK: '股票', ETF: '交易型基金', INDEX: '指数', RANGE: '震荡市场',
  FUYAO: '同花顺金融数据', MARKET_FOUNDATION: '市场基础数据',
}

export function businessLabel(value?: string | null): string {
  if (!value) return '—'
  if (/\p{Script=Han}/u.test(value) || /^[\d\s.,:%+\-~～—/]+$/.test(value)) return value
  const normalized = value.trim().replaceAll('-', '_').toUpperCase()
  if (/^[A-Za-z_]+:/.test(value)) {
    const [code, ...detail] = value.split(':')
    return `${businessLabel(code)}：${detail.join(':')}`
  }
  return businessLabels[normalized] || systemStatusLabel(value)
}
