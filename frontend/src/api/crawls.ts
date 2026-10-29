/**
 * Обходы Этапа 2: кого можно обойти, запуск и ход.
 *
 * Типы — здесь, рядом с запросами, а не в общем `types.ts`: их читает
 * только панель обходов.
 */

import { request } from './client';
import type { PriceSource } from './manualPrice';

export type CrawlStatus = 'queued' | 'running' | 'done' | 'stopped';
export type CrawlOutcome = 'ok' | 'partial' | 'forbidden' | 'blocked' | 'failed';
/** Почему обход кончился — `StopReason` сервера. */
export type CrawlStopReason =
  | 'exhausted'
  | 'max_pages'
  | 'max_attempts'
  | 'timeout'
  | 'unhealthy'
  | 'robots'
  | 'no_start';
/** Откуда обход брал адреса: карта сайта, ссылки со страниц или карта, а после неё ссылки. */
export type CrawlSource = 'sitemap' | 'links' | 'sitemap+links';

/** Потолки обхода донора, кроме страниц, — числами настроек сервера: попыток (запросов к
 *  страницам), времени (с) и паузы между страницами — не меньше `delay_sec`, не больше
 *  `max_delay_sec`, что бы ни просил сайт. Нет — сервер старше экрана. */
export interface CrawlCeilings {
  max_attempts?: number;
  max_seconds?: number;
  delay_sec?: number;
  max_delay_sec?: number;
}

/** Где ждёт обход в очереди: занятые обходчики из скольких и сколько ждущих обходов перед ним.
 *  `workers`: `null` — не спросили, 0 — некому взять; `ahead`: `null` — места нет (его как раз
 *  берёт обходчик или очередь не спросили). */
export interface CrawlPlace {
  busy: number;
  workers: number | null;
  ahead: number | null;
  /** Сколько свободных обходчиков держит сторож нагрузки — машина занята — и почему;
   *  `null` или нет — не спросили (сервер старше). */
  held?: number | null;
  held_reason?: string | null;
}

/** Один обход: где он и чем кончился. `max_pages` — потолок хода «340 из 1000». */
export interface CrawlRow extends CrawlCeilings {
  id: number;
  host: string;
  status: CrawlStatus;
  outcome: CrawlOutcome | null;
  stop_reason: CrawlStopReason | null;
  pages: number;
  max_pages: number;
  articles: number;
  links: number | null;
  resumes: number;
  requested_by: string | null;
  created_at: string;
  finished_at: string | null;
  /** Почему обход стоит, продолжен или остановлен — словами человека. */
  reason: string | null;
  /** Доля закрытых страниц от всех запросов обхода, 0…1. Пусто — не мерили: обход
   *  идёт или записан до замера. Нет поля — сервер старше экрана. */
  blocked_share?: number | null;
  /** Сколько запросов за долей. */
  requests?: number | null;
  /** Доля 403/429/5xx/таймаут за последние `window` запросов — мера здоровья ТЗ. */
  failure_share?: number | null;
  window?: number | null;
  /** Чего каскаду не хватило: уровень обхода → словами (прокси отказали подряд все, сервис
   *  рендеринга отключился, потолок Этапа 2 выбран). Нет у старых ответов — необязательное. */
  degradation?: Record<string, string>;
  /** Почему кончился — из отчёта законченного обхода (у идущего и записанного до отчёта
   *  пусто): откуда брались адреса, дочитана ли карта сайта, какую паузу просит robots.txt
   *  сайта (с), сколько обход шёл (с) и свой срок обхода (с), если он его записал. */
  source?: CrawlSource | null;
  sitemap_complete?: boolean | null;
  crawl_delay?: number | null;
  elapsed_sec?: number | null;
  time_budget_sec?: number | null;
  /** Место в очереди — у ждущего обхода; у идущего и законченного, у ответа старого сервера и
   *  у «Приёмки по донорам» (очередь она не спрашивает) — пусто. */
  place?: CrawlPlace | null;
}

/** Донор, которого можно обойти: цена — основание, обход — что о нём знаем. */
export interface CrawlTarget {
  host: string;
  price: number | null;
  /** Валюта цены, как её назвали: конвертации в сервисе нет. */
  currency: string | null;
  /** Из ответа донора или вписана человеком; `null` — цена до 07.10.2026,
   *  из ответа. */
  source: PriceSource | null;
  priced_at: string | null;
  crawl: CrawlRow | null;
  /** Цена в сроке, протухла или нет — у вкладки «Обход доноров»; у панели — нет. */
  price_state?: DonorPriceState | null;
}

export interface CrawlTargets extends CrawlCeilings {
  donors: CrawlTarget[];
  no_price: number;
  stale_price: number;
  supplier: number;
  notes: string[];
  max_pages: number;
  /** Сколько обходчиков слушает очередь; `null` — не спросили, 0 — некому взять. */
  workers: number | null;
  /** Сколько из них сторож нагрузки держит без работы — машина занята; `null` или нет —
   *  не спросили (сервер старше). */
  held?: number | null;
  /** Почему держит — словами сервера: «нагрузка 0,93 на ядро при пороге 0,80». */
  held_reason?: string | null;
  /** Мера здоровья ТЗ: окно и пороги «темп вдвое ниже» и «пауза». Нет — сервер старше. */
  health_window?: number | null;
  slow_down_share?: number | null;
  stop_share?: number | null;
  /** Потолок срока сайту с долгой паузой, с; нет — срок у всех общий (`max_seconds`). */
  max_seconds_slow?: number | null;
}

