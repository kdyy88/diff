import { useEffect, useMemo, useRef, useState } from 'react';
import { Document, Page } from 'react-pdf';

import type { DiffAnchor, DiffKind, PageMeta, PdfHighlightFragment } from '../types/api';
import { HighlightLayer } from './HighlightLayer';

interface PdfPaneProps {
  title: string;
  side: 'left' | 'right';
  fileUrl: string;
  pages: PageMeta[];
  anchors: DiffAnchor[];
  activeAnchorId: string | null;
  onSelectAnchor: (anchorId: string) => void;
}

export function PdfPane({
  title,
  side,
  fileUrl,
  pages,
  anchors,
  activeAnchorId,
  onSelectAnchor,
}: PdfPaneProps) {
  const paneRef = useRef<HTMLDivElement | null>(null);
  const [renderedWidth, setRenderedWidth] = useState(420);
  const [viewport, setViewport] = useState({ scrollTop: 0, height: 0 });
  const activeAnchor = anchors.find((anchor) => anchor.id === activeAnchorId) ?? anchors[0] ?? null;
  const PAGE_GAP = 16;

  useEffect(() => {
    const node = paneRef.current;
    if (!node) {
      return;
    }
    const syncMeasurements = () => {
      setRenderedWidth(Math.max(320, Math.floor(node.clientWidth - 24)));
      setViewport({
        scrollTop: node.scrollTop,
        height: node.clientHeight,
      });
    };
    const observer = new ResizeObserver(() => {
      syncMeasurements();
    });
    observer.observe(node);
    syncMeasurements();

    const handleScroll = () => {
      setViewport({
        scrollTop: node.scrollTop,
        height: node.clientHeight,
      });
    };
    node.addEventListener('scroll', handleScroll, { passive: true });

    return () => {
      observer.disconnect();
      node.removeEventListener('scroll', handleScroll);
    };
  }, []);

  const pageLayouts = useMemo(() => {
    let cursor = 0;
    return pages.map((page) => {
      const renderedHeight = renderedWidth * (page.height / page.width);
      const top = cursor;
      cursor += renderedHeight + PAGE_GAP;
      return {
        ...page,
        renderedHeight,
        top,
        bottom: top + renderedHeight,
      };
    });
  }, [pages, renderedWidth]);

  useEffect(() => {
    if (!activeAnchor) {
      return;
    }
    const fragments = (side === 'left' ? activeAnchor.left_fragments : activeAnchor.right_fragments).filter(
      (fragment): fragment is PdfHighlightFragment => fragment.kind === 'pdf',
    );
    const targetPage = fragments[0]?.page;
    const paneNode = paneRef.current;
    if (targetPage === undefined || !paneNode) {
      return;
    }

    const targetLayout = pageLayouts.find((page) => page.page === targetPage);
    if (!targetLayout) {
      return;
    }

    const targetTop = targetLayout.top - (paneNode.clientHeight - targetLayout.renderedHeight) / 2;
    paneNode.scrollTo({
      top: Math.max(0, targetTop),
      behavior: 'smooth',
    });
  }, [activeAnchor, pageLayouts, side]);

  const totalHeight = (pageLayouts.at(-1)?.bottom ?? 0) + PAGE_GAP;

  const visiblePages = useMemo(() => {
    const buffer = Math.max(viewport.height, 900);
    const start = Math.max(0, viewport.scrollTop - buffer);
    const end = viewport.scrollTop + viewport.height + buffer;
    return pageLayouts.filter((page) => page.bottom >= start && page.top <= end);
  }, [pageLayouts, viewport.height, viewport.scrollTop]);

  const fragmentsByPage = useMemo(() => {
    const next = new Map<number, Array<{ anchorId: string; page: number; viewport_ref: string; bbox: [number, number, number, number]; diffKind: DiffKind; active: boolean; kind: 'pdf' }>>();
    anchors.forEach((anchor) => {
      const fragments = (side === 'left' ? anchor.left_fragments : anchor.right_fragments).filter(
        (fragment): fragment is PdfHighlightFragment => fragment.kind === 'pdf',
      );
      fragments.forEach((fragment) => {
        const list = next.get(fragment.page) ?? [];
        list.push({
          ...fragment,
          anchorId: anchor.id,
          diffKind: anchor.kind,
          active: anchor.id === activeAnchorId,
        });
        next.set(fragment.page, list);
      });
    });
    return next;
  }, [activeAnchorId, anchors, side]);

  return (
    <section className="flex h-[calc(100vh-8rem)] flex-col rounded-[32px] border border-slate-200/70 bg-white/80 p-4 shadow-xl shadow-slate-200/50 backdrop-blur">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">{title}</p>
          <h2 className="mt-1 text-lg font-semibold text-slate-900">{side === 'left' ? 'Source PDF' : 'Modified PDF'}</h2>
        </div>
        <span className="rounded-full border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600">
          {pages.length} pages
        </span>
      </div>
      <div ref={paneRef} className="pdf-document overflow-y-auto rounded-[24px] bg-slate-100/70 p-3">
        <Document file={fileUrl} loading={<PaneState label="Loading PDF…" />}>
          <div className="relative" style={{ height: `${totalHeight}px` }}>
            {visiblePages.map((page) => (
              <div
                key={page.page}
                className="absolute left-0 right-0"
                style={{ top: `${page.top}px`, height: `${page.renderedHeight}px` }}
              >
                <div className="relative">
                  <Page pageNumber={page.page + 1} width={renderedWidth} renderAnnotationLayer={false} renderTextLayer={false} />
                  <HighlightLayer
                    page={page}
                    renderedWidth={renderedWidth}
                    fragments={fragmentsByPage.get(page.page) ?? []}
                    onSelect={onSelectAnchor}
                  />
                </div>
              </div>
            ))}
          </div>
        </Document>
      </div>
    </section>
  );
}

function PaneState({ label }: { label: string }) {
  return (
    <div className="flex min-h-[50vh] items-center justify-center rounded-[24px] border border-dashed border-slate-300 bg-white text-sm text-slate-500">
      {label}
    </div>
  );
}
