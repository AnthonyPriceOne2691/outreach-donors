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
  /** Домены без DR: в `ready` не входят и ждут, пока обход его спросит. */
  waiting_dr?: number;
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

/** Вывод о доле против допуска — по её границам (`crawl/accuracy.Within`): в допуске, вне
 *  допуска, не доказано или решений нет. */
export type Within = 'within' | 'outside' | 'unproven' | 'unmeasured';

/** Доля «не пишем» среди решённых строк случайной выборки (`crawl/accuracy.Share`). `share`,
 *  `low` и `high` пусты — решений нет. `to_go` — сколько решений без «не пишем» ещё нужно для
 *  «в допуске»; пусто, когда вывод уже есть. `left` — нерешённых строк выборки. */
export interface SampleShare extends VerdictDecisions {
  /** Строк выборки вне замера: нерешённые и решённые не по порядку. */
  left: number;
  /** Решены дальше первой нерешённой строки выборки — в долю не идут (`accuracy.in_turn`). */
  aside: number;
  share: number | null;
  /** Нижняя граница доли, односторонняя 95 %. */
  low: number | null;
  /** Верхняя граница доли, односторонняя 95 %. */
  high: number | null;
  within: Within;
  to_go: number | null;
}

/** Скоринг против человека (`crawl/accuracy.py`): доля ложных среди решённых «куплено»
 *  случайной выборки, ошибка базы «писать всем спорным» по выборке спорных и допуск ТЗ — от
 *  сервера, а не копией числа в экране. Какие строки очереди в выборке, сервер не говорит. */
export interface ScoringCheck {
  bought: VerdictDecisions;
  pending: VerdictDecisions;
  /** «Куплено» случайной выборки — доля ложных рекламодателей. */
  bought_sample: SampleShare;
  /** Спорные случайной выборки — доля «не пишем», ошибка базы «писать всем спорным». */
  pending_sample: SampleShare;
  false_limit: number;
}

/** Срез выборки спорных: строк в нём и решения по ним. Ключ балла — сам балл. */
export interface Stratum extends VerdictDecisions {
  key: string;
  title: string;
}

/** Выборка спорных по баллу, признаку скоринга и нише донора (`crawl/strata.py`). */
export interface DisputedStrata {
  points: Stratum[];
  features: Stratum[];
  niches: Stratum[];
}

/** Разбивка выборки спорных — своим запросом: экран спрашивает её, когда её раскрыли. */
export function fetchDisputedStrata(): Promise<DisputedStrata> {
  return request<DisputedStrata>('/advertisers/pending/strata');
}

/** Совет судьи рекламодателей — только у строки, по которой человек уже решил
 *  (`crawl/judge_advice.py`): до решения он тянул бы решение за собой. */
export interface JudgeAdvice {
  advice: 'write' | 'skip' | 'unclear';
  /** Правило таблицы словами. */
  words: string;
  link_quote: string;
  target_quote: string;
  disclosure: string;
  prompt_version: string;
  model: string;
  judged_at: string;
}

/** Строка очереди проверки: кандидат и, после решения человека, совет судьи. */
export interface ReviewRow extends CandidateCard {
  judge?: JudgeAdvice | null;
  /** Пояснение причины по её словам («зона .org» — почему решает человек); нет — сервер старше. */
  reason_notes?: Record<string, string>;
}

/** Вариант фильтра очереди: значение, слова экрана и сколько строк с ним. */
export interface FacetChoice {
  key: string;
  title: string;
  count: number;
}

/** Варианты фильтра колонки. `total` — сколько строк, когда он не сужает («все»). */
export interface QueueFacet {
  total: number;
  choices: FacetChoice[];
}

/** Счётчики под фильтрами: у каждого — со всеми условиями, кроме своего (`crawl/review.py`).
 *  `listed` — строк списка без фильтров. */
export interface QueueFacets {
  listed: number;
  points: QueueFacet;
  features: QueueFacet;
}

/** Страница очереди проверки. `total` и `limit` — по ним считаются страницы, `total` — под
 *  фильтром; `waiting`, `counts` и доля ложных (сверка ТЗ 11.10.2026) — по всем, от страницы и
 *  фильтра не меняются. Нет `scoring` — сервер старше экрана. */
export interface ReviewQueue extends CandidatesView {
  rows: ReviewRow[];
  total: number;
  page: number;
  limit: number;
  facets: QueueFacets;
  scoring?: ScoringCheck;
}

/** Какие строки спрашивает очередь — именами адреса экрана и сервера (`reviewFilters.ts`). */
export interface ReviewQuery {
  include_decided?: 'true';
  verdict?: ReviewedVerdict;
  points?: string;
  feature?: string;
}

/** Страница очереди: кандидаты с вердиктом под фильтром. */
export function fetchCandidates(query: ReviewQuery = {}, page = 1): Promise<ReviewQueue> {
  const params = new URLSearchParams(query as Record<string, string>);
  if (page > 1) params.set('page', String(page));
  const tail = params.size > 0 ? `?${params.toString()}` : '';
  return request<ReviewQueue>(`/advertisers${tail}`);
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
