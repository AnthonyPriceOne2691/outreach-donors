/**
 * Панель лимитов на «Расходе»: строки по задачам, просчёт нового лимита и смена.
 *
 * Своим файлом, а не в `settings.ts`, как потолки модели и Hunter: это своя тема. Договор строки —
 * `core/limit_rows.py`: чего нет — `null`, а не ноль, и почему — словами сервера.
 */

import { request } from './client';

/** Поле строки, которое меняют с экрана. */
export type LimitField = 'per_session' | 'per_day' | 'per_month';

/** Строка панели: один лимит одной задачи. */
export interface LimitRow {
  /** Стабильный ключ: по нему просчёт и смена. */
  key: string;
  /** Задача — группа панели: «Продажи», «Аутрич». */
  task: string;
  service: string;
  action: string;
  /** Охват: «все ящики продаж», «один ящик». */
  scope: string;
  /** Единица лимитов и выбранного: «токенов», «писем», «$». */
  unit: string;
  per_session: number | null;
  /** Сколько часов в сессии; `null` — сессия не часами (прогон). */
  session_hours: number | null;
  per_day: number | null;
  per_month: number | null;
  used_day: number | null;
  used_month: number | null;
  /** Потрачено за месяц деньгами — у счётчика без лимита и единиц. */
  spent_month_usd: string | null;
  /** Стоимость месяца при выборке лимита целиком; платят тарифом — `null`, а не ноль. */
  cost_usd: string | null;
  cost_note: string | null;
  /** Текущее ограничение словами; у строки без правки — и почему она не меняется. */
  now: string | null;
  /** Откуда значение: поле, `.env` или код. */
  source: string;
  /** Что можно менять с экрана — по состоянию; пусто — причина в `now`. */
  editable: LimitField[];
}

/** Панель: все строки, которые видит учётка, по задачам. */
export interface LimitPanel {
  rows: LimitRow[];
}

/** Какое значение какой строки. */
export interface LimitAsk {
  key: string;
  field: LimitField;
  value: number;
}

/** Ответ просчёта: стоимость нового лимита, слова, отказ и что подтверждается на деле. */
export interface LimitQuote {
  cost_usd: string | null;
  words: string;
  refusal: string | null;
  /** Что встанет на деле, после округления. */
  accepted: number;
}

export function fetchLimitPanel(): Promise<LimitPanel> {
  return request<LimitPanel>('/usage/limits');
}

/** Просчёт нового значения: сервер ничего не пишет. */
export function quoteLimit(ask: LimitAsk): Promise<LimitQuote> {
  return request<LimitQuote>('/usage/limits/quote', { method: 'POST', body: ask });
}

/** Смена: `quoted` — что человек видел как подтверждаемое; сервер считает заново. */
export function changeLimit(ask: LimitAsk & { quoted: number }): Promise<LimitQuote> {
  return request<LimitQuote>('/usage/limits/change', { method: 'POST', body: ask });
}
