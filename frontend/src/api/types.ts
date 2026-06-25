// Type definitions for the archive-only backend API.

export type DataGrade = 'A' | 'B' | 'C' | 'D' | 'F'

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

export interface ArchiveDetail {
  id: number
  timestamp: string
  checkpoint?: string | null
  holdings_source?: string | null
  data_quality_grade?: DataGrade | null
  title?: string | null
  meta?: Record<string, unknown> | null
  holdings: unknown
  advice_md: string
  screenshot?: ScreenshotPayload | null
}

export interface ArchiveContextHolding {
  code: string
  name?: string | null
  qty?: number | string | null
  available_qty?: number | string | null
  unavailable_qty?: number | string | null
  cost?: number | string | null
  price?: number | string | null
  market_value?: number | string | null
  pnl?: number | string | null
  pnl_amount?: number | string | null
  data_quality?: DataGrade | string | null
  availability_note?: string | null
}

export interface ArchiveContextArchive extends ArchiveSummary {
  holdings: ArchiveContextHolding[]
  advice_excerpt: string
  advice_md?: string
}

export interface ArchiveContextTimelineItem extends ArchiveContextHolding {
  archive_id: number
  timestamp: string
  checkpoint?: string | null
  data_quality_grade?: DataGrade | null
  title?: string | null
  advice_excerpt: string
}

export interface ArchiveContextAdvice {
  archive_id: number
  timestamp: string
  checkpoint?: string | null
  data_quality_grade?: DataGrade | null
  title?: string | null
  advice_excerpt: string
}

export interface ArchiveContext {
  archives: ArchiveContextArchive[]
  timeline_by_code: Record<string, ArchiveContextTimelineItem[]>
  latest_by_code: Record<string, ArchiveContextTimelineItem>
  same_day_advice: ArchiveContextAdvice[]
}
