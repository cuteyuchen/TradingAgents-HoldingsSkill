export type DataGrade = 'A' | 'B' | 'C' | 'D' | 'F'
export type ModelPurpose = 'vision' | 'analysis' | 'deep_analysis'
// `quick` remains readable for legacy jobs/schedules; new requests use canonical modes.
export type AnalysisMode = 'quick' | 'fast' | 'standard' | 'deep'
export type WorkflowState = 'PRE_MARKET_MAINTENANCE' | 'PRE_MARKET_READY' | 'AUCTION' | 'MORNING_SESSION' | 'LUNCH_BREAK' | 'AFTERNOON_SESSION' | 'LATE_SESSION' | 'MARKET_CLOSED' | 'POST_CLOSE_ANALYSIS' | 'DAILY_REVIEW' | 'DAY_COMPLETE' | 'NON_TRADING_DAY'
export type HealthSeverity = 'OK' | 'DEGRADED' | 'BLOCKED' | 'UNKNOWN'
export type Freshness = 'FRESH' | 'STALE' | 'FROZEN' | 'MISSING'

export interface DashboardSection { status: string; [key: string]: any }
export interface DashboardTimelineItem { key: string; time: string; label: string; kind: string; mode?: string | null; status?: string; scheduled_at: string; is_current: boolean; [key: string]: any }
export interface DashboardTimeline { as_of: string; trade_date: string; workflow_state: WorkflowState; monitor: Record<string, any>; timeline: DashboardTimelineItem[] }
export interface DashboardHealth { status: HealthSeverity; overall: HealthSeverity; components: Array<{ name: string; status: string; mandatory?: boolean; detail?: any; [key: string]: any }>; severity_values: HealthSeverity[] }
export interface OperatingNotification {
  notification_id: string
  title: string
  summary: string
  severity: string
  portfolio_id: number
  event_type: string
  entity_type: string
  entity_id: string
  occurred_at: string
  deep_link: string
  dedupe_key: string
  status?: string
  read?: boolean
  read_at?: string | null
  [key: string]: any
}
export interface OperatingNotificationList {
  items: OperatingNotification[]
  count: number
  total_count: number
  unread_count: number
  critical_count: number
  latest_at?: string | null
}
export interface DashboardDiagnostics {
  as_of: string
  trade_date: string
  workflow_state: WorkflowState
  read_only: boolean
  no_lookahead: boolean
  health: DashboardHealth
  sections: Record<string, { status: string; error?: string }>
  issues: Array<{ component: string; status: string; detail?: any }>
}
export interface DailyDashboard {
  as_of: string
  trade_date: string
  market_open: boolean
  workflow_state: WorkflowState
  market: DashboardSection
  portfolio: DashboardSection
  candidates: DashboardSection
  triggers: DashboardSection
  analysis: DashboardSection
  decisions: DashboardSection & { final_action?: string }
  executions: DashboardSection
  memory: DashboardSection
  data_health: DashboardHealth | DashboardSection
  timeline: DashboardTimeline
  notifications: OperatingNotificationList & DashboardSection
}

