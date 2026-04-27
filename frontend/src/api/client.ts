import type {
  ChapterAnalysisResult,
  ChapterAnalysisStatus,
  ChapterAnalysisState,
  ChapterConfidenceLevel,
  ChapterSource,
  ChapterValidationRequest,
  ChapterValidationResult,
  CreateJobResponse,
  DiffKind,
  DiffResult,
  FeatureFlags,
  JobStatus,
  JobState,
} from '../types/api';

const configuredApiBase = import.meta.env.VITE_API_BASE?.trim();
const API_BASE = (configuredApiBase && configuredApiBase.length > 0 ? configuredApiBase : '/api').replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  detail: string;
  payload: unknown;

  constructor(status: number, detail: string, payload: unknown = null) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.payload = payload;
  }
}

export interface CreateJobInput {
  sourceFile: File;
  modifiedFile: File;
  headerMargin: number;
  footerMargin: number;
  showReflow: boolean;
}

const DOCUMENT_KINDS = ['pdf', 'docx'] as const;
const JOB_STATES = ['uploaded', 'extracting', 'aligning', 'projecting', 'done', 'failed'] as const;
const CHAPTER_ANALYSIS_STATES = ['uploaded', 'analyzing', 'fallback', 'done', 'failed'] as const;
const DIFF_KINDS = ['insert', 'delete', 'replace', 'reflow'] as const;
const CHAPTER_SOURCES = ['bookmark', 'heading', 'manual', 'synthetic'] as const;
const CHAPTER_CONFIDENCE_LEVELS = ['high', 'medium', 'low'] as const;

type JsonRecord = Record<string, unknown>;

async function readApiError(response: Response, fallbackMessage: string): Promise<ApiError> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const detail = typeof payload.detail === 'string' ? payload.detail : fallbackMessage;
    return new ApiError(response.status, detail, payload.detail ?? payload);
  } catch {
    return new ApiError(response.status, fallbackMessage, null);
  }
}