/** Что нужно «i» обхода: потолки, мера здоровья, число обходчиков — у панели и у вкладки. */
export type CrawlRules = CrawlCeilings & {
  max_pages: number;
  workers: number | null;
  health_window?: number | null;
  slow_down_share?: number | null;
  stop_share?: number | null;
  max_seconds_slow?: number | null;
};

export interface CrawlsView {
  rows: CrawlRow[];
  active: number;
  workers: number | null;
  /** Как у `CrawlTargets`: сколько обходчиков держит сторож нагрузки и почему. */
  held?: number | null;
  held_reason?: string | null;
  /** Обходов в журнале всего — для страниц «Последних обходов»; нет — сервер старше. */
  total?: number | null;
}

/** Статус последнего обхода донора — фильтр и счётчик вкладки «Обход доноров»
 *  (`crawl/donor_page.Status`): «не пустил» — robots.txt, защита от ботов, главная не открылась. */
export type DonorCrawlStatus =
  | 'never'
  | 'queued'
  | 'running'
  | 'done'
  | 'partial'
  | 'refused'
  | 'stopped';
/** Цена донора: в сроке, старше срока, нет. */
export type DonorPriceState = 'fresh' | 'stale' | 'none';
/** Порядок строк вкладки; умолчание — свежая цена сверху. */
export type DonorCrawlSort = 'priced' | 'crawled' | 'host' | 'price' | 'links';

/** Условия вкладки — имена те же, что у сервера (`donor_schemas.DonorFilter`). */
export interface CrawlDonorQuery {
  status?: DonorCrawlStatus;
  price?: DonorPriceState;
  source?: PriceSource;
  search?: string;
  sort?: DonorCrawlSort;
}

export interface CrawlCounts {
  status: Record<DonorCrawlStatus, number>;
  price: Record<DonorPriceState, number>;
}

export interface CrawlDonorsView extends CrawlCeilings {
  rows: CrawlTarget[];
  /** Сколько доноров под условиями всего, а не на этой странице. */
  total: number;
  page: number;
  limit: number;
  counts: CrawlCounts;
  /** Сколько под условиями можно поставить в обход сейчас. */
  startable: number;
  /** Сколько «Обойти всех по фильтру» ставит за раз; сервер старше — без предела. */
  bulk_max?: number;
  /** Сколько ставится сразу в очередь обходов; больше — в очередь загрузки. */
  launch_max?: number;
  workers: number | null;
  /** Как у `CrawlTargets`: сколько обходчиков держит сторож нагрузки и почему. */
  held?: number | null;
  held_reason?: string | null;
  /** Сколько обычно идёт обход донора, с, — для «≈ N ч при K обходчиках». */
  typical_crawl_sec: number;
  max_pages: number;
  health_window?: number | null;
  slow_down_share?: number | null;
  stop_share?: number | null;
  max_seconds_slow?: number | null;
}

export interface CrawlSummary {
  counts: CrawlCounts;
  startable: number;
  active: number;
  workers: number | null;
}

export interface CrawlBulkResult {
  asked: number;
  /** Многих — в очередь загрузки: тогда `queued` пусто, а ход — строкой очереди загрузки. */
  deferred: boolean;
  queued: number | null;
  busy: number | null;
  failed: number | null;
  /** Сколько встало в очередь загрузки (уже стоявшие — не второй раз). */
  backlog?: number | null;
  workers: number | null;
}

export interface CrawlLaunch {
  queued: Record<string, number>;
  busy: string[];
  failed: string[];
  /** Донор → почему его не обходим. */
  refused: Record<string, string>;
  workers: number | null;
}

export function fetchCrawls(page = 1): Promise<CrawlsView> {
  return request<CrawlsView>(page > 1 ? `/crawls?page=${page}` : '/crawls');
}

/** Условия → строка адреса: только заданные, в одном порядке — его ждут и тесты экрана. */
function donorParams(query: CrawlDonorQuery, page = 1, size?: number): string {
  const params = new URLSearchParams();
  for (const key of ['status', 'price', 'source', 'search', 'sort'] as const) {
    const value = query[key];
    if (value !== undefined && value !== '') params.set(key, value);
  }
  if (page > 1) params.set('page', String(page));
  if (size !== undefined) params.set('size', String(size));
  const tail = params.toString();
  return tail === '' ? '' : `?${tail}`;
}

export function fetchCrawlDonors(
  query: CrawlDonorQuery,
  page: number,
  size?: number,
): Promise<CrawlDonorsView> {
  return request<CrawlDonorsView>(`/crawls/donors${donorParams(query, page, size)}`);
}

export function fetchCrawlSummary(): Promise<CrawlSummary> {
  return request<CrawlSummary>('/crawls/summary');
}

/** «Обойти всех по фильтру»: те же условия и сколько человек видел на кнопке. */
export function startCrawlsByFilter(
  query: CrawlDonorQuery,
  expected: number,
): Promise<CrawlBulkResult> {
  return request<CrawlBulkResult>('/crawls/donors/crawl', {
    method: 'POST',
    body: { ...query, expected },
  });
}

export function startCrawls(hosts: string[]): Promise<CrawlLaunch> {
  return request<CrawlLaunch>('/crawls', { method: 'POST', body: { hosts } });
}
