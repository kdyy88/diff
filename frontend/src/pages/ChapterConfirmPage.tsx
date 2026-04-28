import { useEffect, useMemo, useRef, useState } from 'react';
import { Document, Page } from 'react-pdf';

import {
  ApiError,
  buildChapterAnalysisFileUrl,
  confirmChapterAnalysis,
  validateChapterAnalysis,
} from '../api/client';
import type {
  AnalysisSide,
  ChapterAnalysisResult,
  ChapterDraft,
  ChapterValidationIssue,
  ChapterValidationRequest,
  ChapterValidationResult,
  CreateJobResponse,
} from '../types/api';

interface ChapterConfirmPageProps {
  analysisId: string;
  result: ChapterAnalysisResult;
  onConfirmed: (response: CreateJobResponse) => void;
  onCancel: () => void;
}

interface EditableChapter {
  id: string;
  title: string;
  start_page: number;
  start_y: number | null;
  level: number;
  parent_id: string | null;
  path: string[];
  source: ChapterDraft['source'];
  confidence: ChapterDraft['confidence'];
}

interface LocalIssue {
  side: AnalysisSide;
  chapter_id: string;
  message: string;
}

interface PreviewState {
  side: 'source' | 'modified';
  focusPage: number;
}

function toEditable(chapters: ChapterDraft[]): EditableChapter[] {
  return chapters.map((chapter) => ({
    id: chapter.id,
    title: chapter.title,
    start_page: chapter.start_page,
    start_y: chapter.start_y,
    level: chapter.level,
    parent_id: chapter.parent_id,
    path: chapter.path,
    source: chapter.source,
    confidence: chapter.confidence,
  }));
}

function sortEditable(items: EditableChapter[]): EditableChapter[] {
  return [...items].sort((left, right) => {
    if (!Number.isFinite(left.start_page)) {
      return 1;
    }
    if (!Number.isFinite(right.start_page)) {
      return -1;
    }
    if (left.start_page !== right.start_page) {
      return left.start_page - right.start_page;
    }
    return (left.start_y ?? 0) - (right.start_y ?? 0);
  });
}

function collectLocalIssues(side: AnalysisSide, totalPages: number, chapters: EditableChapter[]): LocalIssue[] {
  const issues: LocalIssue[] = [];
  let previousStart: number | null = null;
  let previousY: number | null = null;
  chapters.forEach((chapter, index) => {
    if (!Number.isInteger(chapter.start_page)) {
      issues.push({ side, chapter_id: chapter.id, message: 'Start page must be an integer.' });
      return;
    }
    if (chapter.start_page < 0 || chapter.start_page >= totalPages) {
      issues.push({ side, chapter_id: chapter.id, message: `Start page must be within 1 and ${totalPages}.` });
    }
    const samePageForwardY =
      previousStart !== null &&
      chapter.start_page === previousStart &&
      (previousY === null || chapter.start_y === null || chapter.start_y > previousY);
    if (previousStart !== null && (chapter.start_page < previousStart || (chapter.start_page === previousStart && !samePageForwardY))) {
      issues.push({ side, chapter_id: chapter.id, message: 'Start pages must be strictly increasing.' });
    }
    if (index === 0 && chapter.start_page !== 0) {
      issues.push({ side, chapter_id: chapter.id, message: 'The first chapter must start on page 1.' });
    }
    previousStart = chapter.start_page;
    previousY = chapter.start_y;
  });
  return issues;
}

function buildPayload(sourceChapters: EditableChapter[], modifiedChapters: EditableChapter[]): ChapterValidationRequest {
  return {
    source_chapters: sourceChapters.map(({ id, title, start_page, start_y, level, parent_id, path }) => ({
      id,
      title,
      start_page,
      start_y,
      level,
      parent_id,
      path,
    })),
    modified_chapters: modifiedChapters.map(({ id, title, start_page, start_y, level, parent_id, path }) => ({
      id,
      title,
      start_page,
      start_y,
      level,
      parent_id,
      path,
    })),
  };
}

function visualizeWhitespace(value: string | null | undefined): string {
  return (value ?? '').replace(/ /g, '·').replace(/\n/g, '↵');
}

function isValidationPayload(value: unknown): value is ChapterValidationResult {
  return Boolean(value && typeof value === 'object' && 'can_continue' in value && 'issues' in value);
}

