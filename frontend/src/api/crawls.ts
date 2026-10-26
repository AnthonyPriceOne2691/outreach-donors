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
  /** Мера здоровья ТЗ: окно и пороги «темп вдвое ниже» и «пауза». Нет — сервер старше. */
  health_window?: number | null;
  slow_down_share?: number | null;
  stop_share?: number | null;
  /** Потолок срока сайту с долгой паузой, с; нет — срок у всех общий (`max_seconds`). */
  max_seconds_slow?: number | null;
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
