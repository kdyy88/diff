import { useMemo, useState } from 'react';

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
  const [openChapterIds, setOpenChapterIds] = useState<Set<string>>(() => new Set());
  const chapterTree = useMemo(() => {
    const parents = result.chapters.filter((chapter) => chapter.level <= 1 || !chapter.parent_id);
    return parents.map((parent) => ({
      parent,
      children: result.chapters.filter((chapter) => chapter.parent_id === parent.id),
    }));
  }, [result.chapters]);

  const toggleChapter = (chapterId: string) => {
    setOpenChapterIds((current) => {
      const next = new Set(current);
      if (next.has(chapterId)) {
        next.delete(chapterId);
      } else {
        next.add(chapterId);
      }
      return next;
    });
  };

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
        <div className="mb-5 rounded-[24px] border border-slate-200/70 bg-white/85 p-3 shadow-lg shadow-slate-200/40 backdrop-blur">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <button
              className={`rounded-md px-3 py-2 text-sm font-semibold transition ${
                activeChapterId === null
                  ? 'bg-slate-950 text-white shadow-md shadow-slate-300'
                  : 'border border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
              }`}
              onClick={() => onSelectChapter(null)}
              type="button"
            >
              All Chapters
            </button>
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {chapterTree.map(({ parent, children }) => {
              const isOpen = openChapterIds.has(parent.id) || activeChapterId === parent.id || children.some((child) => child.id === activeChapterId);
              return (
                <div key={parent.id} className="rounded-lg border border-slate-200 bg-white">
                  <div className="flex items-center gap-1 p-1">
                    <button
                      className={`min-w-0 flex-1 rounded-md px-3 py-2 text-left text-sm font-semibold transition ${
                        activeChapterId === parent.id
                          ? 'bg-slate-950 text-white'
                          : 'text-slate-800 hover:bg-slate-50'
                      }`}
                      onClick={() => onSelectChapter(parent.id)}
                      type="button"
                    >
                      <span className="block truncate">{parent.title}</span>
                      <span className={activeChapterId === parent.id ? 'text-xs text-slate-300' : 'text-xs text-slate-500'}>
                        {parent.anchor_count} diffs
                      </span>
                    </button>
                    {children.length > 0 ? (
                      <button
                        className="h-9 w-9 rounded-md border border-slate-200 text-sm font-semibold text-slate-600 hover:bg-slate-50"
                        onClick={() => toggleChapter(parent.id)}
                        title={isOpen ? 'Collapse section' : 'Expand section'}
                        type="button"
                      >
                        {isOpen ? '^' : 'v'}
                      </button>
                    ) : null}
                  </div>
                  {isOpen && children.length > 0 ? (
                    <div className="border-t border-slate-100 p-1">
                      {children.map((child) => (
                        <button
                          key={child.id}
                          className={`block w-full rounded-md px-3 py-2 text-left text-sm transition ${
                            activeChapterId === child.id
                              ? 'bg-slate-900 text-white'
                              : 'text-slate-700 hover:bg-slate-50'
                          }`}
                          onClick={() => onSelectChapter(child.id)}
                          type="button"
                        >
                          <span className="block truncate">{child.title}</span>
                          <span className={activeChapterId === child.id ? 'text-xs text-slate-300' : 'text-xs text-slate-500'}>
                            {child.anchor_count} diffs
                          </span>
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
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