export function ChapterConfirmPage({ analysisId, result, onConfirmed, onCancel }: ChapterConfirmPageProps) {
  const [sourceChapters, setSourceChapters] = useState<EditableChapter[]>(() => toEditable(result.source_plan.chapters));
  const [modifiedChapters, setModifiedChapters] = useState<EditableChapter[]>(() => toEditable(result.modified_plan.chapters));
  const [previewState, setPreviewState] = useState<PreviewState | null>(null);
  const [validation, setValidation] = useState<ChapterValidationResult | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const localIssues = useMemo(
    () => [
      ...collectLocalIssues('source', result.source_plan.total_pages, sourceChapters),
      ...collectLocalIssues('modified', result.modified_plan.total_pages, modifiedChapters),
    ],
    [modifiedChapters, result.modified_plan.total_pages, result.source_plan.total_pages, sourceChapters],
  );

  const payload = useMemo(
    () => buildPayload(sourceChapters, modifiedChapters),
    [modifiedChapters, sourceChapters],
  );

  useEffect(() => {
    let active = true;
    if (localIssues.length > 0) {
      setValidation(null);
      return () => {
        active = false;
      };
    }

    setValidationError(null);
    void validateChapterAnalysis(analysisId, payload)
      .then((nextValidation) => {
        if (active) {
          setValidation(nextValidation);
        }
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        setValidation(null);
        setValidationError(error instanceof Error ? error.message : 'Failed to validate chapter plan.');
      });

    return () => {
      active = false;
    };
  }, [analysisId, localIssues.length, payload]);

  const canContinue = localIssues.length === 0 && validation?.can_continue === true && !submitting;
  const validationIssues = validation?.issues ?? [];
  const validationErrors = validationIssues.filter((issue) => issue.severity === 'error');
  const validationWarnings = validationIssues.filter((issue) => issue.severity === 'warning');

  const handleChapterChange = (side: AnalysisSide, chapterId: string, updates: Partial<EditableChapter>) => {
    const apply = (items: EditableChapter[]) =>
      sortEditable(items.map((item) => (item.id === chapterId ? { ...item, ...updates } : item)));
    if (side === 'source') {
      setSourceChapters((items) => apply(items));
      return;
    }
    setModifiedChapters((items) => apply(items));
  };

  const handleAddChapter = (side: AnalysisSide) => {
    const factory = (items: EditableChapter[], totalPages: number) => {
      const lastStart = items.at(-1)?.start_page ?? 0;
      return sortEditable([
        ...items,
        {
          id: `${side}-manual-${crypto.randomUUID()}`,
          title: `Chapter ${items.length + 1}`,
          start_page: Math.min(Math.max(lastStart + 1, 0), Math.max(totalPages - 1, 0)),
          start_y: null,
          level: 1,
          parent_id: null,
          path: [`Chapter ${items.length + 1}`],
          source: 'manual',
          confidence: 'low',
        },
      ]);
    };
    if (side === 'source') {
      setSourceChapters((items) => factory(items, result.source_plan.total_pages));
      return;
    }
    setModifiedChapters((items) => factory(items, result.modified_plan.total_pages));
  };

  const handleDeleteChapter = (side: AnalysisSide, chapterId: string) => {
    if (side === 'source') {
      setSourceChapters((items) => (items.length > 1 ? items.filter((item) => item.id !== chapterId) : items));
      return;
    }
    setModifiedChapters((items) => (items.length > 1 ? items.filter((item) => item.id !== chapterId) : items));
  };

  const handleConfirm = async () => {
    setSubmitting(true);
    setValidationError(null);
    try {
      const response = await confirmChapterAnalysis(analysisId, payload);
      onConfirmed(response);
    } catch (error) {
      if (error instanceof ApiError && isValidationPayload(error.payload)) {
        setValidation(error.payload);
      }
      setValidationError(error instanceof Error ? error.message : 'Failed to confirm chapter plan.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="px-4 py-4">
      <div className="mx-auto flex max-w-[1600px] flex-col gap-3">
        <section className="sticky top-3 z-10 rounded-xl border border-slate-200/70 bg-white/95 px-4 py-3 shadow-lg shadow-slate-200/40 backdrop-blur">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Chapter Confirmation</p>
              <h1 className="mt-1 text-xl font-semibold text-slate-950">Review bookmark chapters</h1>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="mr-1 flex flex-wrap gap-1.5 text-xs">
                <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">Source {sourceChapters.length}</span>
                <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">Modified {modifiedChapters.length}</span>
                {localIssues.length + validationErrors.length > 0 ? (
                  <span className="rounded-full bg-rose-100 px-2.5 py-1 font-semibold text-rose-700">Errors {localIssues.length + validationErrors.length}</span>
                ) : null}
                {validationWarnings.length > 0 ? (
                  <span className="rounded-full bg-amber-100 px-2.5 py-1 font-semibold text-amber-700">Warnings {validationWarnings.length}</span>
                ) : null}
              </div>
              {result.document_kind === 'pdf' ? (
                <>
                  <button
                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                    onClick={() => setPreviewState({ side: 'source', focusPage: 0 })}
                    type="button"
                  >
                    Source PDF
                  </button>
                  <button
                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                    onClick={() => setPreviewState({ side: 'modified', focusPage: 0 })}
                    type="button"
                  >
                    Modified PDF
                  </button>
                </>
              ) : null}
              <button
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                onClick={onCancel}
                type="button"
              >
                Cancel
              </button>
              <button
                className="rounded-lg bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white shadow-md shadow-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
                disabled={!canContinue}
                onClick={() => void handleConfirm()}
                type="button"
              >
                {submitting ? 'Starting…' : 'Continue'}
              </button>
            </div>
          </div>
        </section>

        <ValidationSummary
          localIssues={localIssues}
          validationError={validationError}
          validationErrors={validationErrors}
          validationWarnings={validationWarnings}
        />

        <div className="grid gap-3 xl:grid-cols-2">
          <ChapterEditorPanel
            chapters={sourceChapters}
            documentKind={result.document_kind}
            label="Source chapters"
            onAddChapter={() => handleAddChapter('source')}
            onDeleteChapter={(chapterId) => handleDeleteChapter('source', chapterId)}
            onPreviewPage={(page) => setPreviewState({ side: 'source', focusPage: page })}
            onUpdateChapter={(chapterId, updates) => handleChapterChange('source', chapterId, updates)}
            side="source"
            totalPages={result.source_plan.total_pages}
          />
          <ChapterEditorPanel
            chapters={modifiedChapters}
            documentKind={result.document_kind}
            label="Modified chapters"
            onAddChapter={() => handleAddChapter('modified')}
            onDeleteChapter={(chapterId) => handleDeleteChapter('modified', chapterId)}
            onPreviewPage={(page) => setPreviewState({ side: 'modified', focusPage: page })}
            onUpdateChapter={(chapterId, updates) => handleChapterChange('modified', chapterId, updates)}
            side="modified"
            totalPages={result.modified_plan.total_pages}
          />
        </div>
      </div>

      {previewState ? (
        <PdfPreviewModal
          fileUrl={buildChapterAnalysisFileUrl(analysisId, previewState.side)}
          focusedPage={previewState.focusPage}
          onClose={() => setPreviewState(null)}
          pageCount={previewState.side === 'source' ? result.source_plan.total_pages : result.modified_plan.total_pages}
          title={previewState.side === 'source' ? 'Source PDF' : 'Modified PDF'}
        />
      ) : null}
    </main>
  );
}

function ValidationSummary({
  localIssues,
  validationError,
  validationErrors,
  validationWarnings,
}: {
  localIssues: LocalIssue[];
  validationError: string | null;
  validationErrors: ChapterValidationIssue[];
  validationWarnings: ChapterValidationIssue[];
}) {
  const issueCount = localIssues.length + validationErrors.length + validationWarnings.length + (validationError ? 1 : 0);
  if (issueCount === 0) {
    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700">
        Chapter coverage and matching are valid.
      </div>
    );
  }

  return (
    <details className="rounded-lg border border-slate-200 bg-white/90 shadow-sm shadow-slate-200/40" open={localIssues.length + validationErrors.length > 0}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs text-slate-700 [&::-webkit-details-marker]:hidden">
        <span className="font-semibold text-slate-900">Validation messages</span>
        <span className="flex flex-wrap gap-1.5">
          {localIssues.length + validationErrors.length > 0 ? (
            <span className="rounded-full bg-rose-100 px-2 py-0.5 font-semibold text-rose-700">{localIssues.length + validationErrors.length} errors</span>
          ) : null}
          {validationWarnings.length > 0 ? (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 font-semibold text-amber-700">{validationWarnings.length} warnings</span>
          ) : null}
          {validationError ? <span className="rounded-full bg-rose-100 px-2 py-0.5 font-semibold text-rose-700">request failed</span> : null}
          <span className="text-slate-500">Click to expand</span>
        </span>
      </summary>
      <div className="max-h-56 overflow-y-auto border-t border-slate-100 p-2">
        <div className="grid gap-1.5 lg:grid-cols-2">
          {localIssues.map((issue) => (
            <CompactLocalIssue key={`${issue.side}-${issue.chapter_id}-${issue.message}`} issue={issue} />
          ))}
          {validationErrors.map((issue) => (
            <ValidationIssueCard key={`${issue.side}-${issue.chapter_id}-${issue.code}`} issue={issue} />
          ))}
          {validationWarnings.map((issue) => (
            <ValidationIssueCard key={`${issue.side}-${issue.chapter_id}-${issue.code}`} issue={issue} />
          ))}
          {validationError ? <div className="rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-xs text-rose-700">{validationError}</div> : null}
        </div>
      </div>
    </details>
  );
}

function CompactLocalIssue({ issue }: { issue: LocalIssue }) {
  return (
    <div className="rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-xs text-rose-800">
      <span className="font-semibold">{issue.side === 'source' ? 'Source' : 'Modified'}</span>
      <span className="mx-1 text-rose-400">·</span>
      <span>{issue.message}</span>
    </div>
  );
}

function ValidationIssueCard({ issue }: { issue: ChapterValidationIssue }) {
  const isWarning = issue.severity === 'warning';
  return (
    <div className={`rounded-md border px-2 py-1.5 text-xs ${isWarning ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-rose-200 bg-rose-50 text-rose-800'}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold">{issue.side === 'source' ? 'Source' : 'Modified'}</span>
        <span className={isWarning ? 'text-amber-500' : 'text-rose-400'}>·</span>
        <span className="font-medium">{issue.message}</span>
      </div>
      <p className="mt-1 truncate opacity-80">Raw: {visualizeWhitespace(issue.raw_title)}</p>
      <p className="truncate opacity-80">Normalized: {visualizeWhitespace(issue.normalized_title)}</p>
      {issue.peer_raw_title || issue.peer_normalized_title ? (
        <details className="mt-1">
          <summary className="cursor-pointer text-[11px] font-semibold opacity-80">Peer detail</summary>
          <div className="mt-1 space-y-0.5 rounded bg-white/60 px-2 py-1 text-[11px]">
            <p className="truncate">Raw: {visualizeWhitespace(issue.peer_raw_title)}</p>
            <p className="truncate">Normalized: {visualizeWhitespace(issue.peer_normalized_title)}</p>
            {issue.suggested_peer_score !== null ? <p>Similarity: {issue.suggested_peer_score}</p> : null}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function ChapterEditorPanel({
  documentKind,
  label,
  side,
  chapters,
  totalPages,
  onUpdateChapter,
  onDeleteChapter,
  onAddChapter,
  onPreviewPage,
}: {
  documentKind: 'pdf' | 'docx';
  label: string;
  side: AnalysisSide;
  chapters: EditableChapter[];
  totalPages: number;
  onUpdateChapter: (chapterId: string, updates: Partial<EditableChapter>) => void;
  onDeleteChapter: (chapterId: string) => void;
  onAddChapter: () => void;
  onPreviewPage: (page: number) => void;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200/70 bg-white/90 shadow-md shadow-slate-200/40 backdrop-blur">
      <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-3 py-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">
            {side === 'source' ? 'Left' : 'Right'} outline
          </p>
          <h2 className="text-sm font-semibold text-slate-900">{label} <span className="font-normal text-slate-500">({chapters.length})</span></h2>
        </div>
        <button
          className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
          onClick={onAddChapter}
          type="button"
        >
          Add chapter
        </button>
      </div>
      <div className="max-h-[calc(100vh-210px)] overflow-y-auto divide-y divide-slate-100">
        {chapters.map((chapter, index) => (
          <div key={chapter.id} className="grid gap-2 px-3 py-2 md:grid-cols-[64px_minmax(0,1fr)_96px_112px] md:items-center">
            <div className="flex items-center gap-2 md:block">
              <span className="inline-flex min-w-10 justify-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-600">#{index + 1}</span>
              <span className="text-[11px] text-slate-500 md:mt-1 md:block">L{chapter.level}</span>
            </div>
            <label className="block min-w-0" style={{ paddingLeft: `${Math.max(chapter.level - 1, 0) * 14}px` }}>
              <span className="sr-only">Title</span>
              <input
                className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                onChange={(event) => onUpdateChapter(chapter.id, { title: event.target.value })}
                value={chapter.title}
              />
              <span className="mt-0.5 block truncate text-[11px] text-slate-400">{chapter.source} · {chapter.confidence}</span>
            </label>
            <label className="block">
              <span className="sr-only">{documentKind === 'pdf' ? 'Start page' : 'Start block'}</span>
              <input
                className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                max={totalPages}
                min={1}
                onChange={(event) => {
                  const rawValue = event.target.value;
                  if (rawValue === '') {
                    onUpdateChapter(chapter.id, { start_page: Number.NaN });
                    return;
                  }
                  onUpdateChapter(chapter.id, { start_page: Number(rawValue) - 1 });
                }}
                type="number"
                value={Number.isFinite(chapter.start_page) ? chapter.start_page + 1 : ''}
              />
              <span className="mt-0.5 block text-[11px] text-slate-400">{documentKind === 'pdf' ? 'page' : 'block'}</span>
            </label>
            <div className="flex flex-wrap gap-1.5 md:justify-end">
                {documentKind === 'pdf' ? (
                  <button
                    className="rounded-md border border-slate-300 px-2 py-1 text-xs font-semibold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                    onClick={() => onPreviewPage(Math.max(chapter.start_page, 0))}
                    type="button"
                  >
                    Preview
                  </button>
                ) : null}
                <button
                  className="rounded-md border border-rose-200 px-2 py-1 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={chapters.length === 1}
                  onClick={() => onDeleteChapter(chapter.id)}
                  type="button"
                >
                  Delete
                </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function PdfPreviewModal({
  title,
  fileUrl,
  pageCount,
  focusedPage,
  onClose,
}: {
  title: string;
  fileUrl: string;
  pageCount: number;
  focusedPage: number;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 px-4 py-6 backdrop-blur-sm">
      <div className="flex h-[min(92vh,1100px)] w-full max-w-5xl flex-col rounded-[32px] border border-slate-200 bg-white p-5 shadow-2xl shadow-slate-950/20">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">PDF Preview</p>
            <h2 className="mt-1 text-xl font-semibold text-slate-950">{title}</h2>
          </div>
          <button
            className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
            onClick={onClose}
            type="button"
          >
            Close
          </button>
        </div>
        <PdfPreviewDocument fileUrl={fileUrl} focusedPage={focusedPage} pageCount={pageCount} />
      </div>
    </div>
  );
}

function PdfPreviewDocument({
  fileUrl,
  pageCount,
  focusedPage,
}: {
  fileUrl: string;
  pageCount: number;
  focusedPage: number;
}) {
  const pageRefs = useRef<Array<HTMLDivElement | null>>([]);

  useEffect(() => {
    pageRefs.current[focusedPage]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [focusedPage]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto rounded-[24px] bg-slate-100/70 p-3">
      <Document file={fileUrl} loading={<PaneState label="Loading PDF…" />}>
        <div className="space-y-4">
          {Array.from({ length: pageCount }, (_, index) => (
            <div
              key={index}
              ref={(node) => {
                pageRefs.current[index] = node;
              }}
              className={`rounded-[24px] p-2 ${focusedPage === index ? 'bg-emerald-100/70 ring-2 ring-emerald-300' : ''}`}
            >
              <Page pageNumber={index + 1} width={780} renderAnnotationLayer={false} renderTextLayer={false} />
            </div>
          ))}
        </div>
      </Document>
    </div>
  );
}

function PaneState({ label }: { label: string }) {
  return (
    <div className="flex min-h-[50vh] items-center justify-center rounded-[24px] border border-dashed border-slate-300 bg-white text-sm text-slate-500">
      {label}
    </div>
  );
}
