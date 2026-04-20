import type { CreateJobResponse, DiffResult, JobStatus } from '../types/api';

const API_BASE = 'http://localhost:8000/api';

export interface CreateJobInput {
  sourcePdf: File;
  modifiedPdf: File;
  headerMargin: number;
  footerMargin: number;
  showReflow: boolean;
}

export async function createJob(input: CreateJobInput): Promise<CreateJobResponse> {
  const formData = new FormData();
  formData.append('sourcePdf', input.sourcePdf);
  formData.append('modifiedPdf', input.modifiedPdf);
  formData.append('headerMargin', String(input.headerMargin));
  formData.append('footerMargin', String(input.footerMargin));
  formData.append('showReflow', String(input.showReflow));

  const response = await fetch(`${API_BASE}/jobs`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    throw new Error('Failed to create diff job.');
  }

  return (await response.json()) as CreateJobResponse;
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}`);
  if (!response.ok) {
    throw new Error('Failed to fetch job status.');
  }
  return (await response.json()) as JobStatus;
}

export async function getJobResult(jobId: string): Promise<DiffResult> {
  const response = await fetch(`${API_BASE}/jobs/${jobId}/result`);
  if (!response.ok) {
    throw new Error('Failed to fetch job result.');
  }
  return (await response.json()) as DiffResult;
}

export function buildPdfUrl(jobId: string, side: 'source' | 'modified'): string {
  return `${API_BASE}/jobs/${jobId}/files/${side}`;
}
