interface ControlBarProps {
  allowReflow: boolean;
  currentIndex: number;
  total: number;
  showReflow: boolean;
  onToggleReflow: () => void;
  onPrevious: () => void;
  onNext: () => void;
}

export function ControlBar({
  allowReflow,
  currentIndex,
  total,
  showReflow,
  onToggleReflow,
  onPrevious,
  onNext,
}: ControlBarProps) {
  return (
    <div className="rounded-[28px] border border-slate-200/70 bg-white/85 p-4 shadow-lg shadow-slate-200/40 backdrop-blur">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
            Review Controls
          </p>
          <p className="mt-1 text-sm text-slate-700">
            {total === 0 ? 'No visible anchors' : `Anchor ${currentIndex + 1} of ${total}`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="rounded-full border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
            onClick={onPrevious}
            type="button"
          >
            Previous
          </button>
          <button
            className="rounded-full border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50"
            onClick={onNext}
            type="button"
          >
            Next
          </button>
          {allowReflow ? (
            <button
              className={`rounded-full px-3 py-2 text-sm font-semibold transition ${
                showReflow
                  ? 'bg-slate-900 text-white shadow-md shadow-slate-300'
                  : 'border border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
              }`}
              onClick={onToggleReflow}
              type="button"
            >
              {showReflow ? 'Hide Reflow' : 'Show Reflow'}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
