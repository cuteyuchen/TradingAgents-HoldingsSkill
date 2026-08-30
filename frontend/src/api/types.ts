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
export type RecomputeCapabilityStatus =
  | 'FULL_PIT_EQUIVALENT'
  | 'PARTIAL_PIT_RECOMPUTE'
  | 'DIAGNOSTIC_ONLY'
  | 'DATA_GAP'
  | 'LEAKAGE_BLOCKED'
  | 'UNSUPPORTED'

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

export interface RecomputeCapabilityManifest {
  manifest_version: string
  scope: string
  requested_range: { start_date: string; end_date: string }
  checkpoint: string
  capability: RecomputeCapabilityStatus
  required_inputs: string[]
  available_inputs: string[]
  partial_inputs: string[]
  missing_inputs: string[]
  coverage: Record<string, number | null>
  parameter_version?: string | null
  config_hash?: string | null
  universe_version: string
  price_basis?: string | null
  engine_version: string
  limitations: string[]
  preview?: boolean
  parameter_snapshot_frozen_at_creation?: boolean
  [key: string]: any
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
  recompute_capability?: RecomputeCapabilityManifest | null
  recompute_summary?: Record<string, any> | null
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

export type GovernanceParameterClassification = 'CALIBRATABLE' | 'PROTECTED' | 'OPERATIONAL' | 'EXTERNAL' | 'DERIVED'
export type GovernanceParameterValue = string | number | boolean | Record<string, any> | null

export interface GovernanceParameter {
  display_name: string
  domain: string
  classification: GovernanceParameterClassification
  value_type: string
  min_value?: number | null
  max_value?: number | null
  allowed_values?: Array<string | number> | null
  calibration_supported: boolean
  requires_calibration_report: boolean
  protected: boolean
  restart_required: boolean
  runtime_contract_relevant: boolean
  description: string
  current_value?: GovernanceParameterValue
}

export interface ParameterSetVersion {
  id: number
  version: number
  status: string
  parent_version_id?: number | null
  created_by_user_id?: number | null
  approved_by_user_id?: number | null
  source_proposal_id?: number | null
  snapshot?: Record<string, any> | null
  diff?: Record<string, any> | null
  config_hash: string
  runtime_contract_version: string
  decision_contract_version: string
  validation?: Record<string, any> | null
  validation_status?: string | null
  created_at?: string | null
  approved_at?: string | null
  activated_at?: string | null
  deactivated_at?: string | null
  activation_reason?: string | null
  rollback_from_version_id?: number | null
  rollback_reason?: string | null
}

export interface ParameterChangeProposal {
  id: number
  user_id?: number | null
  source_type: string
  source_calibration_report_id?: number | null
  base_parameter_set_version_id?: number | null
  target_parameter: string
  current_value?: any
  proposed_value?: any
  proposed_snapshot?: Record<string, any> | null
  proposal_type: string
  status: string
  evidence?: Record<string, any> | null
  risk_summary?: Record<string, any> | null
  validation_summary?: Record<string, any> | null
  reason?: string | null
  risk_acknowledged: boolean
  created_at?: string | null
  submitted_at?: string | null
  reviewed_at?: string | null
  reviewed_by_user_id?: number | null
  review_comment?: string | null
  approved_version_id?: number | null
}

export interface ParameterGovernanceEvent {
  id: number
  actor_user_id?: number | null
  event_type: string
  proposal_id?: number | null
  parameter_set_version_id?: number | null
  occurred_at?: string | null
  metadata?: Record<string, any> | null
}

export interface GovernanceRegistryResponse {
  registry: Record<string, GovernanceParameter>
  active_version_id?: number | null
  active_version?: number | null
}

export interface ParameterSetListResponse {
  versions: ParameterSetVersion[]
}

export interface ProposalListResponse {
  proposals: ParameterChangeProposal[]
}

export interface GovernanceEventListResponse {
  events: ParameterGovernanceEvent[]
}

export interface GovernanceHealth {
  status: 'OK' | 'DEGRADED' | 'BLOCKED'
  reasons: string[]
  active: ParameterSetVersion | null
}

export interface SystemRelease {
  app_version: string
  git_sha: string
  git_ref?: string | null
  build_time?: string | null
  alembic_db_revision?: string | null
  alembic_code_head_revision?: string | null
  schema_state: string
  schema_reason?: string | null
  schema_blocked: boolean
  runtime_contract_version: string
  decision_contract_version: string
  active_parameter_set_version?: string | null
  active_parameter_set_hash?: string | null
  governance_status?: string | null
  python_version: string
  environment: string
  database_backend: string
  database_identity?: string | null
  started_at: string
  uptime_seconds: number
}

export interface SystemHealthCheck {
  status: 'OK' | 'DEGRADED' | 'BLOCKED' | 'UNKNOWN'
  reason?: string | null
  [key: string]: any
}

export interface SystemHealth {
  status: 'OK' | 'DEGRADED' | 'BLOCKED' | 'UNKNOWN'
  components: Record<string, SystemHealthCheck>
  as_of: string
}

export interface SystemReadiness {
  status: 'READY' | 'READY_WITH_WARNINGS' | 'BLOCKED'
  ready: boolean
  checks: Record<string, SystemHealthCheck>
}

export interface SystemBackup {
  backup_id: string
  filename: string
  type: string
  reason: string
  created_at: string
  completed_at: string
  source_db_revision?: string | null
  code_head_revision?: string | null
  app_version: string
  git_sha?: string | null
  source_db_size: number
  backup_size: number
  sha256: string
  quick_check_result: string
  source_db_fingerprint: string
  [key: string]: any
}

export interface SystemBackupList {
  backups: SystemBackup[]
}

export interface SystemDiagnostics {
  bundle_id: string
  filename: string
  sha256: string
  size: number
  entries: string[]
  contains_db: boolean
  contains_backup: boolean
}

export interface SystemRecoveryReport {
  counts: Record<string, number>
  errors: string[]
}

export interface HistoryCoverageItem {
  data_type: string
  semantics: 'DAILY' | 'EVENT' | 'PUBLICATION'
  status: 'FULL' | 'PARTIAL' | 'DATA_GAP' | 'UNSUPPORTED' | 'LEAKAGE_BLOCKED'
  reason?: string | null
  row_count: number
  expected_dates?: number | null
  known_dates?: string[] | null
  coverage?: number | null
  earliest_supported_at?: string | null
  latest_supported_at?: string | null
  sources?: string[]
  last_sync?: {
    run_id: number
    status: string
    completed_at?: string | null
    inserted_count: number
    updated_count: number
    skipped_count: number
    failed_count: number
    provider?: string | null
    source?: string | null
  } | null
  [key: string]: any
}

export interface HistoryCoverage {
  generated_at: string
  requested_range: { start_date?: string | null; end_date?: string | null }
  market: string
  items: HistoryCoverageItem[]
}

export interface HistorySyncRun {
  id: number
  data_type: string
  market: string
  start_date?: string | null
  end_date?: string | null
  status: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'UNSUPPORTED' | 'INSUFFICIENT_DATA'
  progress_percent: number
  fetched_count: number
  inserted_count: number
  updated_count: number
  skipped_count: number
  failed_count: number
  provider?: string | null
  source?: string | null
  started_at?: string | null
  completed_at?: string | null
  attempt_count: number
  error_summary?: string | null
  created_at: string
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

export interface ShadowPosition {
  id: number
  code: string
  name?: string | null
  security_type?: string | null
  etf_category?: string | null
  quantity: number
  sellable_quantity: number
  average_cost: number
  current_mark?: number | null
  market_value?: number | null
  unrealized_pnl?: number | null
  last_mark_at?: string | null
  acquired_decision_ids?: number[]
  metadata?: Record<string, unknown>
}

export interface ShadowAccount {
  id: number
  user_id: number
  source_portfolio_id: number
  name: string
  status: string
  mode: string
  base_currency: string
  paper_only: true
  initialized_from_snapshot_id?: number | null
  initialized_at: string
  starting_cash: number
  current_cash: number
  reserved_cash: number
  shadow_generation: number
  execution_contract_version: string
  expires_policy: string
  version: number
  created_at: string
  paused_at?: string | null
  closed_at?: string | null
  pending_intent_count: number
  shadow_state?: { cash: number; ledger_entry_count: number; as_of?: string | null }
  positions: ShadowPosition[]
}

export interface ShadowDecision {
  id: number
  portfolio_id: number
  trade_date: string
  decision_kind: string
  decision_checkpoint?: string | null
  trigger_type?: string | null
  trigger_event_id?: number | null
  trigger_priority?: string | null
  source_analysis_job_id?: number | null
  source_analysis_run_id?: number | null
  portfolio_snapshot_id?: number | null
  parameter_set_version?: string | null
  parameter_set_hash?: string | null
  runtime_contract_version: string
  decision_contract_version: string
  final_action: string
  market_regime?: string | null
  market_score?: number | null
  market_quality?: string | null
  portfolio_quality?: string | null
  confidence: number
  data_coverage?: number | null
  decision_finalized_at: string
  captured_at: string
  quality_status: string
  live_evidence_eligibility: string
  observation_hash: string
  calculation_key: string
  [key: string]: any
}

export interface ShadowOutcome {
  id: number
  decision_observation_id: number
  shadow_account_id?: number | null
  shadow_generation?: number | null
  target_type: string
  target_key: string
  recommended_action: string
  horizon_trading_days: number
  reference_trade_date?: string | null
  reference_at?: string | null
  reference_price?: number | null
  reference_price_basis?: string | null
  target_trade_date?: string | null
  target_price?: number | null
  forward_return?: number | null
  benchmark_return?: number | null
  excess_return?: number | null
  mfe?: number | null
  mae?: number | null
  drawdown?: number | null
  direction?: string | null
  execution_eligible?: boolean | null
  shadow_filled?: boolean | null
  fill_delay_seconds?: number | null
  fill_drift?: number | null
  candidate_opportunity_cost?: number | null
  drawdown_avoided?: number | null
  risk_off_correct?: boolean | null
  status: string
  quality_status: string
  live_evidence_eligibility: string
  next_due_date?: string | null
  computed_at?: string | null
}

export interface ShadowDecisionDetail extends ShadowDecision {
  reason_codes?: string[]
  selected_actions?: Array<Record<string, unknown>>
  selected_candidate_ids?: number[]
  source_lineage?: Record<string, unknown>
  created_at?: string | null
  execution?: { intents: ShadowOrder[]; fills: ShadowFill[] }
  outcomes?: ShadowOutcome[]
  actual_alignment?: Array<{
    id: number
    code: string
    side: string
    status: string
    actual_trade_ledger_id?: number | null
    matched_at?: string | null
    time_delta_seconds?: number | null
    quantity_ratio?: number | null
    window_start?: string | null
    window_end?: string | null
    source_refs?: Record<string, unknown>
  }>
}

export interface ShadowOrder {
  id: number
  shadow_account_id: number
  shadow_generation: number
  decision_observation_id: number
  action_index: number
  code: string
  security_type?: string | null
  side: string
  target_qty?: number | null
  target_notional?: number | null
  target_weight?: number | null
  decision_reference_price?: number | null
  decision_reference_basis?: string | null
  decision_finalized_at: string
  earliest_executable_at: string
  status: string
  reason_codes: string[]
  created_at: string
  expires_at: string
  idempotency_key: string
}

export interface ShadowFill {
  id: number
  order_intent_id: number
  shadow_account_id: number
  shadow_generation: number
  code: string
  side: string
  quantity: number
  price: number
  gross_amount: number
  commission: number
  tax: number
  total_cost: number
  price_basis: string
  quote_observation_id: number
  quote_captured_at: string
  fill_at: string
  fill_quality: string
  execution_delay_seconds?: number | null
  execution_delay_price_drift?: number | null
  slippage_not_modeled: true
  [key: string]: any
}

export interface ShadowDailySnapshot {
  id: number
  account_id: number
  shadow_generation: number
  trade_date: string
  cash: number
  market_value: number
  total_equity: number
  daily_return?: number | null
  cumulative_return?: number | null
  drawdown?: number | null
  turnover: number
  position_count: number
  action_count: number
  no_action_count: number
  benchmark_return?: number | null
  excess_return?: number | null
  market_regime?: string | null
  price_basis?: string | null
  price_basis_compatible: boolean
  [key: string]: any
}

export interface ShadowPerformance {
  account_id: number
  shadow_generation: number
  status: string
  paper_only: true
  execution_contract_version: string
  current_cash: number
  reserved_cash: number
  current_equity?: number | null
  cumulative_return?: number | null
  benchmark_return?: number | null
  excess_return?: number | null
  performance_quality?: string | null
  max_drawdown?: number | null
  turnover: number
  transaction_cost: number
  position_count: number
  action_count: number
  no_action_count: number
  decision_count: number
  fill_count: number
  sample_days: number
  snapshots: ShadowDailySnapshot[]
}

export interface ShadowValidation {
  user_id: number
  portfolio_id?: number | null
  live_sample_days: number
  decision_count: number
  cohorts: Array<{
    cohort: Record<string, unknown>
    decision_count: number
    action_count: number
    no_action_count: number
    blocked_count: number
    sample_days: number
    completed_outcome_count: number
    outcomes_by_target_horizon: Array<{
      target_type: string
      target_key: string
      horizon_trading_days: number
      completed_outcome_count: number
      mean_excess_return?: number | null
    }>
    evidence_status: string
  }>
  historical_backtest_included: false
  limitations: string[]
}
