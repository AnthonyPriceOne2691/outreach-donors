/**
 * Обходы Этапа 2: кого можно обойти, запуск и ход.
 *
 * Типы — здесь, рядом с запросами, а не в общем `types.ts`: их читает
 * только панель обходов.
 */

import { request } from './client';

export type CrawlStatus = 'queued' | 'running' | 'done' | 'stopped';
export type CrawlOutcome = 'ok' | 'partial' | 'forbidden' | 'blocked' | 'failed';

/** Один обход: где он и чем кончился. `max_pages` — потолок хода «340 из 1000». */
export interface CrawlRow {
  id: number;
  host: string;
  status: CrawlStatus;
  outcome: CrawlOutcome | null;
  stop_reason: string | null;
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
}

/** Донор, которого можно обойти: цена — основание, обход — что о нём знаем. */
export interface CrawlTarget {
  host: string;
  price: number | null;
  priced_at: string | null;
  crawl: CrawlRow | null;
}

export interface CrawlTargets {
  donors: CrawlTarget[];
  no_price: number;
  stale_price: number;
  supplier: number;
  notes: string[];
  max_pages: number;
  /** Сколько обходчиков слушает очередь; `null` — не спросили, 0 — некому взять. */
  workers: number | null;
}

export interface CrawlsView {
  rows: CrawlRow[];
  active: number;
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

export function fetchCrawlTargets(): Promise<CrawlTargets> {
  return request<CrawlTargets>('/crawls/targets');
}

export function fetchCrawls(): Promise<CrawlsView> {
  return request<CrawlsView>('/crawls');
}

export function startCrawls(hosts: string[]): Promise<CrawlLaunch> {
  return request<CrawlLaunch>('/crawls', { method: 'POST', body: { hosts } });
}
