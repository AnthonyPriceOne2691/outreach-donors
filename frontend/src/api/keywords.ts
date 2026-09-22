import type { KeywordPool, PoolRequest } from './types';
import { request } from './client';

export function fetchPresets(): Promise<string[]> {
  return request<string[]>('/keywords/presets');
}

/** Языки рынка. Оператор выбирает только страну — языки выводятся из неё:
 *  подставлять английский незнакомой стране значит увести прогон в другой
 *  веб, и увести молча. */
export function fetchMarketLanguages(country: string): Promise<string[]> {
  return request<string[]>(`/keywords/languages?country=${encodeURIComponent(country)}`);
}

export function buildPool(body: PoolRequest): Promise<KeywordPool> {
  return request<KeywordPool>('/keywords', { method: 'POST', body });
}
