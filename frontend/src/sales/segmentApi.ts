/**
 * Запросы и типы вкладки «Сбор базы» — прогон гипотезы: ключи → выдача → судья сегмента →
 * адрес → лиды (`backend/api/sales/segment.py`). Своим файлом раздела: общие `api/sales*.ts`
 * у предела длины, а типы этой вкладки больше никто не читает.
 *
 * Сервер считает смету и отказывает словами — экран показывает их как есть: что дорого,
 * чего не хватает и почему нельзя, решает правило сервера, а не копия на экране.
 */

import { request } from '../api/client';

/** Режим судьи сегмента — как у судьи площадки: наблюдает, режет, выключен. */
export type SegmentMode = 'shadow' | 'enforce' | 'off';

/** Полоса сайта: ещё не судили, принят, на проверке, отсечён. */
export type SegmentLane = 'pending' | 'accept' | 'review' | 'reject';

/** Где прогон: этап, в который он вошёл (с начала его работы), или чем кончился. */
export type SegmentStage = 'queued' | 'serp' | 'judge' | 'contacts' | 'leads' | 'done' | 'stopped';

export interface SegmentRunRow {
  id: number;
  created_at: string;
  finished_at: string | null;
  created_by: string | null;
  country: string;
  language: string;
  depth_pages: number;
  keywords: number;
  mode: SegmentMode;
  stage: SegmentStage;
  error: string | null;
  job_id: string | null;
  /** Гипотеза прогона. Необязательные поля ниже — у сервера старше хода их нет. */
  hypothesis_id?: number;
  /** Пульс: когда строка прогона менялась последний раз — идущий проход отмечается сам. */
  alive_at?: string;
  /** Прогон идёт — решает сервер по очереди задач; экран опрашивает, пока это так. */
  active?: boolean;
  progress?: SegmentProgress | null;
  watch?: SegmentWatch | null;
}

export interface SegmentHome {
  hypothesis_id: number;
  /** Описание сегмента гипотезы — его читает судья. Пусто — описания нет. */
  segment: string | null;
  mode: SegmentMode;
  countries: string[];
  languages: string[];
  max_keywords: number;
  max_depth: number;
  /** Режим покупки выдачи по умолчанию — настройкой сервиса: `standard`, `priority`, `live`;
   *  пусто — не прочитан. У сервера старше режимов поля нет. */
  serp_mode?: SerpMode | null;
  runs: SegmentRunRow[];
}

/** Что просят посчитать и запустить. Язык пуст — язык рынка выдачи; режим выдачи пуст —
 *  умолчание сервиса. */
export interface SegmentAsk {
  hypothesis_id: number;
  keywords: string;
  country: string;
  language: string | null;
  depth_pages: number;
  serp_mode: SerpMode | null;
}

export interface SegmentEstimate {
  /** Взятые ключи — за них заплатит выдача, в том порядке, как вписаны. */
  keywords: string[];
  /** Отсеянный ключ → почему, словами. */
  rejected: Record<string, string>;
  /** Взятый ключ → пометка словами: почти повтор, решает человек. */
  notes: Record<string, string>;
  country: string;
  language: string;
  depth_pages: number;
  mode: SegmentMode;
  serp_mode: SerpMode;
  serp_page_usd: number;
  serp_wait_s: number;
  results: number;
  /** Сайтов на запрошенный результат: из истории сбора (`share_runs` прогонов) или запасная. */
  share: number;
  share_runs: number;
  /** Сайтов в выдаче, из них уже в сборе гипотезы и новых — их судит судья. */
  hosts: number;
  known: number;
  sites: number;
  serp_usd: number;
  tokens: number;
  tokens_usd: number;
  tokens_left: number | null;
  hunter_searches: number;
  hunter_checks: number;
  /** Смета словами — строкой на статью: выдача, сайты, судья, Hunter, деньги. */
  lines: string[];
  /** Запускать нельзя — почему, словами сервера. */
  refusal: string | null;
}

export interface SegmentCandidate {
  id: number;
  host: string;
  page_url: string | null;
  page_title: string | null;
  keyword: string | null;
  lane: SegmentLane;
  /** Что сказал судья сам: при наблюдении «отказ» лежит здесь, а сайт — на проверке. */
  verdict: SegmentLane | null;
  reason: string | null;
  quote: string | null;
  unanswered: boolean;
  decided_by_user: string | null;
}

export interface SegmentCount {
  words: string;
  count: number;
}

export interface SegmentSummary {
  hosts: number;
  accepted: number;
  review: number;
  rejected: number;
  /** Ещё не судили: без этой плитки во время судьи числа не сходились. */
  pending?: number;
  no_address: number;
  leads: number;
}

