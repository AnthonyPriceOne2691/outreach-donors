/**
 * Кандидаты в рекламодатели: очередь ручной проверки и решение по ней.
 *
 * Экран нужен ради одного числа: допуск по ложным рекламодателям —
 * десять процентов, а скоринг судит по пяти признакам, два из которых
 * выведены из замера на одной нише.
 */

import { request } from './client';
import type { LetterAudience } from './letters';
import type { CandidateCard, CandidatesView, ContactsQueued, ContactsState } from './types';

/** Над кнопкой «Перевести»: сколько переводить и сколько уже заведено. */
export interface PromotionView {
  /** Домены среди «куплено» и «пишем» — их и переводит кнопка. */
  ready: number;
  /** Из них среди рекламодателей ещё нет. */
  fresh: number;
  advertisers: number;
  with_address: number;
}

/** Чем кончился перевод — числами по причинам, как в консоли. */
export interface PromoteResult {
  report: Record<string, number>;
  /** Заведённые впервые. */
  fresh: string[];
  /** Сколько рекламодателей ждёт адреса после перевода. */
  pending: number;
  /** Поиск адресов, поставленный самим переводом; `null` — искать некому. */
  contacts_job_id: string | null;
}

/** Что человек смотрит списком: спорных — решить, «куплено» — проверить до письма. */
export type ReviewedVerdict = 'pending' | 'bought';

/** Кандидаты одного вердикта скоринга и решения человека по ним. */
export interface VerdictDecisions {
  total: number;
  decided: number;
  /** «Пишем». */
  write: number;
  /** «Не пишем». */
  skip: number;
}

/** Скоринг против человека (`crawl/accuracy.py`): доля ложных среди решённых «куплено»
 *  случайной выборки и допуск ТЗ — от сервера, а не копией числа в экране. `false_share`
 *  пусто — решённых в выборке нет; `within_limit` пусто — решений меньше `min_decisions`
 *  и вывода нет. Какие строки очереди в выборке, сервер не говорит. */
export interface ScoringCheck {
  bought: VerdictDecisions;
  pending: VerdictDecisions;
  sample: VerdictDecisions;
  false_share: number | null;
  false_limit: number;
  within_limit: boolean | null;
  /** С какой выборки доле верят — та же, что у точности судьи Этапа 1. */
  min_decisions: number;
}

/** Очередь проверки с долей ложных (сверка ТЗ 11.10.2026). Нет поля — сервер старше экрана. */
export interface ReviewQueue extends CandidatesView {
  scoring?: ScoringCheck;
}

/** Кандидаты с вердиктом. С `includeDecided` — и те, по которым решили. */
export function fetchCandidates(
  includeDecided = false,
  verdict: ReviewedVerdict = 'pending',
): Promise<ReviewQueue> {
  const params = new URLSearchParams();
  if (includeDecided) params.set('include_decided', 'true');
  if (verdict !== 'pending') params.set('verdict', verdict);
  const query = params.size > 0 ? `?${params.toString()}` : '';
  return request<ReviewQueue>(`/advertisers${query}`);
}

/**
 * Решение человека. `force` нужен, чтобы передумать: без него повторное
 * решение — отказ, иначе два человека в одной очереди затрут друг друга.
 */
export function decideCandidate(
  id: number,
  confirmed: boolean,
  force = false,
): Promise<CandidateCard> {
  return request<CandidateCard>(`/advertisers/${id}/decide`, {
    method: 'POST',
    body: { confirmed, force },
  });
}

/** Сколько переводить в рекламодатели и сколько из них уже с адресом. */
export function fetchPromotion(): Promise<PromotionView> {
  return request<PromotionView>('/advertisers/promotion');
}

/** Перевести «куплено» и «пишем» в рекламодатели. Поиск адресов сервер ставит сам. */
export function promoteAdvertisers(): Promise<PromoteResult> {
  return request<PromoteResult>('/advertisers/promote', { method: 'POST' });
}

/** Поиск адресов рекламодателям — то же состояние, что у доноров. С аудиторией — ждущие своей
 *  вкладки писем; задача поиска при этом одна на всех. */
export function fetchAdvertiserContacts(audience?: LetterAudience): Promise<ContactsState> {
  const scope = audience === undefined ? '' : `?audience=${audience}`;
  return request<ContactsState>(`/advertisers/contacts${scope}`);
}

export function searchAdvertiserContacts(body: { limit: number }): Promise<ContactsQueued> {
  return request<ContactsQueued>('/advertisers/contacts', { method: 'POST', body });
}
