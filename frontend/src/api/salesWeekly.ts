/**
 * Недельный отчёт продаж (срез 6.3): экран и выгрузка CSV.
 *
 * Своим файлом, а не в `salesTypes.ts`: тот у предела длины. Имена полей — те же, что у
 * сервера (`backend/api/sales/weekly.py`); их держит тест сервера, читающий этот файл.
 *
 * **Экран своего счёта не ведёт:** разделы приходят словами — теми же строками, что уходят в
 * группу продаж, — а числа по гипотезам и итог считает сервер функциями экранов.
 */

import { download, request } from './client';
import type { Downloaded } from './client';

/** Числа гипотезы или итога за неделю — те же, что в разделах и выгрузке. */
export interface WeeklyNumbers {
  loaded: number;
  ready: number;
  rejected: number;
  cleaning: number;
  /** Воронка недели — лиды по первому письму, как на вкладке «Воронка» за те же даты. */
  queued: number;
  sent: number;
  delivered: number;
  bounced: number;
  answered: number;
  handed_off: number;
  /** Писем цепочки за неделю по шагам, первое — первым. */
  letters: number[];
  /** Ответов за неделю — без автоответов. */
  answers: number;
  /** Вид ответа → сколько; `auto_reply` — автоответы. */
  replies: Record<string, number>;
  active: number;
  silent: number;
  waiting: number;
  /** Передано телемаркетологу за неделю — по времени передачи. */
  handed: number;
  unwritten: number;
  letters_queued: number;
}

export interface WeeklyRow {
  hypothesis_id: number;
  name: string;
  numbers: WeeklyNumbers;
}

/** Раздел отчёта: первая строка — итог, следующие — подробности. */
export interface WeeklySection {
  key: string;
  title: string;
  lines: string[];
}

/** Как отчёт недели ушёл в группу продаж. */
export interface WeeklyDelivery {
  status: 'sent' | 'pending' | 'undelivered' | 'none' | 'off';
  sent_at: string | null;
  /** Когда повтор; пусто у «не доставлен» — повторов больше нет. */
  due_at: string | null;
  error: string | null;
  /** Когда отчёт уходит в группу — словами; пусто — не уходит. */
  schedule: string | null;
}

export interface SalesWeeklyView {
  /** Понедельник недели и её воскресенье, `YYYY-MM-DD`. */
  week: string;
  last_day: string;
  /** Прошедшие недели для выбора, новые первыми. */
  weeks: string[];
  built_at: string;
  sections: WeeklySection[];
  rows: WeeklyRow[];
  total: WeeklyNumbers;
  delivery: WeeklyDelivery;
}

function weekQuery(week: string): string {
  return week === '' ? '' : `?week=${encodeURIComponent(week)}`;
}

/** Отчёт недели; пусто — прошлая неделя. */
export function readSalesWeekly(week: string): Promise<SalesWeeklyView> {
  return request<SalesWeeklyView>(`/sales/weekly${weekQuery(week)}`);
}

/** Тот же отчёт файлом CSV — для Excel. */
export function downloadSalesWeekly(week: string): Promise<Downloaded> {
  return download(`/sales/weekly.csv${weekQuery(week)}`);
}
