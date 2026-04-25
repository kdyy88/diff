import { useMemo } from 'react';

import { buildJobFileUrl, buildJobReviewUrl } from '../api/client';
import { ControlBar } from '../components/ControlBar';
import { DiffList } from '../components/DiffList';
import { HtmlPane } from '../components/HtmlPane';
import { PdfPane } from '../components/PdfPane';
import type { DiffResult } from '../types/api';

interface ReviewPageProps {
  jobId: string;
  result: DiffResult;
  activeAnchorId: string | null;
  activeChapterId: string | null;
  showReflow: boolean;
  onSelectAnchor: (anchorId: string) => void;
  onSelectChapter: (chapterId: string | null) => void;
  onToggleReflow: () => void;
  onNext: () => void;
  onPrevious: () => void;
}

export function ReviewPage({
  jobId,
  result,
  activeAnchorId,
  activeChapterId,
  showReflow,
  onSelectAnchor,
  onSelectChapter,
  onToggleReflow,
  onNext,
  onPrevious,
}: ReviewPageProps) {
  const visibleAnchors = useMemo(
    () =>
      result.anchors.filter((anchor) => {
        const matchesReflow = showReflow || anchor.kind !== 'reflow';
        const matchesChapter = activeChapterId ? anchor.chapter_id === activeChapterId : true;
        return matchesReflow && matchesChapter;
      }),
    [activeChapterId, result.anchors, showReflow],
  );
  const selectedChapter = result.chapters.find((chapter) => chapter.id === activeChapterId) ?? null;
  const summary = selectedChapter?.summary ?? result.summary;
  const currentIndex = Math.max(
    0,
    visibleAnchors.findIndex((anchor) => anchor.id === activeAnchorId),
  );
  const allowReflow = result.document_kind === 'pdf';

  return (
    <main className="min-h-screen px-5 py-5">
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">Review Workspace</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-950">
            {result.document_kind === 'pdf' ? 'Dual-pane audit review' : 'Dual-pane Word review'}
          </h1>
        </div>
        <div className="rounded-[28px] border border-slate-200/70 bg-white/85 px-5 py-4 shadow-lg shadow-slate-200/40 backdrop-blur">
          <div className="grid grid-cols-2 gap-5 text-sm text-slate-600 md:grid-cols-4">
            <SummaryCell label="Insertions" value={String(summary.insertions)} />
            <SummaryCell label="Deletions" value={String(summary.deletions)} />
            <SummaryCell label="Replacements" value={String(summary.replacements)} />
            <SummaryCell label="Reflows" value={String(summary.reflows)} />
          </div>
        </div>
      </div>

      {result.chapters.length > 0 ? (
        <div className="mb-5 rounded-[28px] border border-slate-200/70 bg-white/85 p-4 shadow-lg shadow-slate-200/40 backdrop-blur">
          <div className="flex flex-wrap items-center gap-2">
            <button
              className={`rounded-full px-3 py-2 text-sm font-semibold transition ${
                activeChapterId === null
                  ? 'bg-slate-950 text-white shadow-md shadow-slate-300'
                  : 'border border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
              }`}
              onClick={() => onSelectChapter(null)}
              type="button"
            >
              All Chapters
            </button>
            {result.chapters.map((chapter) => (
              <button
                key={chapter.id}
                className={`rounded-full px-3 py-2 text-sm font-semibold transition ${
                  activeChapterId === chapter.id
                    ? 'bg-slate-950 text-white shadow-md shadow-slate-300'
                    : 'border border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
                }`}
                onClick={() => onSelectChapter(chapter.id)}
                type="button"
              >
                {chapter.title} · {chapter.anchor_count}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px_minmax(0,1fr)]">
        {result.document_kind === 'pdf' ? (
          <PdfPane
            title="Left Pane"
            side="left"
            fileUrl={buildJobFileUrl(jobId, 'source')}
            pages={result.pages_left}
            anchors={visibleAnchors}
            activeAnchorId={activeAnchorId}
            onSelectAnchor={onSelectAnchor}
          />
        ) : (
          <HtmlPane
            title="Left Pane"
            side="left"
            reviewUrl={buildJobReviewUrl(jobId, 'source')}
            anchors={visibleAnchors}
            activeAnchorId={activeAnchorId}
            onSelectAnchor={onSelectAnchor}
          />
        )}
        <div className="space-y-5">
          <ControlBar
            allowReflow={allowReflow}
            currentIndex={currentIndex}
            total={visibleAnchors.length}
            showReflow={allowReflow ? showReflow : false}
            onToggleReflow={onToggleReflow}
            onPrevious={onPrevious}
            onNext={onNext}
          />
          <DiffList anchors={visibleAnchors} activeAnchorId={activeAnchorId} onSelect={onSelectAnchor} />
        </div>
        {result.document_kind === 'pdf' ? (
          <PdfPane
            title="Right Pane"
            side="right"
            fileUrl={buildJobFileUrl(jobId, 'modified')}
            pages={result.pages_right}
            anchors={visibleAnchors}
            activeAnchorId={activeAnchorId}
            onSelectAnchor={onSelectAnchor}
          />
        ) : (
          <HtmlPane
            title="Right Pane"
            side="right"
            reviewUrl={buildJobReviewUrl(jobId, 'modified')}
            anchors={visibleAnchors}
            activeAnchorId={activeAnchorId}
            onSelectAnchor={onSelectAnchor}
          />
        )}
      </div>
    </main>
  );
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">{label}</p>
      <p className="mt-1 text-lg font-semibold text-slate-900">{value}</p>
    </div>
  );
}
