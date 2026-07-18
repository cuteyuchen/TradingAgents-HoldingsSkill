export type DataGrade = 'A' | 'B' | 'C' | 'D' | 'F'
export type ModelPurpose = 'vision' | 'analysis' | 'deep_analysis'
export type AnalysisMode = 'quick' | 'deep'

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
