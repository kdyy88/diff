import type { ChapterAnalysisStatus } from '../types/api';

interface ChapterProcessingPageProps {
  analysisStatus: ChapterAnalysisStatus;
}

export function ChapterProcessingPage({ analysisStatus }: ChapterProcessingPageProps) {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <section className="w-full max-w-2xl rounded-[40px] border border-white/60 bg-white/85 p-8 shadow-2xl shadow-slate-200/60 backdrop-blur">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">Chapter Analysis</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
          {analysisStatus.status === 'fallback'
            ? 'Switching to full-document diff'
            : analysisStatus.stage === 'done'
              ? 'Chapter plan ready'
              : 'Scanning document structure'}
        </h1>
        <p className="mt-3 text-base leading-7 text-slate-600">
          {analysisStatus.document_kind === 'pdf'
            ? 'The backend is checking whether both PDFs contain usable standard bookmarks. If either document does not, the app automatically falls back to the normal full-document diff flow.'
            : 'The backend is checking whether both DOCX files contain usable Heading 1 boundaries. If either document does not, the app automatically falls back to the normal full-document diff flow.'}
        </p>
        <div className="mt-8 h-3 overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full bg-slate-950 transition-all"
            style={{ width: `${Math.max(analysisStatus.progress, 4)}%` }}
          />
        </div>
        <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
          <span>{analysisStatus.stage}</span>
          <span>{analysisStatus.progress}%</span>
        </div>
        {analysisStatus.error ? (
          <div className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {analysisStatus.error}
          </div>
        ) : null}
      </section>
    </main>
  );
}