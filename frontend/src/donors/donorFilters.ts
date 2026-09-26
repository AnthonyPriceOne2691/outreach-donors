/**
 * Фильтры таблицы доноров и страница — в адресе экрана.
 *
 * **Адрес, а не состояние компонента.** Человек уходит в карточку донора
 * и возвращается «назад» — и должен найти ту же страницу с теми же
 * фильтрами, а не первую страницу всей базы. То же при обновлении вкладки
 * и при ссылке, отправленной коллеге.
 *
 * **Адрес — чужой ввод.** Его правят руками и присылают устаревшим, поэтому
 * он разбирается строго: незнакомое значение — «не сужать», а не отказ
 * сервера и не пустой экран без объяснения.
 *
 * **Фильтр под каждой колонкой** (замечание 26.09.2026): к вердикту, DR,
 * адресу и поиску добавились трафик «не ниже», страна и срок метрик. Имена
 * параметров — те же, что у сервера (`DonorQuery`): одна строка параметров
 * у таблицы, у выгрузки и у «назад».
 */

import { countryTitle, DONOR_FRESHNESS, DONOR_STATUSES } from '../api/labels';
import { formatNumber } from '../format';
import type { DonorFilterQuery } from '../api/donors';
import type { DonorFreshness, DonorStatus, DonorsWaiting } from '../api/types';

/** Доноров на странице: замечание 25.09.2026 — «максимум 20 за раз». */
export const PAGE_SIZE = 20;

export interface DonorFilters {
  status: DonorStatus | null;
  /** По домену или причине отсева. Пусто — не сужать. */
  search: string;
  minDr: number | null;
  /** Органический трафик не ниже. Бывает до миллиардов. */
  minTraffic: number | null;
  /** Страна донора — код ISO-2 строчными. */
  geo: string | null;
  freshness: DonorFreshness | null;
  /** Найден ли адрес: `true` — есть, `false` — нет (в том числе не искали). */
  address: boolean | null;
  page: number;
}

export const NO_FILTERS: DonorFilters = {
  status: null,
  search: '',
  minDr: null,
  minTraffic: null,
  geo: null,
  freshness: null,
  address: null,
  page: 1,
};

const STATUSES = Object.keys(DONOR_STATUSES) as DonorStatus[];
export const FRESHNESS = Object.keys(DONOR_FRESHNESS) as DonorFreshness[];

/** Трафик «не ниже» — до тринадцати знаков: у самого большого сайта базы
 *  пять миллиардов, десять знаков. Длиннее — не порог, а опечатка. */
export const TRAFFIC_DIGITS = 13;

function wholeNumber(raw: string | null, min: number, max: number, digits = 6): number | null {
  if (raw === null || !new RegExp(`^\\d{1,${digits}}$`).test(raw)) return null;
  const value = Number(raw);
  return value >= min && value <= max ? value : null;
}

function countryCode(raw: string | null): string | null {
  return raw !== null && /^[a-z]{2}$/i.test(raw) ? raw.toLowerCase() : null;
}

export function readFilters(params: URLSearchParams): DonorFilters {
  const status = params.get('status');
  const address = params.get('has_contact');
  const freshness = params.get('freshness');
  return {
    status: STATUSES.find((known) => known === status) ?? null,
    search: (params.get('search') ?? '').trim(),
    minDr: wholeNumber(params.get('min_dr'), 0, 100),
    minTraffic: wholeNumber(params.get('min_traffic'), 0, Number.MAX_SAFE_INTEGER, TRAFFIC_DIGITS),
    geo: countryCode(params.get('geo')),
    freshness: FRESHNESS.find((known) => known === freshness) ?? null,
    address: address === 'true' ? true : address === 'false' ? false : null,
    page: wholeNumber(params.get('page'), 1, 999_999) ?? 1,
  };
}

/** Фильтры → запрос к серверу. Имена те же, что у адреса экрана: одна
 *  строка параметров и у таблицы, и у выгрузки, и у «назад». */
export function queryOf(filters: DonorFilters): DonorFilterQuery {
  return {
    ...(filters.status !== null ? { status: filters.status } : {}),
    ...(filters.search !== '' ? { search: filters.search } : {}),
    ...(filters.minDr !== null ? { min_dr: filters.minDr } : {}),
    ...(filters.minTraffic !== null ? { min_traffic: filters.minTraffic } : {}),
    ...(filters.geo !== null ? { geo: filters.geo } : {}),
    ...(filters.freshness !== null ? { freshness: filters.freshness } : {}),
    ...(filters.address !== null ? { has_contact: filters.address } : {}),
  };
}

