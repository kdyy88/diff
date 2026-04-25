import { useEffect, useMemo, useRef, useState } from 'react';

import {
  ApiError,
  createChapterAnalysis,
  createJob,
  getChapterAnalysisResult,
  getChapterAnalysisStatus,
  getFeatures,
  getJobResult,
  getJobStatus,
} from './api/client';
import { ChapterConfirmPage } from './pages/ChapterConfirmPage';
import { ChapterProcessingPage } from './pages/ChapterProcessingPage';
import { ProcessingPage } from './pages/ProcessingPage';
import { ReviewPage } from './pages/ReviewPage';
import { UploadPage } from './pages/UploadPage';
import type { ChapterAnalysisResult, ChapterAnalysisStatus, DiffResult, FeatureFlags, JobStatus } from './types/api';

type ViewState = 'upload' | 'chapter-processing' | 'chapter-confirm' | 'processing' | 'review';

interface SubmitInput {
  sourceFile: File;
  modifiedFile: File;
  headerMargin: number;
  footerMargin: number;
  enableChapterSplit: boolean;
}

function filterVisibleAnchors(
  anchors: DiffResult['anchors'],
  activeChapterId: string | null,
  showReflow: boolean,
) {
  return anchors.filter((anchor) => {
    const matchesChapter = activeChapterId ? anchor.chapter_id === activeChapterId : true;
    const matchesReflow = showReflow || anchor.kind !== 'reflow';
    return matchesChapter && matchesReflow;
  });
}

