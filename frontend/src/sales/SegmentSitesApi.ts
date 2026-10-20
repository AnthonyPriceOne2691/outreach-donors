/**
 * Запросы и типы списка сайтов прогона и пересуда (`backend/api/sales/segment_sites.py`).
 *
 * Своим файлом, а не в `segmentApi.ts`: его дописывают три среза сбора разом, а эти типы читает
 * только список сайтов и пересуд. Слова отказов и сметы — сервера, экран их не переводит.
 */

import { request } from '../api/client';
import type { SegmentLane } from './segmentApi';

/** Вкладка списка: полоса сайта, все — или сменившие полосу при пересуде. */
export type SiteTab =
  | 'all'
  | 'accept'
  | 'review'
  | 'would_reject'
  | 'reject'
  | 'pending'
  | 'changed';

/** Кто решил: правило до модели, модель, язык сайта (сверка с языком писем — кодом), человек. */
export type SiteDecider = 'rule' | 'model' | 'language' | 'human';

/** Чем кончился сайт после судьи. */
export type OutcomeKind = 'lead' | 'form_only' | 'no_address' | 'not_searched' | 'other';

export interface SiteOutcome {
  kind: OutcomeKind;
  /** Итог словами сервера: «только форма на сайте», «адрес ещё не искали…». */
  words: string;
  lead_id: number | null;
  /** Что очистка сделала с лидом: состояние и код причины отказа. */
  lead_status: string | null;
  lead_reason: string | null;
}

/** Пересуд сменил полосу сайта — или сменил бы: у сайта с лидом полоса та же («стало бы»). */
export interface SiteChange {
  was: SegmentLane;
  now: SegmentLane;
  would: boolean;
  reason: string | null;
  quote: string | null;
}

export interface SegmentSite {
  id: number;
  host: string;
  page_url: string | null;
  page_title: string | null;
  keyword: string | null;
  lane: SegmentLane;
  /** Что сказал судья сам: в наблюдении «отказ» лежит здесь, а сайт — в «судья отказал бы». */
  verdict: SegmentLane | null;
  decided_by: SiteDecider | null;
  decided_by_user: string | null;
  decided_at: string | null;
  reason: string | null;
  quote: string | null;
  unanswered: boolean;
  model: string | null;
  /** Решение человека можно отменить: решил человек, и лида из сайта нет. */
  undoable: boolean;
  /** Решил человек — слова судьи, с которыми он не согласился (или согласился); иначе пусто. */
  judge_reason: string | null;
  outcome: SiteOutcome | null;
  change: SiteChange | null;
}

export interface SegmentSitesPage {
  run_id: number;
  lane: SiteTab;
  decider: SiteDecider | null;
  page: number;
  /** Размер страницы называет сервер. */
  limit: number;
  /** Сколько сайтов под фильтром — всего, а не на странице. */
  total: number;
  /** Числа вкладок: `all` — все сайты прогона, `changed` — сменили полосу при пересуде. */
  lanes: Record<SiteTab, number>;
  /** Кто решил — внутри вкладки; `none` — никто (не судили, модель не ответила). */
  deciders: Record<SiteDecider | 'none', number>;
  rows: SegmentSite[];
}

export interface RejudgeEstimate {
  sites: number;
  model: number;
  per_site: number;
  tokens: number;
  tokens_left: number | null;
  kept_human: number;
  with_lead: number;
  pending: number;
  /** Отпечаток описания, которое спросит пересуд: запуск — только по этой смете. */
  digest: string;
  mode: string;
  /** Продолжение незаконченного пересуда того же описания — с его снимка; `done` — пройдено. */
  resumes: boolean;
  done: number;
  /** Смета словами — строкой на статью. */
  lines: string[];
  /** Пересуживать нельзя — почему, словами сервера. */
  refusal: string | null;
}

export interface RejudgeMove {
  was: SegmentLane;
  now: SegmentLane;
  would: boolean;
  count: number;
}

export type RejudgeState = 'queued' | 'going' | 'done' | 'stopped';

export interface RejudgeLast {
  state: RejudgeState;
  by: string | null;
  asked_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  total: number;
  done: number;
  /** Решил человек посреди пересуда. */
  human: number;
  /** Решения человека до пересуда: не пересуживались. */
  kept_human: number;
  pending: number;
  moved: RejudgeMove[];
  tokens: number;
  unanswered: number;
  failed: number;
  error: string | null;
}

/** Задача пересуда глазами очереди: ждёт повтора — почему и когда; упала — почему. */
export interface RejudgeJob {
  state: string;
  error: string | null;
  next_try_at: string | null;
}

export interface RejudgeView {
  run_id: number;
  estimate: RejudgeEstimate;
  last: RejudgeLast | null;
  /** Идёт ли задача пересуда; `null` — очередь не ответила. */
  going: boolean | null;
  /** Исход задачи в очереди; нет — очередь о ней не знает. */
  job?: RejudgeJob | null;
}

export interface RejudgeQueued {
  run_id: number;
  job_id: string;
}

export interface SitesQuery {
  tab: SiteTab;
  decider: SiteDecider | null;
  page: number;
}

export function readSites(run: number, query: SitesQuery): Promise<SegmentSitesPage> {
  const params = new URLSearchParams({ lane: query.tab, page: String(query.page) });
  if (query.decider !== null) params.set('decider', query.decider);
  return request<SegmentSitesPage>(`/sales/segment/runs/${run}/sites?${params.toString()}`);
}

export function readRejudge(run: number): Promise<RejudgeView> {
  return request<RejudgeView>(`/sales/segment/runs/${run}/rejudge`);
}

export function startRejudge(run: number, digest: string): Promise<RejudgeQueued> {
  return request<RejudgeQueued>(`/sales/segment/runs/${run}/rejudge`, {
    method: 'POST',
    body: { digest },
  });
}

/** Решение человека по сайту «на проверку» — ответ строкой списка: она встаёт на место той, по
 *  которой решали. */
export function decideSite(candidate: number, accept: boolean): Promise<SegmentSite> {
  return request<SegmentSite>(`/sales/segment/candidates/${candidate}/decision`, {
    method: 'POST',
    body: { accept },
  });
}

/** Отмена решения человека: сайт — туда и со словами, куда его кладёт судья; лид уже заведён,
 *  решил не человек — отказ словами сервера. */
export function undoDecision(candidate: number): Promise<SegmentSite> {
  return request<SegmentSite>(`/sales/segment/candidates/${candidate}/decision`, {
    method: 'DELETE',
  });
}