/** Фильтры → адрес. Первая страница не пишется: `?page=1` ничего не сужает. */
export function writeFilters(filters: DonorFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(queryOf(filters))) {
    params.set(key, String(value));
  }
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

export function isFiltered(filters: DonorFilters): boolean {
  return Object.keys(queryOf(filters)).length > 0;
}

/** Что сужает таблицу — словами. Для пустого результата: «ничего не нашлось»
 *  без условий читается как «доноров нет». */
export function conditionsOf(filters: DonorFilters): string[] {
  const said: string[] = [];
  if (filters.status !== null) said.push(`вердикт «${DONOR_STATUSES[filters.status].title}»`);
  if (filters.search !== '') said.push(`«${filters.search}» в домене или причине отсева`);
  if (filters.minDr !== null) said.push(`DR не ниже ${filters.minDr}`);
  if (filters.minTraffic !== null) said.push(`трафик не ниже ${formatNumber(filters.minTraffic)}`);
  if (filters.geo !== null) said.push(`страна «${countryTitle(filters.geo)}»`);
  if (filters.freshness !== null) {
    said.push(`данные «${DONOR_FRESHNESS[filters.freshness].title}»`);
  }
  if (filters.address === true) said.push('адрес найден');
  if (filters.address === false) said.push('адреса нет');
  return said;
}

/** Всего доноров по сводке сервера: она считается по всем донорам, а не по фильтру. */
export function totalOf(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, count) => sum + count, 0);
}

export interface Emptiness {
  title: string;
  detail: string;
  /** Есть ли что сбросить: без фильтров кнопка ничего бы не сделала. */
  resettable: boolean;
  /** Куда идти, чтобы доноры появились. */
  path?: { label: string; to: string };
}

/**
 * Доноров нет вовсе — и почему, и куда идти.
 *
 * Донор — домен, принятый человеком на рассмотрении прогона (решение
 * 26.09.2026). «Доноров в базе нет» было бы неправдой: проверенных доменов
 * в базе могут быть сотни, они ждут решения в очередях прогонов.
 */
function noDonors(waiting: DonorsWaiting, resettable: boolean): Emptiness {
  const detail =
    'Донором домен становится, когда его принимает человек на рассмотрении прогона: ' +
    'прогон кончается очередью кандидатов, а не списком доноров.';
  const newest = waiting.runs[0];
  if (waiting.domains === 0 || newest === undefined) {
    return {
      title: 'Доноров пока нет.',
      detail: `${detail} Кандидатов, ждущих решения, сейчас нет — их приносит прогон.`,
      resettable,
      path: { label: 'К прогонам', to: '/run' },
    };
  }
  return {
    title: 'Доноров пока нет.',
    detail: `${detail} Ждут решения: ${formatNumber(waiting.domains)}.`,
    resettable,
    path: { label: `Рассмотреть · ${formatNumber(waiting.domains)}`, to: `/runs/${newest}/review` },
  };
}

/**
 * Почему под фильтром пусто — словами.
 *
 * Пустой экран объясняет, чего на нём нет и почему. Три случая читаются
 * по-разному, и путать их нельзя: доноров нет вовсе — ведём туда, где их
 * принимают; вердикта нет ни у одного донора — сужать дальше бессмысленно;
 * пусто только под сочетанием условий — условия и названы.
 */
export function emptinessOf(
  filters: DonorFilters,
  counts: Record<string, number>,
  waiting: DonorsWaiting,
): Emptiness {
  const all = totalOf(counts);
  const resettable = isFiltered(filters);
  if (all === 0) return noDonors(waiting, resettable);
  if (filters.status !== null && (counts[filters.status] ?? 0) === 0) {
    return {
      title: `С вердиктом «${DONOR_STATUSES[filters.status].title}» доноров нет.`,
      detail: `Нет ни у одного донора, а не только под остальными условиями. Всего доноров — ${formatNumber(all)}.`,
      resettable,
    };
  }
  const conditions = conditionsOf(filters);
  return {
    title: 'Под фильтр ничего не попало.',
    detail:
      conditions.length === 0
        ? `На этой странице пусто. Всего доноров — ${formatNumber(all)}.`
        : `Условия: ${conditions.join(', ')}. Всего доноров — ${formatNumber(all)}.`,
    resettable,
  };
}
