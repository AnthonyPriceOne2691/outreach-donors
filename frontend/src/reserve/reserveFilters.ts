/**
 * Фильтры резерва — в адресе (`useUrlFilters`): прогон, страна прогона, группа причины,
 * страна в топ-5 домена и страница. Ссылка «резерв прогона №30» из отчёта и очереди —
 * тот же экран с `?run=30`.
 */

import { RESERVE_GROUPS } from './reserveApi';
import type { ReserveGroup, ReserveQuery } from './reserveApi';

export type ReserveFilters = ReserveQuery;

export const NO_FILTERS: ReserveFilters = {
  run: null,
  country: null,
  group: null,
  geo: null,
  page: 1,
};

/** Номер из адреса — только целое больше нуля: «abc» и «-1» фильтром не становятся. */
function positive(text: string | null): number | null {
  const value = Number(text);
  return Number.isInteger(value) && value >= 1 ? value : null;
}

/** Код страны — две латинские буквы строчными; прочее — не фильтр. */
function country(text: string | null): string | null {
  const code = (text ?? '').trim().toLowerCase();
  return /^[a-z]{2}$/.test(code) ? code : null;
}

export function readReserve(params: URLSearchParams): ReserveFilters {
  const group = params.get('group');
  return {
    run: positive(params.get('run')),
    country: country(params.get('country')),
    group: RESERVE_GROUPS.find((one) => one === group) ?? null,
    geo: country(params.get('geo')),
    page: positive(params.get('page')) ?? 1,
  };
}

export function writeReserve(filters: ReserveFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.run !== null) params.set('run', String(filters.run));
  if (filters.country !== null) params.set('country', filters.country);
  if (filters.group !== null) params.set('group', filters.group);
  if (filters.geo !== null) params.set('geo', filters.geo);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

/** Сужен ли резерв чем-то, кроме страницы. */
export function isFiltered(filters: ReserveFilters): boolean {
  return (
    filters.run !== null ||
    filters.country !== null ||
    filters.group !== null ||
    filters.geo !== null
  );
}

/** Группы — порядком фильтра. */
export function groupOf(value: string | null): ReserveGroup | null {
  return RESERVE_GROUPS.find((one) => one === value) ?? null;
}
