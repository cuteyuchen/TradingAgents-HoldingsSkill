/** Chinese presentation only; API status and readiness decisions remain intact. */
export const statuses: Record<string, string> = {
  OK: '正常', READY: '已就绪', NOT_READY: '未就绪', READY_WITH_WARNINGS: '已就绪，有提醒',
  BLOCKED: '受阻', DEGRADED: '功能受限', CURRENT: '已更新', BEHIND: '待升级',
  AHEAD: '版本超前', BROKEN: '异常', UNKNOWN: '未知', ACTIVE: '启用中',
  PASS: '通过', WARNING: '需关注', FAILED: '失败', ERROR: '错误', FULL: '完整',
  PARTIAL: '部分完成', DATA_GAP: '数据不足', LEAKAGE_BLOCKED: '时间信息泄漏，已阻止',
  PENDING: '待处理', RUNNING: '运行中', PAUSED: '已暂停', DISABLED: '已停用',
  ENABLED: '已启用', COMPLETED: '已完成', DONE: '已完成', QUEUED: '排队中',
  CANCELLED: '已取消', STOPPED: '已停止', VALID: '有效', MISSING: '缺失',
  STALE: '已过期', HEALTHY: '健康', RECOVERING: '恢复中', FIXTURE: '验收模拟数据',
  NOT_CONFIGURED: '未配置', NOT_INSTALLED: '未安装', UNAVAILABLE: '不可用',
  VERIFIED: '已校验', MANUAL: '手动', DAILY: '每日', WEEKLY: '每周', PRE_UPGRADE: '升级前',
  SCHEDULED: '自动计划', PRE_RESTORE_SAFETY: '恢复前保护备份', RESTORED: '已恢复', NOT_RECENTLY_CHECKED: '最近未检查',
  SUCCESS: '成功', SUCCEEDED: '成功', FAIL: '失败', PASSED: '通过', EXPIRED: '已过期',
  HOLD: '持有', HOLD_ONLY: '仅持有', BUY: '买入', SELL: '卖出', ACTION: '需要调整', ACTIONABLE: '建议调整',
  NO_ACTION: '无需操作', WAITING: '等待条件', FILLED: '已成交', VOIDED: '已撤销', VETO: '未批准',
  DRAFT: '草稿', REVIEW: '待审核', PENDING_REVIEW: '待核对', CONFIRMED: '已确认',
  APPROVED: '已批准', REJECTED: '已拒绝', SUPERSEDED: '已被替代', CLOSED: '已关闭',
  OPEN: '待回应', ADDRESSED: '已回应', RESOLVED: '已定论', UNRESOLVED: '未解决', ACCEPTED: '已采纳',
  AMBIGUOUS: '存在歧义', INVALID: '无效', INVALIDATED: '已失效', INCOMPLETE: '未完成',
  AVAILABLE: '可用', UNSUPPORTED: '不支持', EMPTY: '暂无数据', PARSING: '识别中', PARSED: '已识别',
  WATCH: '持续观察', WATCHLIST: '持续观察', WATCH_ONLY: '仅观察', OBSERVING: '持续观察',
  GATE_BLOCKED: '门禁受阻', DECISION_BLOCKED: '决策受阻', NO_PORTFOLIO: '未选组合',
  INSUFFICIENT: '不足', INSUFFICIENT_DATA: '数据不足', INSUFFICIENT_EVIDENCE: '证据不足',
  INSUFFICIENT_LIVE_EVIDENCE: '实盘证据不足', DIAGNOSTIC_ONLY: '仅供诊断',
  FULL_PIT_EQUIVALENT: '完整历史时点重算', PARTIAL_PIT_RECOMPUTE: '部分历史输入缺失，仅供研究',
  KEEP_CURRENT: '保持当前参数', CONSIDER_CHANGE: '建议评审变更', REJECT_CHANGE: '不建议变更',
  ALIGNED: '已对齐', NO_MATCH: '未匹配', MATCHED: '已匹配', DUPLICATE: '重复记录',
  ELIGIBLE: '符合条件', INELIGIBLE: '不符合条件', LIVE: '实时', HISTORICAL: '历史',
  LIVE_ELIGIBLE: '符合实盘验证条件', LIVE_INELIGIBLE: '不符合实盘验证条件',
  CAUTION: '谨慎', CLEAN: '未发现泄露', NO_LEAKAGE: '未发现泄露',
  CANCELING: '取消中', CANCELLING: '取消中', CANCELED: '已取消', RETRYING: '重试中',
  NOT_STARTED: '未开始', NOT_APPLICABLE: '不适用', SKIPPED: '已跳过', TIMED_OUT: '已超时',
  NOT_PROVEN: '尚未证明', UNPROVEN: '尚未证明', PROVEN: '已证明',
  UNVERIFIED: '待校验', UNCONFIRMED: '未确认', ROLLED_BACK: '已回滚', ACTIVATED: '已激活',
  IDLE: '空闲', BUSY: '忙碌', CREATED: '已创建', DETECTED: '已检测', ANALYZING: '分析中',
  DISPATCHING: '分发中', CLAIMED: '已领取', RECLAIMED: '已重新领取', FAILURE: '失败',
  SENDING: '发送中', SENT: '已发送', LINKED: '已关联', RECHECKED: '已复核', FROZEN: '已冻结',
  REUSED: '已复用', DEDUPED: '已去重', MISSED: '已错过', NOT_SCHEDULED: '未安排',
  NOT_AVAILABLE: '不可用', NOT_MODELED: '未建模', REVIEW_ONLY: '仅供审核',
  WAITING_DATA: '等待数据', BLOCKED_FOR_ACTION: '行动受阻', INSUFFICIENT_SAMPLE: '样本不足',
  PARTIALLY_ACCEPTED: '部分采纳', PIT_INPUTS_READY: '历史时点输入已齐备',
  ROBUST_PLATEAU: '稳健参数区间', CIRCUIT_OPEN: '数据源暂时熔断', PROVIDER_DISABLED: '数据源已停用',
  FUTURE_QUOTE_AVAILABLE_UNFILLED: '后续行情已到达，尚未成交', NO_SHADOW_FILL: '尚无模拟成交',
  SHADOW_ACCOUNT_DATA_GAP: '模拟账户数据不足', NON_EXECUTABLE_TARGET: '目标暂不可执行',
  FRESH: '最新', UPLOADED: '已上传', VISION_PARSING: '识别中', IDENTITY_RESOLVING: '正在匹配证券身份',
  WAITING_CONFIRMATION: '待人工确认', NEEDS_MODEL: '缺少识图模型',
  A: 'A级', B: 'B级', C: 'C级', D: 'D级', F: 'F级',
  COMPLETE: '完整', TRACEABLE: '可追溯', EVALUATED: '已评估', DESCRIPTIVE: '描述性统计',
  CONDITION_PENDING: '等待条件', CONDITION_UNOBSERVED: '尚未观察到条件', NOT_TRIGGERED: '条件未触发',
  TRIGGERED: '条件已触发', RECORDED_ADVICE: '已记录建议', EXECUTED: '已执行',
  PARTIALLY_EXECUTED: '部分执行', EXECUTION_MISMATCH: '执行不一致', PROPOSED: '待验证', REFERENCE: '可参考',
  ADD: '加仓', REDUCE: '减仓', EXIT: '清仓', CONDITIONAL_ADD: '条件加仓', CONDITIONAL_REDUCE: '条件减仓',
}

