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
 *
 * **Цена, решение человека и порядок** (сверка ТЗ 12.10.2026): фильтр под «Ценой»,
 * «Решение» — кто на экране (без него — доноры, как было) — и порядок не только
 * по DR. Решение и порядок — не сужение: «найдено N из M» и «Выгрузить найденных»
 * их не считают, а в адрес они пишутся, только когда не умолчание.
 */

import { countryTitle, DONOR_FRESHNESS, DONOR_STATUSES } from '../api/labels';
import { numberOf, numberRefusal, numberText } from '../components/numberText';
import { formatNumber } from '../format';
import type { DonorFilterQuery } from '../api/donors';
import type { DonorSort, PriceState, ReviewScope } from '../api/donorPrice';
import type { DonorFreshness, DonorStatus, DonorsWaiting } from '../api/types';
import { DONOR_SORTS, PRICE_STATES, REVIEW_SCOPES } from './priceWords';

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
  /** Есть ли цена и в сроке ли. */
  price: PriceState | null;
  /** Решение человека — кто на экране; умолчание — доноры. */
  review: ReviewScope;
  sort: DonorSort;
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
  price: null,
  review: 'accepted',
  sort: 'dr',
  page: 1,
};

const STATUSES = Object.keys(DONOR_STATUSES) as DonorStatus[];
export const FRESHNESS = Object.keys(DONOR_FRESHNESS) as DonorFreshness[];
export const PRICES = Object.keys(PRICE_STATES) as PriceState[];
export const REVIEWS = Object.keys(REVIEW_SCOPES) as ReviewScope[];
export const SORTS = Object.keys(DONOR_SORTS) as DonorSort[];

/** Трафик «не ниже» — до тринадцати знаков: у самого большого сайта базы
 *  пять миллиардов, десять знаков. Длиннее — не порог, а опечатка. */
export const TRAFFIC_DIGITS = 13;

/** Верх порогов — тот же, что у разбора адреса (`readFilters`). */
export const DR_MAX = 100;
export const TRAFFIC_MAX = 10 ** TRAFFIC_DIGITS - 1;

/** Порог в поле — строкой, разряды пробелом, как на экране; нет порога — пусто. */
export function thresholdText(value: number | null): string {
  return value === null ? '' : numberText(value);
}

/**
 * Почему набранный порог не фильтр — коротко: поле узкое, в шапке колонки.
 * Числовое поле молча выбрасывало точку, и «1.5» становилось фильтром «не ниже
 * 15» (проверка QA 10.10.2026). Пусто — не отказ, а «не сужать».
 */
export function thresholdRefusal(text: string, max: number): string | null {
  const refusal = numberRefusal(text, { decimals: 0, min: 0 });
  if (refusal !== null) return refusal;
  const value = numberOf(text);
  return value !== null && value > max ? `Не больше ${formatNumber(max)}` : null;
}

/** Что набранный порог даёт фильтру: число; `null` — пусто, не сужать;
 *  `undefined` — набрано не число, и фильтр остаётся прежним. */
export function thresholdOf(text: string, max: number): number | null | undefined {
  if (text.trim() === '') return null;
  return thresholdRefusal(text, max) === null ? (numberOf(text) ?? undefined) : undefined;
}

function wholeNumber(raw: string | null, min: number, max: number, digits = 6): number | null {
  if (raw === null || !new RegExp(`^\\d{1,${digits}}$`).test(raw)) return null;
  const value = Number(raw);
  return value >= min && value <= max ? value : null;
}

function countryCode(raw: string | null): string | null {
  return raw !== null && /^[a-z]{2}$/i.test(raw) ? raw.toLowerCase() : null;
}

/** Известное значение из адреса — или `null`: незнакомое не сужает. */
function known<T extends string>(values: readonly T[], raw: string | null): T | null {
  return values.find((value) => value === raw) ?? null;
}

export function readFilters(params: URLSearchParams): DonorFilters {
  const address = params.get('has_contact');
  return {
    status: known(STATUSES, params.get('status')),
    search: (params.get('search') ?? '').trim(),
    minDr: wholeNumber(params.get('min_dr'), 0, DR_MAX),
    minTraffic: wholeNumber(params.get('min_traffic'), 0, Number.MAX_SAFE_INTEGER, TRAFFIC_DIGITS),
    geo: countryCode(params.get('geo')),
    freshness: known(FRESHNESS, params.get('freshness')),
    address: address === 'true' ? true : address === 'false' ? false : null,
    price: known(PRICES, params.get('price')),
    review: known(REVIEWS, params.get('review')) ?? 'accepted',
    sort: known(SORTS, params.get('sort')) ?? 'dr',
    page: wholeNumber(params.get('page'), 1, 999_999) ?? 1,
  };
}

/** Что сужает таблицу — без решения человека и порядка: они не сужают, а выбирают. */
function narrowing(filters: DonorFilters): DonorFilterQuery {
  return {
    ...(filters.status !== null ? { status: filters.status } : {}),
    ...(filters.search !== '' ? { search: filters.search } : {}),
    ...(filters.minDr !== null ? { min_dr: filters.minDr } : {}),
    ...(filters.minTraffic !== null ? { min_traffic: filters.minTraffic } : {}),
    ...(filters.geo !== null ? { geo: filters.geo } : {}),
    ...(filters.freshness !== null ? { freshness: filters.freshness } : {}),
    ...(filters.address !== null ? { has_contact: filters.address } : {}),
    ...(filters.price !== null ? { price: filters.price } : {}),
  };
}

/** Фильтры → запрос к серверу. Имена те же, что у адреса экрана: одна
 *  строка параметров и у таблицы, и у выгрузки, и у «назад». Решение и порядок —
 *  только не умолчанием: адрес прежнего экрана остаётся прежним. */
export function queryOf(filters: DonorFilters): DonorFilterQuery {
  return {
    ...narrowing(filters),
    ...(filters.review !== 'accepted' ? { review: filters.review } : {}),
    ...(filters.sort !== 'dr' ? { sort: filters.sort } : {}),
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
  return Object.keys(narrowing(filters)).length > 0;
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
  if (filters.price !== null) said.push(PRICE_STATES[filters.price].said);
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
  if (all === 0 && filters.review !== 'accepted') {
    // Не доноры, а другое решение: путь к рассмотрению тут ни при чём.
    return {
      title: REVIEW_SCOPES[filters.review].empty,
      detail: 'Решение человека выбрано над таблицей — «доноры» вернут принятых.',
      resettable,
    };
  }
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