function expectRecord(value: unknown, context: string): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${context} must be an object.`);
  }
  return value as JsonRecord;
}

function expectString(value: unknown, context: string): string {
  if (typeof value !== 'string') {
    throw new TypeError(`${context} must be a string.`);
  }
  return value;
}

function expectNumber(value: unknown, context: string): number {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    throw new TypeError(`${context} must be a number.`);
  }
  return value;
}

function expectBoolean(value: unknown, context: string): boolean {
  if (typeof value !== 'boolean') {
    throw new TypeError(`${context} must be a boolean.`);
  }
  return value;
}

function expectNullableString(value: unknown, context: string): string | null {
  if (value === null) {
    return null;
  }
  return expectString(value, context);
}

function expectNullableNumber(value: unknown, context: string): number | null {
  if (value === null) {
    return null;
  }
  return expectNumber(value, context);
}

function expectArray(value: unknown, context: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new TypeError(`${context} must be an array.`);
  }
  return value;
}

function expectStringArray(value: unknown, context: string): string[] {
  return expectArray(value, context).map((item, index) => expectString(item, `${context}[${index}]`));
}

function optionalNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && !Number.isNaN(value) ? value : fallback;
}

function optionalNullableNumber(value: unknown): number | null {
  return value === null || value === undefined ? null : expectNumber(value, 'optional number');
}

function optionalNullableString(value: unknown): string | null {
  return value === null || value === undefined ? null : expectString(value, 'optional string');
}

function optionalStringArray(value: unknown): string[] {
  return value === undefined ? [] : expectStringArray(value, 'optional string array');
}

function optionalBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function expectOneOf<T extends readonly string[]>(value: unknown, allowed: T, context: string): T[number] {
  const nextValue = expectString(value, context);
  if (!allowed.includes(nextValue)) {
    throw new TypeError(`${context} must be one of ${allowed.join(', ')}.`);
  }
  return nextValue as T[number];
}

function parseFeatureFlags(value: unknown): FeatureFlags {
  const record = expectRecord(value, 'Feature flags response');
  return {
    chapterSplit: expectBoolean(record.chapterSplit, 'Feature flags response.chapterSplit'),
  };
}

function parseCreateJobResponse(value: unknown): CreateJobResponse {
  const record = expectRecord(value, 'Create job response');
  return {
    id: expectString(record.id, 'Create job response.id'),
    document_kind: expectOneOf(record.document_kind, DOCUMENT_KINDS, 'Create job response.document_kind'),
    status: expectOneOf(record.status, JOB_STATES, 'Create job response.status') as JobState,
  };
}

function parseDiffSummary(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    insertions: expectNumber(record.insertions, `${context}.insertions`),
    deletions: expectNumber(record.deletions, `${context}.deletions`),
    replacements: expectNumber(record.replacements, `${context}.replacements`),
    reflows: expectNumber(record.reflows, `${context}.reflows`),
    pages_a: expectNumber(record.pages_a, `${context}.pages_a`),
    pages_b: expectNumber(record.pages_b, `${context}.pages_b`),
  };
}

function parsePageMeta(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    page: expectNumber(record.page, `${context}.page`),
    width: expectNumber(record.width, `${context}.width`),
    height: expectNumber(record.height, `${context}.height`),
  };
}

function parseHighlightFragment(value: unknown, context: string) {
  const record = expectRecord(value, context);
  const kind = expectOneOf(record.kind, ['pdf', 'word'] as const, `${context}.kind`);
  if (kind === 'word') {
    return {
      kind,
      dom_id: expectString(record.dom_id, `${context}.dom_id`),
      char_start: expectNumber(record.char_start, `${context}.char_start`),
      char_end: expectNumber(record.char_end, `${context}.char_end`),
    };
  }

  const bbox = expectArray(record.bbox, `${context}.bbox`).map((item, index) => expectNumber(item, `${context}.bbox[${index}]`));
  if (bbox.length !== 4) {
    throw new TypeError(`${context}.bbox must contain exactly 4 numbers.`);
  }
  return {
    kind,
    page: expectNumber(record.page, `${context}.page`),
    bbox: bbox as [number, number, number, number],
    viewport_ref: expectString(record.viewport_ref, `${context}.viewport_ref`),
  };
}

function parseCharRange(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    start: expectNumber(record.start, `${context}.start`),
    end: expectNumber(record.end, `${context}.end`),
  };
}

function parseTableContext(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    table_id: expectString(record.table_id, `${context}.table_id`),
    row: expectNumber(record.row, `${context}.row`),
    col: expectNumber(record.col, `${context}.col`),
    row_label: expectNullableString(record.row_label, `${context}.row_label`),
    col_label: expectNullableString(record.col_label, `${context}.col_label`),
  };
}

function parseDiffAnchor(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    id: expectString(record.id, `${context}.id`),
    kind: expectOneOf(record.kind, DIFF_KINDS, `${context}.kind`) as DiffKind,
    source_type: expectOneOf(record.source_type, ['text', 'table'] as const, `${context}.source_type`),
    confidence: expectOneOf(record.confidence, ['high', 'low'] as const, `${context}.confidence`),
    excerpt_left: expectString(record.excerpt_left, `${context}.excerpt_left`),
    excerpt_right: expectString(record.excerpt_right, `${context}.excerpt_right`),
    left_fragments: expectArray(record.left_fragments, `${context}.left_fragments`).map((item, index) =>
      parseHighlightFragment(item, `${context}.left_fragments[${index}]`),
    ),
    right_fragments: expectArray(record.right_fragments, `${context}.right_fragments`).map((item, index) =>
      parseHighlightFragment(item, `${context}.right_fragments[${index}]`),
    ),
    left_range: record.left_range === null ? null : parseCharRange(record.left_range, `${context}.left_range`),
    right_range: record.right_range === null ? null : parseCharRange(record.right_range, `${context}.right_range`),
    raw_event_count: expectNumber(record.raw_event_count, `${context}.raw_event_count`),
    group_key: expectNullableString(record.group_key, `${context}.group_key`),
    table_context: record.table_context === null ? null : parseTableContext(record.table_context, `${context}.table_context`),
    chapter_id: expectNullableString(record.chapter_id, `${context}.chapter_id`),
    chapter_title: expectNullableString(record.chapter_title, `${context}.chapter_title`),
    chapter_index: expectNullableNumber(record.chapter_index, `${context}.chapter_index`),
    chapter_level: optionalNullableNumber(record.chapter_level),
    chapter_path: optionalStringArray(record.chapter_path),
    is_large_region: optionalBoolean(record.is_large_region, false),
  };
}

function parseChapterDiffSummary(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    id: expectString(record.id, `${context}.id`),
    title: expectString(record.title, `${context}.title`),
    index: expectNumber(record.index, `${context}.index`),
    level: optionalNumber(record.level, 1),
    parent_id: optionalNullableString(record.parent_id),
    path: optionalStringArray(record.path),
    anchor_count: expectNumber(record.anchor_count, `${context}.anchor_count`),
    first_anchor_id: expectNullableString(record.first_anchor_id, `${context}.first_anchor_id`),
    summary: parseDiffSummary(record.summary, `${context}.summary`),
  };
}

function parseDiffResult(value: unknown): DiffResult {
  const record = expectRecord(value, 'Diff result');
  return {
    document_kind: expectOneOf(record.document_kind, DOCUMENT_KINDS, 'Diff result.document_kind'),
    pages_left: expectArray(record.pages_left, 'Diff result.pages_left').map((item, index) => parsePageMeta(item, `Diff result.pages_left[${index}]`)),
    pages_right: expectArray(record.pages_right, 'Diff result.pages_right').map((item, index) => parsePageMeta(item, `Diff result.pages_right[${index}]`)),
    summary: parseDiffSummary(record.summary, 'Diff result.summary'),
    anchors: expectArray(record.anchors, 'Diff result.anchors').map((item, index) => parseDiffAnchor(item, `Diff result.anchors[${index}]`)),
    chapters: expectArray(record.chapters, 'Diff result.chapters').map((item, index) =>
      parseChapterDiffSummary(item, `Diff result.chapters[${index}]`),
    ),
  };
}

function parseJobStatus(value: unknown): JobStatus {
  const record = expectRecord(value, 'Job status response');
  return {
    id: expectString(record.id, 'Job status response.id'),
    document_kind: expectOneOf(record.document_kind, DOCUMENT_KINDS, 'Job status response.document_kind'),
    status: expectOneOf(record.status, JOB_STATES, 'Job status response.status') as JobState,
    stage: expectString(record.stage, 'Job status response.stage'),
    progress: expectNumber(record.progress, 'Job status response.progress'),
    error: expectNullableString(record.error, 'Job status response.error'),
    summary: record.summary === null ? null : parseDiffSummary(record.summary, 'Job status response.summary'),
  };
}

function parseChapterDraft(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    id: expectString(record.id, `${context}.id`),
    title: expectString(record.title, `${context}.title`),
    normalized_title: expectString(record.normalized_title, `${context}.normalized_title`),
    normalized_path: optionalStringArray(record.normalized_path),
    start_page: expectNumber(record.start_page, `${context}.start_page`),
    end_page: expectNumber(record.end_page, `${context}.end_page`),
    start_y: optionalNullableNumber(record.start_y),
    end_y: optionalNullableNumber(record.end_y),
    level: optionalNumber(record.level, 1),
    parent_id: optionalNullableString(record.parent_id),
    path: optionalStringArray(record.path),
    source: expectOneOf(record.source, CHAPTER_SOURCES, `${context}.source`) as ChapterSource,
    confidence: expectOneOf(record.confidence, CHAPTER_CONFIDENCE_LEVELS, `${context}.confidence`) as ChapterConfidenceLevel,
  };
}

function parseDocumentChapterPlan(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    side: expectOneOf(record.side, ['source', 'modified'] as const, `${context}.side`),
    total_pages: expectNumber(record.total_pages, `${context}.total_pages`),
    chapters: expectArray(record.chapters, `${context}.chapters`).map((item, index) => parseChapterDraft(item, `${context}.chapters[${index}]`)),
  };
}

function parseChapterAnalysisStatus(value: unknown): ChapterAnalysisStatus {
  const record = expectRecord(value, 'Chapter analysis status response');
  return {
    id: expectString(record.id, 'Chapter analysis status response.id'),
    document_kind: expectOneOf(record.document_kind, DOCUMENT_KINDS, 'Chapter analysis status response.document_kind'),
    status: expectOneOf(record.status, CHAPTER_ANALYSIS_STATES, 'Chapter analysis status response.status') as ChapterAnalysisState,
    stage: expectString(record.stage, 'Chapter analysis status response.stage'),
    progress: expectNumber(record.progress, 'Chapter analysis status response.progress'),
    error: expectNullableString(record.error, 'Chapter analysis status response.error'),
  };
}

function parseChapterAnalysisResult(value: unknown): ChapterAnalysisResult {
  const record = expectRecord(value, 'Chapter analysis result');
  return {
    id: expectString(record.id, 'Chapter analysis result.id'),
    document_kind: expectOneOf(record.document_kind, DOCUMENT_KINDS, 'Chapter analysis result.document_kind'),
    status: expectOneOf(record.status, CHAPTER_ANALYSIS_STATES, 'Chapter analysis result.status') as ChapterAnalysisState,
    source_plan: parseDocumentChapterPlan(record.source_plan, 'Chapter analysis result.source_plan'),
    modified_plan: parseDocumentChapterPlan(record.modified_plan, 'Chapter analysis result.modified_plan'),
  };
}

function parseChapterValidationIssue(value: unknown, context: string) {
  const record = expectRecord(value, context);
  return {
    code: expectOneOf(
      record.code,
      ['invalid_page', 'non_increasing_start', 'coverage_gap', 'duplicate_normalized_title', 'unmatched_chapter'] as const,
      `${context}.code`,
    ),
    side: expectOneOf(record.side, ['source', 'modified'] as const, `${context}.side`),
    chapter_id: expectString(record.chapter_id, `${context}.chapter_id`),
    message: expectString(record.message, `${context}.message`),
    raw_title: expectString(record.raw_title, `${context}.raw_title`),
    normalized_title: expectString(record.normalized_title, `${context}.normalized_title`),
    peer_chapter_id: expectNullableString(record.peer_chapter_id, `${context}.peer_chapter_id`),
    peer_raw_title: expectNullableString(record.peer_raw_title, `${context}.peer_raw_title`),
    peer_normalized_title: expectNullableString(record.peer_normalized_title, `${context}.peer_normalized_title`),
    suggested_peer_score: expectNullableNumber(record.suggested_peer_score, `${context}.suggested_peer_score`),
  };
}

function parseChapterValidationResult(value: unknown): ChapterValidationResult {
  const record = expectRecord(value, 'Chapter validation result');
  return {
    can_continue: expectBoolean(record.can_continue, 'Chapter validation result.can_continue'),
    issues: expectArray(record.issues, 'Chapter validation result.issues').map((item, index) =>
      parseChapterValidationIssue(item, `Chapter validation result.issues[${index}]`),
    ),
    source_plan: parseDocumentChapterPlan(record.source_plan, 'Chapter validation result.source_plan'),
    modified_plan: parseDocumentChapterPlan(record.modified_plan, 'Chapter validation result.modified_plan'),
  };
}

async function readApiJson<T>(response: Response, fallbackMessage: string, parser: (payload: unknown) => T): Promise<T> {
  if (!response.ok) {
    throw await readApiError(response, fallbackMessage);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, `${fallbackMessage} Response was not valid JSON.`);
  }

  try {
    return parser(payload);
  } catch (error) {
    const detail = error instanceof Error ? error.message : 'Unexpected response shape.';
    throw new ApiError(response.status, `${fallbackMessage} ${detail}`, payload);
  }
}

function buildFormData(input: CreateJobInput): FormData {
  const formData = new FormData();
  formData.append('sourceFile', input.sourceFile);
  formData.append('modifiedFile', input.modifiedFile);
  formData.append('headerMargin', String(input.headerMargin));
  formData.append('footerMargin', String(input.footerMargin));
  formData.append('showReflow', String(input.showReflow));
  return formData;
}

export async function getFeatures(): Promise<FeatureFlags> {
  const response = await fetch(`${API_BASE}/features`);
  return readApiJson(response, 'Failed to fetch feature flags.', parseFeatureFlags);
}

export async function createJob(input: CreateJobInput): Promise<CreateJobResponse> {
  const response = await fetch(`${API_BASE}/jobs`, {
    method: 'POST',
    body: buildFormData(input),
  });

  return readApiJson(response, 'Failed to create diff job.', parseCreateJobResponse);
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}`);
  return readApiJson(response, 'Failed to fetch job status.', parseJobStatus);
}