export const systemCheckLabels: Record<string, string> = {
  database: '数据库', schema: '数据库结构', disk: '磁盘空间', storage: '存储空间',
  backup: '备份策略', scheduler: '调度器', worker_recovery: '后台任务恢复',
  governance: '参数治理', trading_calendar: '交易日历', market_provider: '行情数据源',
  quote_pipeline: '行情处理流程', market_refresh: '行情刷新', portfolio_snapshot: '组合快照',
  analysis_smoke: '分析基础验证', candidate_smoke: '候选标的基础验证',
  shadow_subsystem: '模拟交易子系统', future_quote_observation: '后续行情观察',
  real_broker_write_path: '真实券商交易权限', startup_preflight: '启动前检查', preflight: '启动前检查',
  realtime_monitor: '实时监控', shadow: '模拟交易',
}

export const historyDataLabels: Record<string, string> = {
  security_lifecycle: '证券生命周期', trading_status: '交易状态', st_classification: '风险警示分类',
  valuation: '估值', fundamentals: '财务基本面', etf_metadata: 'ETF 基础信息', price_basis: '价格口径',
}

const reasons: Record<string, string> = {
  quote_provider_not_observed: '尚未观察到真实行情数据源成功返回数据',
  market_snapshot_not_observed: '尚未生成真实市场行情快照',
  market_refresh_not_observed: '尚未观察到成功的真实行情刷新',
  confirmed_portfolio_snapshot_missing: '尚无已确认的持仓快照，请先导入并确认持仓',
  confirmed_portfolio_snapshot_stale: '已确认的持仓快照已过期，请更新持仓',
  successful_analysis_run_not_observed: '尚未完成一次成功的持仓分析',
  successful_candidate_run_not_observed: '尚未完成一次成功的候选标的分析',
  future_quote_observation_not_observed: '尚未记录可用于结果验证的后续行情',
  real_broker_order_path_not_exposed: '未开放真实券商下单接口',
  market_snapshot_stale: '市场行情快照已过期', provider_recovering: '行情数据源正在恢复',
  scheduler_disabled_by_config: '已按配置停用调度器', scheduler_not_running: '调度器未运行',
  monitor_disabled_by_config: '已按配置停用实时监控', monitor_not_running: '实时监控未运行',
  monitor_unavailable: '无法读取实时监控状态', non_trading_day: '当前为非交易日',
  calendar_unavailable: '交易日历不可用', shadow_schema_not_installed: '模拟交易数据库结构尚未安装',
  shadow_health_unavailable: '无法读取模拟交易健康状态',
  database_check_missing: '缺少数据库检查结果', schema_check_missing: '缺少数据库结构检查结果',
  disk_check_missing: '缺少磁盘检查结果', backup_check_missing: '缺少备份检查结果',
  scheduler_check_missing: '缺少调度器检查结果', worker_recovery_check_missing: '缺少后台任务恢复检查结果',
  governance_check_missing: '缺少参数治理检查结果', startup_preflight_not_completed: '启动前检查尚未完成',
  MIGRATION_HEAD_UNAVAILABLE: '无法读取数据库迁移版本', ALEMBIC_VERSION_MISSING: '缺少数据库迁移记录',
  DB_SCHEMA_BEHIND: '数据库结构需要升级', BLOCKED_SCHEMA_AHEAD: '数据库版本高于当前程序，已阻止运行',
  DB_REVISION_UNKNOWN: '无法识别数据库版本', STALE_DURABLE_JOBS: '存在过期的后台任务',
  DB_QUICK_CHECK_FAILED: '数据库完整性检查未通过', DB_NOT_WRITABLE: '数据库无法写入',
  LEGACY_PRE_GOVERNANCE: '旧版数据库尚未启用参数治理', EXPIRED_PENDING_INTENTS: '存在已过期的待执行模拟指令',
  FAILED_OUTCOME_EVALUATIONS: '存在失败的结果评估任务', NO_VERIFIED_BACKUP: '尚无通过校验的备份',
  BACKUP_DISABLED: '已停用自动备份', BACKUP_STALE: '最近备份已过期',
  check_failed: '检查未通过', degraded: '功能受限，请检查系统配置',
  BLOCKED: '检查受阻，请查看诊断详情', UNKNOWN: '检查结果未知',
}

export function systemStatusLabel(value?: string | null): string {
  if (!value) return '—'
  const normalized = value.trim().replaceAll('-', '_').toUpperCase()
  return statuses[normalized] || (/\p{Script=Han}/u.test(value) ? value : '待确认')
}

export function systemReasonLabel(value?: string | null): string {
  if (!value) return '检查通过'
  if (value.includes(';')) return value.split(';').map(systemReasonLabel).join('；')
  if (reasons[value]) return reasons[value]
  const [code, detail] = value.split(':', 2)
  if (code === 'market_snapshot_quality') return `市场快照质量：${systemStatusLabel(detail)}`
  const prefixes: Record<string, string> = {
    db_unavailable: '数据库不可用，请查看诊断详情', disk_unavailable: '磁盘状态不可用，请查看诊断详情',
    GOVERNANCE_UNAVAILABLE: '参数治理不可用，请查看诊断详情',
    MISSING_TABLES: '缺少必要的数据表，请升级数据库结构',
  }
  if (prefixes[code]) return prefixes[code]
  return /\p{Script=Han}/u.test(value) ? value : '检查详情待确认（悬停可查看诊断代码）'
}
