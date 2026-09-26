import type {
  DonorFullCard,
  DonorsPage,
  Forecast,
  RunCard,
  RunQueued,
  RunRequest,
  RunsView,
} from './types';
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

/** Страница истории прогонов, с единицы. Размер страницы называет сервер
 *  в ответе (`limit`): своей копии числа у экрана нет. */
export function listRuns(page: number): Promise<RunsView> {
  return request<RunsView>(`/runs?page=${page}`);
}

/** Прогоны, в которых кого-то приняли, — все, без страниц: из них
 *  собирается рассылка, и страница истории не должна их урезать. */
export function listRunsWithAccepted(): Promise<RunCard[]> {
  return request<RunCard[]>('/runs/with-accepted');
}

export interface DonorQuery {
  status?: string;
  search?: string;
  min_dr?: number;
  has_contact?: boolean;
  min_traffic?: number;
  geo?: string;
  freshness?: string;
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