export async function getJobResult(jobId: string): Promise<DiffResult> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/result`);
  return readApiJson(response, 'Failed to fetch job result.', parseDiffResult);
}

export async function createChapterAnalysis(input: CreateJobInput): Promise<ChapterAnalysisStatus> {
  const response = await fetch(`${API_BASE}/chapter-analyses`, {
    method: 'POST',
    body: buildFormData(input),
  });
  return readApiJson(response, 'Failed to start chapter analysis.', parseChapterAnalysisStatus);
}

export async function getChapterAnalysisStatus(analysisId: string): Promise<ChapterAnalysisStatus> {
  const response = await fetch(`${API_BASE}/chapter-analyses/${analysisId}`);
  return readApiJson(response, 'Failed to fetch chapter analysis status.', parseChapterAnalysisStatus);
}

export async function getChapterAnalysisResult(analysisId: string): Promise<ChapterAnalysisResult> {
  const response = await fetch(`${API_BASE}/chapter-analyses/${analysisId}/result`);
  return readApiJson(response, 'Failed to fetch chapter analysis result.', parseChapterAnalysisResult);
}

export async function validateChapterAnalysis(
  analysisId: string,
  payload: ChapterValidationRequest,
): Promise<ChapterValidationResult> {
  const response = await fetch(`${API_BASE}/chapter-analyses/${analysisId}/validate`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  return readApiJson(response, 'Failed to validate chapters.', parseChapterValidationResult);
}

export async function confirmChapterAnalysis(
  analysisId: string,
  payload: ChapterValidationRequest,
): Promise<CreateJobResponse> {
  const response = await fetch(`${API_BASE}/chapter-analyses/${analysisId}/confirm`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  return readApiJson(response, 'Failed to confirm chapter plan.', parseCreateJobResponse);
}

export function buildJobFileUrl(jobId: string, side: 'source' | 'modified'): string {
  return `${API_BASE}/jobs/${jobId}/files/${side}`;
}

export function buildJobReviewUrl(jobId: string, side: 'source' | 'modified'): string {
  return `${API_BASE}/jobs/${jobId}/review/${side}`;
}

export function buildChapterAnalysisFileUrl(analysisId: string, side: 'source' | 'modified'): string {
  return `${API_BASE}/chapter-analyses/${analysisId}/files/${side}`;
}

export function buildChapterAnalysisReviewUrl(analysisId: string, side: 'source' | 'modified'): string {
  return `${API_BASE}/chapter-analyses/${analysisId}/review/${side}`;
}
