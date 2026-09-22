import type { KeywordPool, PoolRequest } from './types';
import { request } from './client';

export function fetchPresets(): Promise<string[]> {
  return request<string[]>('/keywords/presets');
}

export function buildPool(body: PoolRequest): Promise<KeywordPool> {
  return request<KeywordPool>('/keywords', { method: 'POST', body });
}
