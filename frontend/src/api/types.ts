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
