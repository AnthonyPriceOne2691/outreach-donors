/**
 * Резерв (`GET /review/reserve`, `POST /review/reserve/take`, сервер — `review/reserve.py`):
 * домены прогонов, которые не прошли, с причиной, и «Взять на рассмотрение».
 *
 * Типы и запросы — здесь, у экрана, а не в `api/`: их читает только резерв, а общий
 * `types.ts` у потолка длины.
 */

import { request } from '../api/client';
import type { ReviewDecision } from '../api/types';

/** Почему не прошёл — группой фильтра: слабые метрики, регион, «не проверен» из-за
 *  нехватки данных, «кому не пишем». */
export type ReserveGroup = 'weak' | 'region' | 'gap' | 'not_writing';

export const RESERVE_GROUPS: readonly ReserveGroup[] = ['weak', 'region', 'gap', 'not_writing'];

/** Страна прогона в топ-5 домена: место и доля, верхняя страна и её доля. */
export interface CountryFit {
  place: number | null;
  share: number | null;
  top: string | null;
  top_share: number | null;
  /** Страны спрашивали: без них места нет, потому что не знаем. */
  asked: boolean;
  /** В разбивке одна верхняя страна — остальные не запрашивались. */
  partial: boolean;
}

export interface ReserveCard {
  run_id: number;
  /** Страна прогона: вердикт вынесен для неё. */
  country: string;
  domain_id: number;
  /** Донор домена — карточка, куда ведёт имя; `null` — гейт снял домен до Ahrefs. */
  donor_id: number | null;
  host: string;
  group: ReserveGroup;
  cause: string | null;
  /** Почему не прошёл — словами вердикта. */
  reason: string | null;
  dr: number | null;
  org_traffic: number | null;
  refdomains: number | null;
  keywords: number | null;
  fit: CountryFit;
  /** Уже в очереди этого прогона. */
  queued: ReviewDecision | null;
  takeable: boolean;
  /** Почему взять нельзя — словами сервера. */
  refusal: string | null;
}

export interface ReserveRun {
  id: number;
  country: string;
  keywords: number;
  created_at: string;
}

export interface ReserveView {
  rows: ReserveCard[];
  total: number;
  /** Строк на странице — число сервера. */
  limit: number;
  /** Группа → сколько доменов под остальными фильтрами. */
  groups: Partial<Record<ReserveGroup, number>>;
  /** Страна прогонов → сколько доменов в их резерве. */
  countries: Record<string, number>;
  /** Страна в топ-5 доменов резерва → у скольких. */
  geos: Record<string, number>;
  /** Прогон фильтра; `null` — резерв всех прогонов. */
  run: ReserveRun | null;
}

export interface ReserveQuery {
  run: number | null;
  country: string | null;
  group: ReserveGroup | null;
  geo: string | null;
  page: number;
}

export function loadReserve(query: ReserveQuery): Promise<ReserveView> {
  const params = new URLSearchParams();
  if (query.run !== null) params.set('run_id', String(query.run));
  if (query.country !== null) params.set('country', query.country);
  if (query.group !== null) params.set('group', query.group);
  if (query.geo !== null) params.set('geo', query.geo);
  if (query.page > 1) params.set('page', String(query.page));
  const tail = params.toString();
  return request<ReserveView>(`/review/reserve${tail ? `?${tail}` : ''}`);
}

export interface TakeResult {
  taken: number;
  /** Номер прогона → взятые в его очередь домены. */
  runs: Record<string, string[]>;
  already: string[];
  refused: Record<string, string>;
}

export function takeFromReserve(
  items: { run_id: number; domain_id: number }[],
): Promise<TakeResult> {
  return request<TakeResult>('/review/reserve/take', { method: 'POST', body: { items } });
}
