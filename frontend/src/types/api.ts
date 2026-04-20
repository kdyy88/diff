export type DiffKind = 'insert' | 'delete' | 'replace' | 'reflow';

export interface HighlightFragment {
  page: number;
  bbox: [number, number, number, number];
  viewport_ref: string;
}

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
  excerpt_left: string;
  excerpt_right: string;
  left_fragments: HighlightFragment[];
  right_fragments: HighlightFragment[];
  left_range: CharRange | null;
  right_range: CharRange | null;
  raw_event_count: number;
  group_key: string | null;
  table_context: TableContext | null;
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

export interface DiffResult {
  pages_left: PageMeta[];
  pages_right: PageMeta[];
  summary: DiffSummary;
  anchors: DiffAnchor[];
}

export type JobState =
  | 'uploaded'
  | 'extracting'
  | 'aligning'
  | 'projecting'
  | 'done'
  | 'failed';

export interface JobStatus {
  id: string;
  status: JobState;
  stage: string;
  progress: number;
  error: string | null;
  summary: DiffSummary | null;
}

export interface CreateJobResponse {
  id: string;
  status: JobState;
}
