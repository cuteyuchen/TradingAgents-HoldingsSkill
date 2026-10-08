/** Chinese presentation only; API status and readiness decisions remain intact. */
const statuses: Record<string, string> = {
  OK: '正常', READY: '已就绪', NOT_READY: '未就绪', READY_WITH_WARNINGS: '已就绪，有提醒',
  BLOCKED: '受阻', DEGRADED: '功能受限', CURRENT: '已更新', BEHIND: '待升级',
  AHEAD: '版本超前', BROKEN: '异常', UNKNOWN: '未知', ACTIVE: '启用中',
  PASS: '通过', WARNING: '需关注', FAILED: '失败', ERROR: '错误', FULL: '完整',
  PARTIAL: '部分完成', DATA_GAP: '数据缺失', LEAKAGE_BLOCKED: '时间信息泄漏，已阻止',
  PENDING: '待处理', RUNNING: '运行中', PAUSED: '已暂停', DISABLED: '已停用',
  ENABLED: '已启用', COMPLETED: '已完成', DONE: '已完成', QUEUED: '排队中',
  CANCELLED: '已取消', STOPPED: '已停止', VALID: '有效', MISSING: '缺失',
  STALE: '已过期', HEALTHY: '健康', RECOVERING: '恢复中', FIXTURE: '验收模拟数据',
  NOT_CONFIGURED: '未配置', NOT_INSTALLED: '未安装', UNAVAILABLE: '不可用',
  VERIFIED: '已校验', MANUAL: '手动', DAILY: '每日', WEEKLY: '每周', PRE_UPGRADE: '升级前',
  SCHEDULED: '自动计划', PRE_RESTORE_SAFETY: '恢复前保护备份', RESTORED: '已恢复', NOT_RECENTLY_CHECKED: '最近未检查',
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
  return statuses[value.toUpperCase()] || (/\p{Script=Han}/u.test(value) ? value : '待确认')
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
