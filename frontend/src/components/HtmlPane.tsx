import { useEffect, useMemo, useRef, useState } from 'react';

import type { DiffAnchor, WordHighlightFragment } from '../types/api';

interface HtmlPaneProps {
  title: string;
  side: 'left' | 'right';
  reviewUrl: string;
  anchors: DiffAnchor[];
  activeAnchorId: string | null;
  onSelectAnchor: (anchorId: string) => void;
}

function escapeSelector(value: string): string {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
    return CSS.escape(value);
  }
  return value.replace(/([#.;?+*~':"!^$\[\]()=>|/@])/g, '\\$1');
}

function collectWordFragments(anchor: DiffAnchor, side: 'left' | 'right'): WordHighlightFragment[] {
  return (side === 'left' ? anchor.left_fragments : anchor.right_fragments).filter(
    (fragment): fragment is WordHighlightFragment => fragment.kind === 'word',
  );
}

function wrapTextRange(element: HTMLElement, fragment: WordHighlightFragment, active: boolean, anchorId: string): void {
  if (fragment.char_start === 0 && fragment.char_end === 0) {
    element.classList.add('word-node-highlight');
    if (active) {
      element.classList.add('word-node-highlight-active');
    }
    element.dataset.anchorId = anchorId;
    return;
  }

  const textNode = element.firstChild;
  if (!textNode || textNode.nodeType !== Node.TEXT_NODE) {
    element.classList.add('word-node-highlight');
    if (active) {
      element.classList.add('word-node-highlight-active');
    }
    element.dataset.anchorId = anchorId;
    return;
  }

  const textLength = textNode.textContent?.length ?? 0;
  const start = Math.max(0, Math.min(fragment.char_start, textLength));
  const end = Math.max(start, Math.min(fragment.char_end, textLength));
  if (start === end) {
    element.classList.add('word-node-highlight');
    if (active) {
      element.classList.add('word-node-highlight-active');
    }
    element.dataset.anchorId = anchorId;
    return;
  }

  const range = document.createRange();
  range.setStart(textNode, start);
  range.setEnd(textNode, end);
  const mark = document.createElement('mark');
  mark.className = active ? 'word-inline-highlight word-inline-highlight-active' : 'word-inline-highlight';
  mark.dataset.anchorId = anchorId;
  range.surroundContents(mark);
  element.dataset.anchorId = anchorId;
}

export function HtmlPane({ title, side, reviewUrl, anchors, activeAnchorId, onSelectAnchor }: HtmlPaneProps) {
  const paneRef = useRef<HTMLDivElement | null>(null);
  const [html, setHtml] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void fetch(reviewUrl)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Failed to load review HTML (${response.status}).`);
        }
        return response.text();
      })
      .then((nextHtml) => {
        if (active) {
          setHtml(nextHtml);
          setLoading(false);
        }
      })
      .catch((nextError) => {
        if (active) {
          setError(nextError instanceof Error ? nextError.message : 'Failed to load review HTML.');
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [reviewUrl]);

  const wordAnchors = useMemo(
    () => anchors.map((anchor) => ({ anchor, fragments: collectWordFragments(anchor, side) })).filter((entry) => entry.fragments.length > 0),
    [anchors, side],
  );

  useEffect(() => {
    const pane = paneRef.current;
    if (!pane || !html) {
      return;
    }

    pane.innerHTML = html;
    wordAnchors.forEach(({ anchor, fragments }) => {
      fragments.forEach((fragment) => {
        const element = pane.querySelector<HTMLElement>(`[data-dom-id="${escapeSelector(fragment.dom_id)}"]`);
        if (!element) {
          return;
        }
        element.classList.add('word-node-hit');
        if (!element.dataset.anchorId) {
          element.dataset.anchorId = anchor.id;
        }
        if (anchor.id === activeAnchorId) {
          wrapTextRange(element, fragment, true, anchor.id);
        }
      });
    });

    const activeEntry = wordAnchors.find(({ anchor }) => anchor.id === activeAnchorId);
    const targetDomId = activeEntry?.fragments[0]?.dom_id;
    if (targetDomId) {
      pane.querySelector<HTMLElement>(`[data-dom-id="${escapeSelector(targetDomId)}"]`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    }
  }, [activeAnchorId, html, wordAnchors]);

  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) {
      return;
    }
    const handleClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      const anchorElement = target?.closest<HTMLElement>('[data-anchor-id]');
      const anchorId = anchorElement?.dataset.anchorId;
      if (anchorId) {
        onSelectAnchor(anchorId);
      }
    };
    pane.addEventListener('click', handleClick);
    return () => {
      pane.removeEventListener('click', handleClick);
    };
  }, [onSelectAnchor]);

  return (
    <section className="flex h-[calc(100vh-8rem)] flex-col rounded-[32px] border border-slate-200/70 bg-white/80 p-4 shadow-xl shadow-slate-200/50 backdrop-blur">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">{title}</p>
          <h2 className="mt-1 text-lg font-semibold text-slate-900">{side === 'left' ? 'Source Word' : 'Modified Word'}</h2>
        </div>
        <span className="rounded-full border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600">Structured review</span>
      </div>
      <div className="word-document overflow-y-auto rounded-[24px] bg-slate-100/70 p-4">
        {loading ? <PaneState label="Loading Word review…" /> : null}
        {error ? <PaneState label={error} /> : null}
        {!loading && !error ? <div ref={paneRef} className="word-pane" /> : null}
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