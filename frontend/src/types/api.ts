export type DocumentKind = 'pdf' | 'docx';
export type HighlightKind = 'pdf' | 'word';
export type DiffKind = 'insert' | 'delete' | 'replace' | 'reflow';
export type ConfidenceLevel = 'high' | 'low';
export type ChapterConfidenceLevel = 'high' | 'medium' | 'low';
export type ChapterSource = 'bookmark' | 'heading' | 'manual' | 'synthetic';
export type ChapterValidationSeverity = 'error' | 'warning';
export type SectionStatus = 'equal' | 'inserted' | 'deleted' | 'container';
export type JobState = 'uploaded' | 'extracting' | 'aligning' | 'projecting' | 'done' | 'failed';
export type ChapterAnalysisState = 'uploaded' | 'analyzing' | 'fallback' | 'done' | 'failed';
export type AnalysisSide = 'source' | 'modified';

export interface FeatureFlags {
  chapterSplit: boolean;
}

export interface PdfHighlightFragment {
  kind: 'pdf';
  page: number;
  bbox: [number, number, number, number];
  viewport_ref: string;
}

export interface WordHighlightFragment {
  kind: 'word';
  dom_id: string;
  char_start: number;
  char_end: number;
}

export type HighlightFragment = PdfHighlightFragment | WordHighlightFragment;

export interface CharRange {
  start: number;
  end: number;
}

export interface TableContext {
  table_id: string;
  row: number;
  col: number;
  row_label: string | null;
  col_label: string | null;
}

export interface DiffAnchor {
  id: string;
  kind: DiffKind;
  source_type: 'text' | 'table';
  confidence: ConfidenceLevel;
  excerpt_left: string;
  excerpt_right: string;
  left_fragments: HighlightFragment[];
  right_fragments: HighlightFragment[];
  left_range: CharRange | null;
  right_range: CharRange | null;
  raw_event_count: number;
  group_key: string | null;
  table_context: TableContext | null;
  chapter_id: string | null;
  chapter_title: string | null;
  chapter_index: number | null;
  chapter_level: number | null;
  chapter_path: string[];
  is_large_region: boolean;
}

export interface DiffSummary {
  insertions: number;
  deletions: number;
  replacements: number;
  reflows: number;
  pages_a: number;
  pages_b: number;
}

export interface PageMeta {
  page: number;
  width: number;
  height: number;
}

export interface ChapterDiffSummary {
  id: string;
  title: string;
  index: number;
  status: SectionStatus;
  level: number;
  parent_id: string | null;
  path: string[];
  anchor_count: number;
  first_anchor_id: string | null;
  summary: DiffSummary;
}

export interface DiffResult {
  document_kind: DocumentKind;
  pages_left: PageMeta[];
  pages_right: PageMeta[];
  summary: DiffSummary;
  anchors: DiffAnchor[];
  chapters: ChapterDiffSummary[];
}

export interface JobStatus {
  id: string;
  document_kind: DocumentKind;
  status: JobState;
  stage: string;
  progress: number;
  error: string | null;
  summary: DiffSummary | null;
}

export interface CreateJobResponse {
  id: string;
  document_kind: DocumentKind;
  status: JobState;
}

export interface ChapterDraft {
  id: string;
  title: string;
  normalized_title: string;
  normalized_path: string[];
  start_page: number;
  end_page: number;
  start_y: number | null;
  end_y: number | null;
  level: number;
  parent_id: string | null;
  path: string[];
  source: ChapterSource;
  confidence: ChapterConfidenceLevel;
}

export interface DocumentChapterPlan {
  side: AnalysisSide;
  total_pages: number;
  chapters: ChapterDraft[];
}

export interface ChapterAnalysisStatus {
  id: string;
  document_kind: DocumentKind;
  status: ChapterAnalysisState;
  stage: string;
  progress: number;
  error: string | null;
}

export interface ChapterAnalysisResult {
  id: string;
  document_kind: DocumentKind;
  status: ChapterAnalysisState;
  source_plan: DocumentChapterPlan;
  modified_plan: DocumentChapterPlan;
}

export interface ChapterValidationIssue {
  code:
    | 'invalid_page'
    | 'non_increasing_start'
    | 'coverage_gap'
    | 'duplicate_normalized_title'
    | 'unmatched_chapter';
  severity: ChapterValidationSeverity;
  side: AnalysisSide;
  chapter_id: string;
  message: string;
  raw_title: string;
  normalized_title: string;
  peer_chapter_id: string | null;
  peer_raw_title: string | null;
  peer_normalized_title: string | null;
  suggested_peer_score: number | null;
}

export interface ChapterValidationItem {
  id: string;
  title: string;
  start_page: number;
  start_y?: number | null;
  level?: number | null;
  parent_id?: string | null;
  path?: string[];
}

export interface ChapterValidationRequest {
  source_chapters: ChapterValidationItem[];
  modified_chapters: ChapterValidationItem[];
}

export interface ChapterValidationResult {
  can_continue: boolean;
  issues: ChapterValidationIssue[];
  source_plan: DocumentChapterPlan;
  modified_plan: DocumentChapterPlan;
}
