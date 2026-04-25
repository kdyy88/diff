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
    return left.start_page - right.start_page;
  });
}

function collectLocalIssues(side: AnalysisSide, totalPages: number, chapters: EditableChapter[]): LocalIssue[] {
  const issues: LocalIssue[] = [];
  let previousStart: number | null = null;
  chapters.forEach((chapter, index) => {
    if (!Number.isInteger(chapter.start_page)) {
      issues.push({ side, chapter_id: chapter.id, message: 'Start page must be an integer.' });
      return;
    }
    if (chapter.start_page < 0 || chapter.start_page >= totalPages) {
      issues.push({ side, chapter_id: chapter.id, message: `Start page must be within 1 and ${totalPages}.` });
    }
    if (previousStart !== null && chapter.start_page <= previousStart) {
      issues.push({ side, chapter_id: chapter.id, message: 'Start pages must be strictly increasing.' });
    }
    if (index === 0 && chapter.start_page !== 0) {
      issues.push({ side, chapter_id: chapter.id, message: 'The first chapter must start on page 1.' });
    }
    previousStart = chapter.start_page;
  });
  return issues;
}

function buildPayload(sourceChapters: EditableChapter[], modifiedChapters: EditableChapter[]): ChapterValidationRequest {
  return {
    source_chapters: sourceChapters.map(({ id, title, start_page }) => ({ id, title, start_page })),
    modified_chapters: modifiedChapters.map(({ id, title, start_page }) => ({ id, title, start_page })),
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
    <main className="px-5 py-5">
      <div className="mx-auto flex max-w-7xl flex-col gap-5">
        <section className="sticky top-4 z-10 rounded-[28px] border border-slate-200/70 bg-white/90 p-5 shadow-xl shadow-slate-200/40 backdrop-blur">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Chapter Confirmation</p>
              <h1 className="mt-2 text-3xl font-semibold text-slate-950">Review bookmark chapters</h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
                {result.document_kind === 'pdf'
                  ? 'Review the detected bookmark outline, fix titles or starting pages if needed, and continue when both sides match. PDF preview stays off the main page and opens only when you need it.'
                  : 'Review the detected Heading 1 outline, adjust titles or starting block indexes if needed, and continue when both sides match.'}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {result.document_kind === 'pdf' ? (
                <>
                  <button
                    className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                    onClick={() => setPreviewState({ side: 'source', focusPage: 0 })}
                    type="button"
                  >
                    View source PDF
                  </button>
                  <button
                    className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                    onClick={() => setPreviewState({ side: 'modified', focusPage: 0 })}
                    type="button"
                  >
                    View modified PDF
                  </button>
                </>
              ) : null}
              <button
                className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                onClick={onCancel}
                type="button"
              >
                Cancel
              </button>
              <button
                className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
                disabled={!canContinue}
                onClick={() => void handleConfirm()}
                type="button"
              >
                {submitting ? 'Starting chapter diff…' : 'Continue'}
              </button>
            </div>
          </div>

          {localIssues.length > 0 || validation?.issues.length || validationError ? (
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {localIssues.map((issue) => (
                <div
                  key={`${issue.side}-${issue.chapter_id}-${issue.message}`}
                  className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
                >
                  <p className="font-semibold uppercase tracking-[0.14em]">
                    {issue.side === 'source' ? 'Source' : 'Modified'} local check
                  </p>
                  <p className="mt-1">{issue.message}</p>
                </div>
              ))}
              {validation?.issues.map((issue) => (
                <ValidationIssueCard key={`${issue.side}-${issue.chapter_id}-${issue.code}`} issue={issue} />
              ))}
              {validationError ? (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                  {validationError}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
              Chapter coverage and matching are valid. Continuing will start the chapter-aware diff job.
            </div>
          )}
        </section>

        <div className="grid gap-5 xl:grid-cols-2">
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

function ValidationIssueCard({ issue }: { issue: ChapterValidationIssue }) {
  return (
    <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
      <p className="font-semibold uppercase tracking-[0.14em]">{issue.side === 'source' ? 'Source' : 'Modified'} match check</p>
      <p className="mt-1">{issue.message}</p>
      <p className="mt-2 text-xs text-rose-700">Raw: {visualizeWhitespace(issue.raw_title)}</p>
      <p className="mt-1 text-xs text-rose-700">Normalized: {visualizeWhitespace(issue.normalized_title)}</p>
      {issue.peer_raw_title || issue.peer_normalized_title ? (
        <div className="mt-2 rounded-xl border border-rose-200/80 bg-white/60 px-3 py-2 text-xs text-rose-700">
          <p>Closest peer raw: {visualizeWhitespace(issue.peer_raw_title)}</p>
          <p className="mt-1">Closest peer normalized: {visualizeWhitespace(issue.peer_normalized_title)}</p>
          {issue.suggested_peer_score !== null ? <p className="mt-1">Similarity: {issue.suggested_peer_score}</p> : null}
        </div>
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
    <section className="rounded-[28px] border border-slate-200/70 bg-white/85 p-4 shadow-lg shadow-slate-200/40 backdrop-blur">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
            {side === 'source' ? 'Left' : 'Right'} outline
          </p>
          <h2 className="mt-1 text-lg font-semibold text-slate-900">{label}</h2>
        </div>
        <button
          className="rounded-full border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
          onClick={onAddChapter}
          type="button"
        >
          Add chapter
        </button>
      </div>
      <div className="space-y-3">
        {chapters.map((chapter, index) => (
          <div key={chapter.id} className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Chapter {index + 1}</p>
                <p className="mt-1 text-xs text-slate-500">{chapter.source} · {chapter.confidence}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {documentKind === 'pdf' ? (
                  <button
                    className="rounded-full border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
                    onClick={() => onPreviewPage(Math.max(chapter.start_page, 0))}
                    type="button"
                  >
                    Preview page
                  </button>
                ) : null}
                <button
                  className="rounded-full border border-rose-200 px-3 py-1 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={chapters.length === 1}
                  onClick={() => onDeleteChapter(chapter.id)}
                  type="button"
                >
                  Delete
                </button>
              </div>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_140px]">
              <label className="block">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">Title</p>
                <input
                  className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                  onChange={(event) => onUpdateChapter(chapter.id, { title: event.target.value })}
                  value={chapter.title}
                />
              </label>
              <label className="block">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">{documentKind === 'pdf' ? 'Start page' : 'Start block'}</p>
                <input
                  className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-400"
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
              </label>
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