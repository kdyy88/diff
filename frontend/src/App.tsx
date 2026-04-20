import { useEffect, useMemo, useState } from 'react';

import { ApiError, createJob, getJobResult, getJobStatus } from './api/client';
import { ProcessingPage } from './pages/ProcessingPage';
import { ReviewPage } from './pages/ReviewPage';
import { UploadPage } from './pages/UploadPage';
import type { DiffResult, JobStatus } from './types/api';

type ViewState = 'upload' | 'processing' | 'review';

export default function App() {
  const [viewState, setViewState] = useState<ViewState>('upload');
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [result, setResult] = useState<DiffResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [showReflow, setShowReflow] = useState(false);
  const [activeAnchorId, setActiveAnchorId] = useState<string | null>(null);

  const resetToUpload = (message: string) => {
    setViewState('upload');
    setJobId(null);
    setJobStatus(null);
    setResult(null);
    setActiveAnchorId(null);
    setError(message);
  };

  useEffect(() => {
    if (!jobId || viewState !== 'processing') {
      return;
    }

    let active = true;
    const run = async () => {
      try {
        const status = await getJobStatus(jobId);
        if (!active) {
          return;
        }
        setJobStatus(status);
        if (status.status === 'done') {
          const nextResult = await getJobResult(jobId);
          if (!active) {
            return;
          }
          setResult(nextResult);
          const firstVisible =
            nextResult.anchors.find((anchor) => anchor.kind !== 'reflow') ?? nextResult.anchors[0] ?? null;
          setActiveAnchorId(firstVisible?.id ?? null);
          setViewState('review');
          return;
        }
        if (status.status === 'failed') {
          resetToUpload(status.error ?? 'Job failed.');
        }
      } catch (nextError) {
        if (active) {
          if (nextError instanceof ApiError && nextError.status === 404) {
            resetToUpload(
              'The background job is no longer available. This usually happens after the local backend reloads or restarts. Please submit the PDFs again.',
            );
            return;
          }
          resetToUpload(nextError instanceof Error ? nextError.message : 'Failed to poll job.');
        }
      }
    };

    void run();
    const timer = window.setInterval(() => {
      void run();
    }, 1200);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [jobId, viewState]);

  const visibleAnchors = useMemo(
    () => result?.anchors.filter((anchor) => showReflow || anchor.kind !== 'reflow') ?? [],
    [result, showReflow],
  );

  useEffect(() => {
    if (!visibleAnchors.length) {
      setActiveAnchorId(null);
      return;
    }
    if (!activeAnchorId || !visibleAnchors.some((anchor) => anchor.id === activeAnchorId)) {
      setActiveAnchorId(visibleAnchors[0].id);
    }
  }, [activeAnchorId, visibleAnchors]);

  const handleSubmit = async (input: {
    sourcePdf: File;
    modifiedPdf: File;
    headerMargin: number;
    footerMargin: number;
  }) => {
    setSubmitting(true);
    setError(null);
    setResult(null);
    setJobStatus(null);
    try {
      const response = await createJob({
        ...input,
        showReflow: true,
      });
      setJobId(response.id);
      setViewState('processing');
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Failed to create job.');
    } finally {
      setSubmitting(false);
    }
  };

  const moveAnchor = (direction: 1 | -1) => {
    if (!visibleAnchors.length) {
      return;
    }
    const currentIndex = visibleAnchors.findIndex((anchor) => anchor.id === activeAnchorId);
    const nextIndex = currentIndex < 0 ? 0 : (currentIndex + direction + visibleAnchors.length) % visibleAnchors.length;
    setActiveAnchorId(visibleAnchors[nextIndex].id);
  };

  if (viewState === 'processing' && jobStatus) {
    return <ProcessingPage jobStatus={jobStatus} />;
  }

  if (viewState === 'review' && jobId && result) {
    return (
      <ReviewPage
        activeAnchorId={activeAnchorId}
        jobId={jobId}
        onNext={() => moveAnchor(1)}
        onPrevious={() => moveAnchor(-1)}
        onSelectAnchor={setActiveAnchorId}
        onToggleReflow={() => setShowReflow((value) => !value)}
        result={result}
        showReflow={showReflow}
      />
    );
  }

  return <UploadPage error={error} onSubmit={handleSubmit} submitting={submitting} />;
}
