/**
 * Доноры с ценами списком: шаблон, предпросмотр, загрузка и очередь загрузки обхода
 * (`api/donors/bulk.py`, 29.10.2026). Строки судит сервер правилом формы «Завести донора
 * вручную»; экран показывает его отчёт как есть.
 */

import type { Downloaded } from './client';
import { download, request, upload } from './client';

const BASE = '/donors/import';

/** Что вышло со строкой файла. */
export type BulkOutcome = 'created' | 'priced' | 'skipped';

export interface BulkRow {
  /** Номер строки файла. */
  line: number;
  /** Домен, как написан в файле. */
  given: string;
  outcome: BulkOutcome;
  /** Домен, приведённый к корню; `null` — доменом строка не стала. */
  host: string | null;
  /** Пропущена — почему; записана, но обход не возьмёт — почему. */
  reason: string | null;
  amount: string | null;
  currency: string | null;
  /** Записан, годен и с ценой — обход его возьмёт. */
  crawlable: boolean;
}

/** Очередь загрузки обхода: сколько ждёт и сколько это займёт. */
export interface BacklogView {
  waiting: number;
  /** Срок обойти всех ждущих, с; `null` — обходчиков не знаем или их нет. */
  eta_sec: number | null;
  workers: number | null;
}

export interface BulkReport {
  file: string;
  rows: BulkRow[];
  created: number;
  priced: number;
  skipped: number;
  crawlable: number;
  /** Поставлено в очередь загрузки этой загрузкой; у предпросмотра — 0. */
  queued: number;
  /** Очередь загрузки после загрузки; у предпросмотра — какой она станет с галочкой. */
  backlog: BacklogView;
}

function formOf(file: File, crawl?: boolean): FormData {
  const form = new FormData();
  form.append('file', file);
  if (crawl !== undefined) form.append('crawl', crawl ? 'true' : 'false');
  return form;
}

export function previewDonorList(file: File): Promise<BulkReport> {
  return upload<BulkReport>(`${BASE}/preview`, formOf(file));
}

export function enterDonorList(file: File, crawl: boolean): Promise<BulkReport> {
  return upload<BulkReport>(BASE, formOf(file, crawl));
}

export function downloadDonorTemplate(): Promise<Downloaded> {
  return download(`${BASE}/template`);
}

export function fetchBacklog(): Promise<BacklogView> {
  return request<BacklogView>(`${BASE}/backlog`);
}

export function dropBacklog(): Promise<{ dropped: number }> {
  return request<{ dropped: number }>(`${BASE}/backlog`, { method: 'DELETE' });
}
