import { useMemo } from 'react';

import { buildPdfUrl } from '../api/client';
import { ControlBar } from '../components/ControlBar';
import { DiffList } from '../components/DiffList';
import { PdfPane } from '../components/PdfPane';
import type { DiffResult } from '../types/api';

interface ReviewPageProps {
  jobId: string;
  result: DiffResult;
  activeAnchorId: string | null;
  showReflow: boolean;
  onSelectAnchor: (anchorId: string) => void;
  onToggleReflow: () => void;
  onNext: () => void;
  onPrevious: () => void;
}

export function ReviewPage({
  jobId,
  result,
  activeAnchorId,
  showReflow,
  onSelectAnchor,
  onToggleReflow,
  onNext,
  onPrevious,
}: ReviewPageProps) {
  const visibleAnchors = useMemo(
    () => result.anchors.filter((anchor) => showReflow || anchor.kind !== 'reflow'),
    [result.anchors, showReflow],
  );
  const currentIndex = Math.max(
    0,
    visibleAnchors.findIndex((anchor) => anchor.id === activeAnchorId),
  );

  return (
    <main className="min-h-screen px-5 py-5">
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">Review Workspace</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-950">
            Dual-pane audit review
          </h1>
        </div>
        <div className="rounded-[28px] border border-slate-200/70 bg-white/85 px-5 py-4 shadow-lg shadow-slate-200/40 backdrop-blur">
          <div className="grid grid-cols-2 gap-5 text-sm text-slate-600 md:grid-cols-4">
            <SummaryCell label="Insertions" value={String(result.summary.insertions)} />
            <SummaryCell label="Deletions" value={String(result.summary.deletions)} />
            <SummaryCell label="Replacements" value={String(result.summary.replacements)} />
            <SummaryCell label="Reflows" value={String(result.summary.reflows)} />
          </div>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px_minmax(0,1fr)]">
        <PdfPane
          title="Left Pane"
          side="left"
          fileUrl={buildPdfUrl(jobId, 'source')}
          pages={result.pages_left}
          anchors={visibleAnchors}
          activeAnchorId={activeAnchorId}
          onSelectAnchor={onSelectAnchor}
        />
        <div className="space-y-5">
          <ControlBar
            currentIndex={currentIndex}
            total={visibleAnchors.length}
            showReflow={showReflow}
            onToggleReflow={onToggleReflow}
            onPrevious={onPrevious}
            onNext={onNext}
          />
          <DiffList anchors={visibleAnchors} activeAnchorId={activeAnchorId} onSelect={onSelectAnchor} />
        </div>
        <PdfPane
          title="Right Pane"
          side="right"
          fileUrl={buildPdfUrl(jobId, 'modified')}
          pages={result.pages_right}
          anchors={visibleAnchors}
          activeAnchorId={activeAnchorId}
          onSelectAnchor={onSelectAnchor}
        />
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
