import { useState, type FormEvent } from 'react';

interface UploadPageProps {
  onSubmit: (input: {
    sourcePdf: File;
    modifiedPdf: File;
    headerMargin: number;
    footerMargin: number;
    enableChapterSplit: boolean;
  }) => Promise<void>;
  submitting: boolean;
  error: string | null;
  chapterFeatureEnabled: boolean;
  enableChapterSplit: boolean;
  onEnableChapterSplitChange: (value: boolean) => void;
}

export function UploadPage({
  onSubmit,
  submitting,
  error,
  chapterFeatureEnabled,
  enableChapterSplit,
  onEnableChapterSplitChange,
}: UploadPageProps) {
  const [sourcePdf, setSourcePdf] = useState<File | null>(null);
  const [modifiedPdf, setModifiedPdf] = useState<File | null>(null);
  const [headerMargin, setHeaderMargin] = useState(50);
  const [footerMargin, setFooterMargin] = useState(50);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!sourcePdf || !modifiedPdf) {
      return;
    }
    await onSubmit({
      sourcePdf,
      modifiedPdf,
      headerMargin,
      footerMargin,
      enableChapterSplit,
    });
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-6xl items-center px-6 py-12">
      <div className="grid w-full gap-8 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="rounded-[40px] border border-white/60 bg-white/80 p-8 shadow-2xl shadow-slate-200/60 backdrop-blur">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-emerald-700">Text Flow Reconstruction</p>
          <h1 className="mt-4 text-4xl font-semibold tracking-tight text-slate-950">Audit-grade PDF diff for pagination reflow</h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600">
            Upload the source and modified PDFs, reconstruct each document as a continuous text stream,
            compute a character-level diff, then project the changes back onto the original pages for review.
          </p>
          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            <Metric title="Non-linear aware" value="Across pages" />
            <Metric title="Diff precision" value="Character-level" />
            <Metric title="Reviewer focus" value={chapterFeatureEnabled ? 'Whole book or by chapter' : 'Anchor-driven'} />
          </div>
        </section>

        <section className="rounded-[40px] border border-slate-200/70 bg-white/90 p-8 shadow-2xl shadow-slate-200/60 backdrop-blur">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Create Job</p>
            <h2 className="mt-2 text-2xl font-semibold text-slate-950">Start a new comparison</h2>
          </div>
          <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
            <FileField label="Source PDF" onChange={(file) => setSourcePdf(file)} selectedFile={sourcePdf} />
            <FileField label="Modified PDF" onChange={(file) => setModifiedPdf(file)} selectedFile={modifiedPdf} />
            <div className="grid gap-4 sm:grid-cols-2">
              <NumberField label="Header margin (pt)" value={headerMargin} onChange={setHeaderMargin} />
              <NumberField label="Footer margin (pt)" value={footerMargin} onChange={setFooterMargin} />
            </div>
            {chapterFeatureEnabled ? (
              <label className="flex items-start gap-3 rounded-[24px] border border-slate-200 bg-slate-50/80 px-4 py-4">
                <input
                  checked={enableChapterSplit}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-slate-950 focus:ring-slate-400"
                  onChange={(event) => onEnableChapterSplitChange(event.target.checked)}
                  type="checkbox"
                />
                <div>
                  <p className="text-sm font-semibold text-slate-900">Compare chapter by chapter</p>
                  <p className="mt-1 text-sm leading-6 text-slate-600">
                    Run bookmark-based chapter analysis first, review the detected outline, then diff each matched chapter.
                  </p>
                </div>
              </label>
            ) : null}
            {error ? (
              <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                {error}
              </div>
            ) : null}
            <button
              className="w-full rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white shadow-lg shadow-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
              disabled={!sourcePdf || !modifiedPdf || submitting}
              type="submit"
            >
              {submitting ? 'Submitting…' : chapterFeatureEnabled && enableChapterSplit ? 'Analyze chapters' : 'Run diff job'}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}

function Metric({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-[28px] border border-slate-200/80 bg-slate-50/80 p-5">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">{title}</p>
      <p className="mt-2 text-lg font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function FileField({
  label,
  selectedFile,
  onChange,
}: {
  label: string;
  selectedFile: File | null;
  onChange: (file: File | null) => void;
}) {
  return (
    <label className="block rounded-[28px] border border-dashed border-slate-300 bg-slate-50/70 p-5">
      <p className="text-sm font-semibold text-slate-800">{label}</p>
      <input
        accept="application/pdf"
        className="mt-4 block w-full text-sm text-slate-600 file:mr-4 file:rounded-full file:border-0 file:bg-slate-950 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white hover:file:bg-slate-800"
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        type="file"
      />
      <p className="mt-3 text-sm text-slate-500">{selectedFile?.name ?? 'No file selected yet.'}</p>
    </label>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block rounded-[24px] border border-slate-200 bg-white px-4 py-3">
      <p className="text-sm font-semibold text-slate-800">{label}</p>
      <input
        className="mt-3 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900 outline-none ring-0 transition focus:border-slate-400"
        min={0}
        onChange={(event) => onChange(Number(event.target.value))}
        type="number"
        value={value}
      />
    </label>
  );
}