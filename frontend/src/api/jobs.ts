import type { JobCard } from './types';
import { request } from './client';

export function fetchJob(jobId: string): Promise<JobCard> {
  return request<JobCard>(`/jobs/${encodeURIComponent(jobId)}`);
}
