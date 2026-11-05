/**
 * Что смотрит очередь проверки — список, решённые, фильтры «Балл» и «За что», страница — в адресе
 * экрана, по общему правилу списков (`components/useUrlFilters.ts`): обновление вкладки, «назад» и
 * ссылка коллеге показывают ту же страницу того же списка. Страница без списка в адресе показала
 * бы после обновления чужую.
 *
 * Адрес — чужой ввод: незнакомое значение не сужает. Варианты фильтров называет сервер (`facets`
 * в ответе очереди, `crawl/review.py`), и своей копии ключей признаков здесь нет: признак, которого
 * нет, — пустой список со «Сбросить фильтры». Смена списка фильтры снимает: баллы спорных и
 * «куплено» не пересекаются.
 *
 * Имена параметров — те же, что у сервера (`ReviewQuery`). На «Проверке» адрес один на две
 * карточки, и страница бизнесов ниши едет рядом: `write` собирает адрес заново и не должен её терять.
 */

import type { QueueFacets, ReviewedVerdict, ReviewQuery } from '../api/advertisers';
import type { Emptiness } from '../components/WholeRow';
import { pageParam } from '../components/useUrlFilters';
import { formatNumber } from '../format';

/** Имя страницы бизнесов ниши в адресе: листает своя карточка (`NicheQueue`). */
export const NICHE_PAGE_PARAM = 'niche_page';

export interface ReviewFilters {
  verdict: ReviewedVerdict;
  includeDecided: boolean;
  /** Балл — точным значением. Пусто — не сужать. */
  points: string | null;
  /** Признак скоринга — ключом сервера. Пусто — не сужать. */
  feature: string | null;
  page: number;
  nichePage: number;
}

const POINTS = /^\d{1,4}$/;
const FEATURE = /^[a-z0-9_]{1,64}$/;

/** Значение из адреса, если оно похоже на своё; иначе — «не сужать». */
function shaped(raw: string | null, shape: RegExp): string | null {
  return raw !== null && shape.test(raw) ? raw : null;
}

export function readReviewFilters(params: URLSearchParams): ReviewFilters {
  return {
    verdict: params.get('verdict') === 'bought' ? 'bought' : 'pending',
    includeDecided: params.get('include_decided') === 'true',
    points: shaped(params.get('points'), POINTS),
    feature: shaped(params.get('feature'), FEATURE),
    page: pageParam(params.get('page')),
    nichePage: pageParam(params.get(NICHE_PAGE_PARAM)),
  };
}

/** Фильтры → запрос к серверу, без страницы. Умолчания в запрос не идут. */
export function reviewQueryOf(filters: ReviewFilters): ReviewQuery {
  return {
    ...(filters.includeDecided ? { include_decided: 'true' as const } : {}),
    ...(filters.verdict !== 'pending' ? { verdict: filters.verdict } : {}),
    ...(filters.points !== null ? { points: filters.points } : {}),
    ...(filters.feature !== null ? { feature: filters.feature } : {}),
  };
}

/** Фильтры → адрес экрана: вид проверки, условия, страницы (первая — без номера). */
export function writeReviewFilters(filters: ReviewFilters): URLSearchParams {
  const params = new URLSearchParams({ view: 'review' });
  for (const [key, value] of Object.entries(reviewQueryOf(filters))) {
    params.set(key, String(value));
  }
  if (filters.page > 1) params.set('page', String(filters.page));
  if (filters.nichePage > 1) params.set(NICHE_PAGE_PARAM, String(filters.nichePage));
  return params;
}

export function isReviewFiltered(filters: ReviewFilters): boolean {
  return filters.points !== null || filters.feature !== null;
}

/** Почему под фильтром пусто — словами: «ничего не нашлось» без условий читается как «нет никого». */
export function reviewEmptiness(filters: ReviewFilters, facets: QueueFacets): Emptiness {
  const feature = facets.features.choices.find((choice) => choice.key === filters.feature);
  const said = [
    ...(filters.points !== null ? [`балл ${filters.points}`] : []),
    ...(filters.feature !== null ? [`за что: ${feature?.title ?? filters.feature}`] : []),
  ];
  return {
    title: 'Под фильтр ничего не попало.',
    detail: `Условия: ${said.join(', ')}. Всего в списке — ${formatNumber(facets.listed)}.`,
    resettable: true,
  };
}
