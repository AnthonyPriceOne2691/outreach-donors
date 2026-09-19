import type { DonorFullCard, DonorsPage, Forecast, RunCard, RunQueued, RunRequest } from './types';
import { request } from './client';

export function fetchCountries(): Promise<string[]> {
  return request<string[]>('/runs/countries');
}

export function estimateRun(body: RunRequest): Promise<Forecast> {
  return request<Forecast>('/runs/estimate', { method: 'POST', body });
}

export function startRun(body: RunRequest): Promise<RunQueued> {
  return request<RunQueued>('/runs', { method: 'POST', body });
}

export function listRuns(): Promise<RunCard[]> {
  return request<RunCard[]>('/runs');
}

export interface DonorQuery {
  status?: string;
  search?: string;
  min_dr?: number;
  has_contact?: boolean;
  limit?: number;
  offset?: number;
}

export function listDonors(query: DonorQuery = {}): Promise<DonorsPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value));
  }
  const tail = params.toString();
  return request<DonorsPage>(`/donors${tail === '' ? '' : `?${tail}`}`);
}

export function fetchDonor(id: number): Promise<DonorFullCard> {
  return request<DonorFullCard>(`/donors/${id}`);
}