export interface SegmentRunDetail {
  run: SegmentRunRow;
  hypothesis: string;
  keywords: string[];
  estimate_lines: string[];
  summary: SegmentSummary;
  lanes: Record<string, number>;
  rejected: SegmentCount[];
  no_address: SegmentCount[];
  waiting_address: number;
  /** Лиды прогона по состоянию очистки: новый, готов, отклонён. */
  leads: Record<string, number>;
  /** Отклонённые очисткой: код причины → сколько. */
  lead_reasons: Record<string, number>;
  unanswered: number;
  report: Record<string, unknown>;
  review: SegmentCandidate[];
  /** Ключи и расход прогона рядом со сметой на запуске. В записанных ответах старых тестов
   *  его нет — тогда панели ключей и расхода молчат. */
  spend?: SegmentRunSpend;
}

export interface SegmentQueued {
  run_id: number;
  job_id: string;
}

export function readSegment(hypothesis: number): Promise<SegmentHome> {
  return request<SegmentHome>(`/sales/segment?hypothesis=${hypothesis}`);
}

/** Описание сегмента гипотезы. Пустое — описания нет, и судье не с чем сравнивать. */
export function writeSegment(
  hypothesis: number,
  description: string,
): Promise<{ hypothesis_id: number; description: string | null }> {
  return request(`/sales/hypotheses/${hypothesis}`, { method: 'PATCH', body: { description } });
}

export function estimateRun(ask: SegmentAsk): Promise<SegmentEstimate> {
  return request<SegmentEstimate>('/sales/segment/estimate', { method: 'POST', body: ask });
}

export function startRun(ask: SegmentAsk): Promise<SegmentQueued> {
  return request<SegmentQueued>('/sales/segment/runs', { method: 'POST', body: ask });
}

export function resumeRun(run: number): Promise<SegmentQueued> {
  return request<SegmentQueued>(`/sales/segment/runs/${run}/resume`, { method: 'POST' });
}

export function readRun(run: number): Promise<SegmentRunDetail> {
  return request<SegmentRunDetail>(`/sales/segment/runs/${run}`);
}

export function decideSite(candidate: number, accept: boolean): Promise<SegmentCandidate> {
  return request<SegmentCandidate>(`/sales/segment/candidates/${candidate}/decision`, {
    method: 'POST',
    body: { accept },
  });
}

/** Режим покупки выдачи: быстрый — секунды, стандарт — дешевле и дольше (приоритетный форма
 *  не предлагает). Цену страницы и ожидание называет смета. */
export type SerpMode = 'live' | 'standard' | 'priority';

/** Статья расхода прогона: факт и смета на запуске — словами сервера. Число пусто — в этой
 *  единице не платили или этапа ещё не было. */
export interface SegmentSpendRow {
  item: 'serp' | 'judge' | 'search' | 'check' | 'money';
  title: string;
  fact: string;
  promised: string;
  fact_units: number | null;
  fact_usd: number | null;
  promised_units: number | null;
  promised_usd: number | null;
}

/** Ключи прогона: взятые с пометками и отсеянные — как на запуске; пропавшие (заплачено —
 *  не пришло) и пустые — что вышло с покупкой выдачи. */
export interface SegmentRunKeywords {
  taken: string[];
  notes: Record<string, string>;
  rejected: Record<string, string>;
  lost: string[];
  empty: string[];
}

export interface SegmentRunSpend {
  keywords: SegmentRunKeywords;
  rows: SegmentSpendRow[];
}

/** Ход прохода (`report.progress`): этап, сделано из всего, когда начат этап и проход. «Сделано»
 *  судьи и адресов сервер считает по сайтам прогона — оно живое. */
export interface SegmentProgress {
  stage: string;
  done: number | null;
  total: number | null;
  started_at: string | null;
  updated_at: string | null;
  pass_started_at: string | null;
  /** Режим покупки выдачи: `standard`, `priority`, `live`. */
  mode?: string | null;
  /** Что знать о сбое этапа до следующего прохода — словами сервера. */
  note?: string | null;
}

/** Что с задачей прогона в очереди — у идущего прогона и у поставленного продолжения. */
export interface SegmentWatch {
  /** Задача жива: в очереди, идёт или ждёт повтора. */
  going: boolean;
  /** Строка «идёт», а задачи нет — почему, словами: прогон остановлен, его продолжают. */
  stalled: string | null;
  /** Живых воркеров долгих задач продаж; ноль — задачу некому взять; пусто — не спросили. */
  workers: number | null;
  /** Очередь не ответила: жив ли прогон, сейчас не узнать. */
  silent: boolean;
  /** Состояние живой задачи: `queued`, `deferred`, `scheduled` (ждёт повтора), `started`. */
  job?: string | null;
}
