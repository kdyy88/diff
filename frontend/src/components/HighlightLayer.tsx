import type { DiffKind, PageMeta, PdfHighlightFragment } from '../types/api';

const KIND_CLASS: Record<DiffKind, string> = {
  insert: 'bg-emerald-400/30 ring-1 ring-emerald-500/50',
  delete: 'bg-rose-400/25 ring-1 ring-rose-500/40',
  replace: 'bg-amber-300/35 ring-1 ring-amber-500/40',
  reflow: 'bg-sky-300/20 ring-1 ring-sky-500/40',
};

interface HighlightLayerProps {
  page: PageMeta;
  renderedWidth: number;
  fragments: Array<PdfHighlightFragment & { anchorId: string; diffKind: DiffKind; active: boolean }>;
  onSelect: (anchorId: string) => void;
}

export function HighlightLayer({
  page,
  renderedWidth,
  fragments,
  onSelect,
}: HighlightLayerProps) {
  if (renderedWidth <= 0) {
    return null;
  }

  const scale = renderedWidth / page.width;

  return (
    <div className="pointer-events-none absolute inset-0">
      {fragments.map((fragment) => {
        const [x0, y0, x1, y1] = fragment.bbox;
        return (
          <button
            key={`${fragment.anchorId}-${fragment.viewport_ref}`}
            className={`pointer-events-auto absolute rounded-md transition ${KIND_CLASS[fragment.diffKind]} ${
              fragment.active ? 'opacity-100 ring-2 ring-slate-950/70' : 'opacity-45'
            }`}
            onClick={() => onSelect(fragment.anchorId)}
            style={{
              left: `${x0 * scale}px`,
              top: `${y0 * scale}px`,
              width: `${(x1 - x0) * scale}px`,
              height: `${(y1 - y0) * scale}px`,
            }}
            title={fragment.diffKind}
            type="button"
          />
        );
      })}
    </div>
  );
}
