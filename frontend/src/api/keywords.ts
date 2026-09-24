import type { KeywordPool, KeywordYield, PoolRequest } from './types';
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

/** Ключи прошлых прогонов страны, дававшие принятых доноров. */
export function fetchKeywordYield(country: string): Promise<KeywordYield[]> {
  return request<KeywordYield[]>(`/keywords/yield?country=${encodeURIComponent(country)}`);
}

export function buildPool(body: PoolRequest): Promise<KeywordPool> {
  return request<KeywordPool>('/keywords', { method: 'POST', body });
}
