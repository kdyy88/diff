import type { DiffAnchor } from '../types/api';

const KIND_LABEL: Record<DiffAnchor['kind'], string> = {
  insert: 'Insert',
  delete: 'Delete',
  replace: 'Replace',
  reflow: 'Reflow',
};

const KIND_STYLE: Record<DiffAnchor['kind'], string> = {
  insert: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  delete: 'border-rose-200 bg-rose-50 text-rose-700',
  replace: 'border-amber-200 bg-amber-50 text-amber-700',
  reflow: 'border-sky-200 bg-sky-50 text-sky-700',
};

interface DiffListProps {
  anchors: DiffAnchor[];
  activeAnchorId: string | null;
  onSelect: (anchorId: string) => void;
}

export function DiffList({ anchors, activeAnchorId, onSelect }: DiffListProps) {
  return (
    <div className="rounded-[28px] border border-slate-200/70 bg-white/85 p-4 shadow-lg shadow-slate-200/40 backdrop-blur">
      <div className="mb-3">
        <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
          Diff Anchors
        </p>
        <h2 className="mt-1 text-lg font-semibold text-slate-900">Focused review list</h2>
      </div>
      <div className="max-h-[calc(100vh-20rem)] space-y-3 overflow-y-auto pr-1">
        {anchors.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-300 p-6 text-sm text-slate-500">
            No anchors in the current filter.
          </div>
        ) : null}
        {anchors.map((anchor, index) => {
          const isActive = anchor.id === activeAnchorId;
          const tableLabel = anchor.source_type === 'table'
            ? anchor.table_context && anchor.table_context.row >= 0 && anchor.table_context.col >= 0
              ? anchor.table_context.col_label
                ? `Table Cell · ${anchor.table_context.col_label}`
                : `Table Cell · R${anchor.table_context.row + 1} C${anchor.table_context.col + 1}`
              : 'Table Region'
            : null;
          return (
            <button
              key={anchor.id}
              className={`w-full rounded-2xl border p-4 text-left transition ${
                isActive
                  ? 'border-slate-900 bg-slate-900 text-white shadow-xl shadow-slate-300'
                  : 'border-slate-200 bg-white/90 hover:border-slate-300 hover:bg-slate-50'
              }`}
              onClick={() => onSelect(anchor.id)}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold">#{index + 1}</span>
                <span
                  className={`rounded-full border px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${
                    isActive ? 'border-white/30 bg-white/10 text-white' : KIND_STYLE[anchor.kind]
                  }`}
                >
                  {KIND_LABEL[anchor.kind]}
                </span>
              </div>
              <div className="mt-3 space-y-2 text-sm">
                {tableLabel ? (
                  <p className={isActive ? 'text-xs text-slate-300' : 'text-xs text-slate-500'}>
                    {tableLabel}
                  </p>
                ) : null}
                {anchor.raw_event_count > 1 ? (
                  <p className={isActive ? 'text-xs text-slate-300' : 'text-xs text-slate-500'}>
                    Merged from {anchor.raw_event_count} edits
                  </p>
                ) : null}
                <div>
                  <p className={`text-[11px] uppercase tracking-[0.18em] ${isActive ? 'text-slate-300' : 'text-slate-400'}`}>
                    Source
                  </p>
                  <p className={isActive ? 'text-slate-100' : 'text-slate-700'}>
                    {anchor.excerpt_left || '—'}
                  </p>
                </div>
                <div>
                  <p className={`text-[11px] uppercase tracking-[0.18em] ${isActive ? 'text-slate-300' : 'text-slate-400'}`}>
                    Modified
                  </p>
                  <p className={isActive ? 'text-slate-100' : 'text-slate-700'}>
                    {anchor.excerpt_right || '—'}
                  </p>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
