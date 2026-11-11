/**
 * Панель раздела «Рекламодатели» (`GET /advertisers/panel…`): шаги выборки, качество загрузок,
 * режим очереди и выгрузка спорных файлом. Числа считает сервер, экран их только называет.
 */

import type { MailingForecastView } from '../sales/mailingTypes';
import type { QueueAutosend } from './autosend';
import { download, request } from './client';
import type { Downloaded } from './client';
import type { CrawlRow } from './crawls';

/** Какую выборку смотрят: по умолчанию — сегодня. */
export type PanelRange = 'today' | 'week' | 'all' | 'load';

/** Откуда доноры строки: файлом, вручную, обходы кнопкой без записи о загрузке (`crawl`) или
 *  ночной обход (`night`). */
export type LoadKind = 'bulk' | 'manual' | 'crawl' | 'night';

export interface PanelLoad {
  id: number;
  at: string;
  kind: Exclude<LoadKind, 'crawl' | 'night'>;
  file_name: string | null;
  hosts: number;
}

/** Доноры выборки: `added` — все, остальные — где каждый из них сейчас. */
export interface PanelDonors {
  added: number;
  started: number;
  running: number;
  waiting: number;
  refused: number;
  idle: number;
}

/** Рекламодатели выборки: каждый следующий шаг — из предыдущего. */
export interface PanelAdvertisers {
  found: number;
  fresh: number;
  repeat: number;
  promoted: number;
  with_address: number;
  sent: number;
  delivered: number;
}

export interface PanelView {
  range: PanelRange;
  since: string | null;
  load: PanelLoad | null;
  loads: PanelLoad[];
  donors: PanelDonors;
  advertisers: PanelAdvertisers;
}

/** Что есть только у ночной строки: сколько ночей за ней, идёт ли обход и что нового. */
export interface QualityNight {
  /** Ночей с обходами — вместе с последней: прошлых на одну меньше. */
  nights: number;
  /** Обход этой ночи ещё идёт: итог строки не окончательный. */
  running: boolean;
  /** Новых рекламодателей за ночь; пусто — посчитать не по чему. */
  fresh: number | null;
}

export interface QualityRow {
  at: string;
  kind: LoadKind;
  load_id: number | null;
  /** День обходов без загрузки или ночь ночного обхода; у партии пусто. */
  day: string | null;
  file_name: string | null;
  donors: number;
  /** Домен строки, когда он один. */
  host: string | null;
  scanned: number;
  pages: number;
  /** Продающих ссылок; пусто — у обходов строки этого числа ещё нет. */
  selling_links: number | null;
  selling_pages: number;
  /** Доля страниц с продающей ссылкой; пусто — по той же причине. */
  share: number | null;
  /** Только у ночной строки страницы качества; у прошлых ночей пусто. У сервера старше экрана поля
   *  нет вовсе: его строки — загрузки и дни, как раньше. */
  night?: QualityNight | null;
}

export interface QualityView {
  rows: QualityRow[];
  total: number;
  page: number;
  limit: number;
}

export interface QualityHostRow {
  host: string;
  crawl: CrawlRow | null;
  selling_links: number;
}

export interface QualityHostsView {
  rows: QualityHostRow[];
  total: number;
  page: number;
  limit: number;
}

/** Режим обеих очередей рекламодателей. */
export interface PanelMailing {
  links: QueueAutosend;
  niche: QueueAutosend;
}

export function fetchPanel(range: PanelRange, load: number | null): Promise<PanelView> {
  const params = new URLSearchParams({ range });
  if (load !== null) params.set('load', String(load));
  return request<PanelView>(`/advertisers/panel?${params.toString()}`);
}

export function fetchQuality(page: number): Promise<QualityView> {
  return request<QualityView>(`/advertisers/panel/quality?page=${page}`);
}

/** Ночи ночного обхода до последней — свежие первыми: последняя стоит строкой качества. */
export function fetchQualityNights(page: number): Promise<QualityView> {
  return request<QualityView>(`/advertisers/panel/quality/nights?page=${page}`);
}

/** Чем сервер узнаёт строку качества: партию — по номеру, ночной обход — по ночи, обходы кнопкой
 *  без загрузки — по дню. */
function rowName(row: QualityRow): Record<string, string> {
  if (row.load_id !== null) return { load: String(row.load_id) };
  return row.kind === 'night' ? { night: row.day ?? '' } : { day: row.day ?? '' };
}

/** Домены строки качества страницей. */
export function fetchQualityHosts(row: QualityRow, page: number): Promise<QualityHostsView> {
  const params = new URLSearchParams(rowName(row));
  params.set('page', String(page));
  return request<QualityHostsView>(`/advertisers/panel/quality/hosts?${params.toString()}`);
}

export function fetchPanelMailing(): Promise<PanelMailing> {
  return request<PanelMailing>('/advertisers/panel/mailing');
}

/** Дней плана и факта — те же, что у вкладки «Рассылка». */
export const FORECAST_DAYS = 7;

/** Прогноз рассылки рекламодателей: общая ручка почты, что и у вкладки «Рассылка» продаж. */
export function fetchAdvertiserForecast(): Promise<MailingForecastView> {
  return request<MailingForecastView>(
    `/letters/forecast?stage=advertisers&days=${FORECAST_DAYS}&back=${FORECAST_DAYS}`,
  );
}

/** Все спорные файлом — специалисту. */
export function exportPending(): Promise<Downloaded> {
  return download('/advertisers/pending/export');
}
