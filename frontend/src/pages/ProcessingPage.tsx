import type { JobStatus } from '../types/api';

interface ProcessingPageProps {
  jobStatus: JobStatus;
}

const STAGE_LABEL: Record<string, string> = {
  uploaded: 'Upload complete',
  extracting: 'Extracting text and coordinates',
  aligning: 'Running line-anchor patience diff and local text diff',
  projecting: 'Projecting diff results back onto PDF coordinates',
  done: 'Review workspace ready',
  failed: 'Job failed',
};

export function ProcessingPage({ jobStatus }: ProcessingPageProps) {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <section className="w-full max-w-2xl rounded-[40px] border border-white/60 bg-white/85 p-8 shadow-2xl shadow-slate-200/60 backdrop-blur">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">Background Job</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
          {STAGE_LABEL[jobStatus.stage] ?? 'Processing'}
        </h1>
        <p className="mt-3 text-base leading-7 text-slate-600">
          The backend is coarse-aligning extracted lines, running local word and character diffs
          only inside changed windows, and projecting the results back into page coordinates.
        </p>
        <div className="mt-8 h-3 overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full bg-slate-950 transition-all"
            style={{ width: `${Math.max(jobStatus.progress, 4)}%` }}
          />
        </div>
        <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
          <span>{jobStatus.stage}</span>
          <span>{jobStatus.progress}%</span>
        </div>
        {jobStatus.error ? (
          <div className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {jobStatus.error}
          </div>
        ) : null}
      </section>
    </main>
  );
}