export interface User {
  id: number
  email: string
  username?: string | null
  status: string
  timezone: string
  created_at: string
  last_login_at?: string | null
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface ModelProvider {
  id: number
  provider: string
  display_name: string
  base_url?: string | null
  enabled: boolean
  has_api_key: boolean
  api_key_masked?: string | null
  created_at: string
  updated_at: string
}

export interface ModelProfile {
  id: number
  provider_id: number
  purpose: ModelPurpose
  model_name: string
  parameters: Record<string, unknown>
  is_default: boolean
  last_health_status?: string | null
  last_health_at?: string | null
  created_at: string
  updated_at: string
}

export interface Portfolio {
  id: number
  name: string
  market: string
  currency: string
  is_default: boolean
  latest_snapshot_id?: number | null
  latest_snapshot_time?: string | null
  created_at: string
  updated_at: string
}

export interface Holding {
  code: string
  name?: string | null
  market?: string | null
  qty?: number | null
  available_qty?: number | null
  cost?: number | null
  price?: number | null
  market_value?: number | null
  pnl?: number | null
  pnl_amount?: number | null
  weight?: number | null
  extra?: Record<string, unknown>
}

export interface ParsedHoldings {
  holdings: Holding[]
  total_assets?: number | null
  total_market_value?: number | null
  broker_available_cash?: number | null
  corrected_unused_funds?: number | null
  repo_or_standard_bond_value?: number | null
  excluded_items: Record<string, unknown>[]
  notes: string[]
}

export interface HoldingUpload {
  id: number
  portfolio_id: number
  original_filename: string
  mime_type: string
  parsing_status: string
  parsed?: ParsedHoldings | null
  validation_errors: string[]
  error_message?: string | null
  screenshot_url: string
  confirmed_at?: string | null
  created_at: string
}

export interface PortfolioSnapshot extends ParsedHoldings {
  id: number
  portfolio_id: number
  upload_id?: number | null
  source: string
  snapshot_time: string
  status: string
}

export interface AnalysisJob {
  id: number
  portfolio_id: number
  snapshot_id: number
  trigger_type: string
  checkpoint?: string | null
  mode: AnalysisMode
  status: string
  progress_percent: number
  current_stage: string
  notify: boolean
  started_at?: string | null
  finished_at?: string | null
  error_code?: string | null
  error_message?: string | null
  retry_count: number
  run_id?: number | null
  created_at: string
}

export interface AnalysisRunSummary {
  id: number
  job_id: number
  portfolio_snapshot_id: number
  data_quality_grade?: DataGrade | null
  summary?: string | null
  final_rating?: string | null
  cash_target?: string | null
  confidence?: string | null
  created_at: string
}

export interface AnalysisRunDetail extends AnalysisRunSummary {
  structured_result: {
    result?: Record<string, any>
    market_snapshot?: Record<string, any>
    input_snapshot?: Record<string, any>
    history_used?: Record<string, any>[]
    [key: string]: any
  }
  markdown: string
}

export interface Schedule {
  id: number
  portfolio_id: number
  name: string
  timezone: string
  hour: number
  minute: number
  checkpoint: string
  mode: AnalysisMode
  enabled: boolean
  stale_snapshot_days: number
  notify: boolean
  max_consecutive_failures: number
  consecutive_failures: number
  last_run_at?: string | null
  next_run_at?: string | null
  created_at: string
  updated_at: string
}

export interface NotificationChannel {
  id: number
  type: 'dingtalk' | 'wecom'
  name: string
  enabled: boolean
  webhook_masked: string
  has_secret: boolean
  last_test_status?: string | null
  last_test_at?: string | null
  created_at: string
  updated_at: string
}

export type ResearchScope = 'MARKET' | 'CANDIDATE' | 'PORTFOLIO_DECISION' | 'MEMORY_DECISION' | 'BAR_FACTOR'
export type ReplayMode = 'PRODUCTION_REPLAY' | 'DETERMINISTIC_RECOMPUTE' | 'BAR_ONLY_DIAGNOSTIC'
export type CalibrationRecommendation = 'KEEP_CURRENT' | 'CONSIDER_CHANGE' | 'INSUFFICIENT_EVIDENCE' | 'REJECT_CHANGE'

export interface ReplayAvailabilityItem {
  name?: string
  status: string
  row_count?: number
  distinct_trade_dates?: number
  earliest_supported_at?: string | null
  latest_supported_at?: string | null
  coverage?: number | null
  reason?: string
  capabilities?: Record<string, string>
  [key: string]: any
}

export interface ReplayAvailabilityManifest {
  manifest_version: string
  generated_at: string
  requested_range: { start_date?: string | null; end_date?: string | null }
  data_hash: string
  known_limitations?: string[]
  [key: string]: ReplayAvailabilityItem | Record<string, any> | string | string[] | undefined
}

export interface BacktestMetricSlice {
  id: number
  metric_family: string
  security_type?: string | null
  market_regime?: string | null
  stage?: string | null
  score_bucket?: string | null
  horizon?: number | null
  parameter_variant?: string | null
  sample_count: number
  trade_date_count: number
  coverage?: number | null
  metrics?: Record<string, any> | null
  confidence_interval?: Record<string, any> | null
  quality_status: string
  limitations?: string[] | null
}

export interface BacktestRun {
  id: number
  user_id?: number | null
  portfolio_id?: number | null
  scope: ResearchScope
  replay_mode: ReplayMode
  start_date: string
  end_date: string
  status: string
  progress_percent: number
  current_stage: string
  config_version: string
  engine_version: string
  data_hash: string
  data_manifest?: Record<string, any> | null
  sample_count: number
  unique_trade_dates: number
  quality_status: string
  leakage_status: string
  result_summary?: Record<string, any> | null
  failure_counts?: Record<string, number> | null
  horizons?: number[] | null
  known_limitations?: string[] | null
  error_code?: string | null
  error_message?: string | null
  started_at?: string | null
  completed_at?: string | null
  created_at?: string | null
  lease_expires_at?: string | null
  last_heartbeat_at?: string | null
  attempt_count: number
  cancel_requested: boolean
  metric_slices: BacktestMetricSlice[]
}

export interface CalibrationReport {
  id: number
  backtest_run_id: number
  user_id?: number | null
  portfolio_id?: number | null
  status: string
  target_parameter: string
  current_value?: any
  challenger_value?: any
  recommendation: CalibrationRecommendation
  train?: Record<string, any> | null
  validation?: Record<string, any> | null
  test?: Record<string, any> | null
  robustness?: Record<string, any> | null
  sample_counts?: Record<string, any> | null
  risk_notes?: string[] | null
  proposal?: Record<string, any> | null
  report?: Record<string, any> | null
  calibration_version: string
  created_at?: string | null
  no_auto_apply: boolean
}

// Legacy archive types remain for migration/debug views.
export interface ScreenshotPayload {
  filename?: string | null
  mime_type?: string | null
  data_url?: string | null
  [key: string]: unknown
}

export interface ArchiveSummary {
  id: number
  timestamp: string
  checkpoint?: string | null
  holdings_source?: string | null
  data_quality_grade?: DataGrade | null
  title?: string | null
  holdings_count: number
  has_screenshot: boolean
}

export interface ArchiveDetail extends ArchiveSummary {
  meta?: Record<string, unknown> | null
  holdings: unknown
  advice_md: string
  screenshot?: ScreenshotPayload | null
}

export interface ArchiveContext {
  archives: any[]
  timeline_by_code: Record<string, any[]>
  latest_by_code: Record<string, any>
  same_day_advice: any[]
}