export default function App() {
  const [features, setFeatures] = useState<FeatureFlags>({ chapterSplit: false });
  const [viewState, setViewState] = useState<ViewState>('upload');
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [analysisStatus, setAnalysisStatus] = useState<ChapterAnalysisStatus | null>(null);
  const [analysisResult, setAnalysisResult] = useState<ChapterAnalysisResult | null>(null);
  const [result, setResult] = useState<DiffResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [showReflow, setShowReflow] = useState(false);
  const [enableChapterSplit, setEnableChapterSplit] = useState(true);
  const [activeAnchorId, setActiveAnchorId] = useState<string | null>(null);
  const [activeChapterId, setActiveChapterId] = useState<string | null>(null);
  const [pendingSubmit, setPendingSubmit] = useState<SubmitInput | null>(null);
  const fallbackStartedRef = useRef(false);

  useEffect(() => {
    let active = true;
    void getFeatures()
      .then((nextFeatures) => {
        if (active) {
          setFeatures(nextFeatures);
          setEnableChapterSplit(nextFeatures.chapterSplit);
        }
      })
      .catch(() => {
        if (active) {
          setFeatures({ chapterSplit: false });
          setEnableChapterSplit(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const resetToUpload = (message: string) => {
    setViewState('upload');
    setJobId(null);
    setJobStatus(null);
    setAnalysisId(null);
    setAnalysisStatus(null);
    setAnalysisResult(null);
    setResult(null);
    setActiveAnchorId(null);
    setActiveChapterId(null);
    setPendingSubmit(null);
    setError(message);
  };

  const startFullDocumentJob = async (input: SubmitInput) => {
    const response = await createJob({
      sourceFile: input.sourceFile,
      modifiedFile: input.modifiedFile,
      headerMargin: input.headerMargin,
      footerMargin: input.footerMargin,
      showReflow: input.sourceFile.name.toLowerCase().endsWith('.pdf'),
    });
    setPendingSubmit(null);
    setJobId(response.id);
    setJobStatus(null);
    setAnalysisId(null);
    setAnalysisStatus(null);
    setAnalysisResult(null);
    setViewState('processing');
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
          setShowReflow(nextResult.document_kind === 'pdf');
          setActiveChapterId(null);
          const firstVisible = nextResult.anchors.find((anchor) => anchor.kind !== 'reflow') ?? nextResult.anchors[0] ?? null;
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

  useEffect(() => {
    if (!analysisId || viewState !== 'chapter-processing') {
      return;
    }

    let active = true;
    fallbackStartedRef.current = false;
    const run = async () => {
      if (fallbackStartedRef.current) {
        return;
      }
      try {
        const status = await getChapterAnalysisStatus(analysisId);
        if (!active) {
          return;
        }
        setAnalysisStatus(status);
        if (status.status === 'done') {
          const nextResult = await getChapterAnalysisResult(analysisId);
          if (!active) {
            return;
          }
          setPendingSubmit(null);
          setAnalysisResult(nextResult);
          setViewState('chapter-confirm');
          return;
        }
        if (status.status === 'fallback') {
          if (!pendingSubmit) {
            resetToUpload(status.error ?? 'Chapter analysis could not continue, and the original upload is no longer available.');
            return;
          }
          fallbackStartedRef.current = true;
          try {
            await startFullDocumentJob(pendingSubmit);
          } catch (nextError) {
            if (active) {
              resetToUpload(nextError instanceof Error ? nextError.message : 'Failed to start fallback diff job.');
            }
          }
          return;
        }
        if (status.status === 'failed') {
          resetToUpload(status.error ?? 'Chapter analysis failed.');
        }
      } catch (nextError) {
        if (active) {
          resetToUpload(nextError instanceof Error ? nextError.message : 'Failed to poll chapter analysis.');
        }
      }
    };

    void run();
    const timer = window.setInterval(() => {
      void run();
    }, 1200);

    return () => {
      active = false;
      fallbackStartedRef.current = false;
      window.clearInterval(timer);
    };
  }, [analysisId, pendingSubmit, viewState]);

  const visibleAnchors = useMemo(() => {
    if (!result) {
      return [];
    }
    return filterVisibleAnchors(result.anchors, activeChapterId, showReflow);
  }, [activeChapterId, result, showReflow]);

  useEffect(() => {
    if (!visibleAnchors.length) {
      setActiveAnchorId(null);
      return;
    }
    if (!activeAnchorId || !visibleAnchors.some((anchor) => anchor.id === activeAnchorId)) {
      setActiveAnchorId(visibleAnchors[0].id);
    }
  }, [activeAnchorId, visibleAnchors]);

  const handleSubmit = async (input: SubmitInput) => {
    setSubmitting(true);
    setError(null);
    setResult(null);
    setJobStatus(null);
    setAnalysisStatus(null);
    setAnalysisResult(null);
    setPendingSubmit(input);
    try {
      if (features.chapterSplit && input.enableChapterSplit) {
        const response = await createChapterAnalysis({
          sourceFile: input.sourceFile,
          modifiedFile: input.modifiedFile,
          headerMargin: input.headerMargin,
          footerMargin: input.footerMargin,
          showReflow: input.sourceFile.name.toLowerCase().endsWith('.pdf'),
        });
        setAnalysisId(response.id);
        setViewState('chapter-processing');
      } else {
        await startFullDocumentJob(input);
      }
    } catch (nextError) {
      setPendingSubmit(null);
      setError(nextError instanceof Error ? nextError.message : 'Failed to create job.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleConfirmedChapterPlan = (response: { id: string }) => {
    setJobId(response.id);
    setJobStatus(null);
    setAnalysisStatus(null);
    setPendingSubmit(null);
    setViewState('processing');
  };

  const handleSelectChapter = (chapterId: string | null) => {
    setActiveChapterId(chapterId);
    if (!result) {
      return;
    }
    const chapterAnchors = filterVisibleAnchors(result.anchors, chapterId, showReflow);
    const chapterSummary = chapterId ? result.chapters.find((chapter) => chapter.id === chapterId) : null;
    setActiveAnchorId(chapterAnchors[0]?.id ?? chapterSummary?.first_anchor_id ?? null);
  };

  const moveAnchor = (direction: 1 | -1) => {
    if (!visibleAnchors.length) {
      return;
    }
    const currentIndex = visibleAnchors.findIndex((anchor) => anchor.id === activeAnchorId);
    const nextIndex = currentIndex < 0 ? 0 : (currentIndex + direction + visibleAnchors.length) % visibleAnchors.length;
    setActiveAnchorId(visibleAnchors[nextIndex].id);
  };

  if (viewState === 'chapter-processing' && analysisStatus) {
    return <ChapterProcessingPage analysisStatus={analysisStatus} />;
  }

  if (viewState === 'chapter-confirm' && analysisId && analysisResult) {
    return (
      <ChapterConfirmPage
        analysisId={analysisId}
        onCancel={() => resetToUpload('Chapter confirmation was cancelled.')}
        onConfirmed={handleConfirmedChapterPlan}
        result={analysisResult}
      />
    );
  }

  if (viewState === 'processing' && jobStatus) {
    return <ProcessingPage jobStatus={jobStatus} />;
  }

  if (viewState === 'review' && jobId && result) {
    return (
      <ReviewPage
        activeAnchorId={activeAnchorId}
        activeChapterId={activeChapterId}
        jobId={jobId}
        onNext={() => moveAnchor(1)}
        onPrevious={() => moveAnchor(-1)}
        onSelectAnchor={setActiveAnchorId}
        onSelectChapter={handleSelectChapter}
        onToggleReflow={() => setShowReflow((value) => !value)}
        result={result}
        showReflow={showReflow}
      />
    );
  }

  return (
    <UploadPage
      chapterFeatureEnabled={features.chapterSplit}
      enableChapterSplit={enableChapterSplit}
      error={error}
      onEnableChapterSplitChange={setEnableChapterSplit}
      onSubmit={handleSubmit}
      submitting={submitting}
    />
  );
}