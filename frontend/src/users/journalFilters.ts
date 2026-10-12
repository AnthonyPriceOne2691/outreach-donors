/**
 * Фильтры журнала действий и страница — в адресе экрана (`?action=…&author=…&from=…&to=…&page=2`).
 *
 * **Адрес, а не состояние компонента**: ссылку на «входы Ивана за вторник» отправляют
 * коллеге, а обновление вкладки и «назад» не сбрасывают разбор. **Адрес — чужой ввод**:
 * незнакомое действие, не номер учётки, не дата — «не сужать», а не отказ сервера.
 *
 * **День — по часам человека.** «С 5-го по 6-е» — с начала 5-го до начала 7-го в его
 * поясе; сервер получает моменты с поясом. Сутки по UTC сдвинули бы вечер в завтра.
 */

import { AUDIT_ACTIONS } from '../api/auditLabels';
import type { AuditAction } from '../api/journal';

export interface JournalFilters {
  action: AuditAction | null;
  /** Номер учётки автора. */
  author: number | null;
  /** Первый и последний день отрезка, «ГГГГ-ММ-ДД» — как отдаёт поле даты. Пусто — без края. */
  from: string;
  to: string;
  page: number;
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Дата поля — настоящий день, а не «2026-02-31». */
function isDay(text: string): boolean {
  if (!DAY.test(text)) return false;
  const day = new Date(`${text}T00:00:00`);
  return !Number.isNaN(day.getTime()) && sameDay(day, text);
}

function sameDay(day: Date, text: string): boolean {
  const [year, month, date] = text.split('-').map(Number);
  return day.getFullYear() === year && day.getMonth() + 1 === month && day.getDate() === date;
}

function isAction(code: string | null): code is AuditAction {
  return code !== null && Object.hasOwn(AUDIT_ACTIONS, code);
}

/** Фильтры из адреса. Негодное значение — «не сужать». */
export function readJournalFilters(params: URLSearchParams): JournalFilters {
  const action = params.get('action');
  const author = Number(params.get('author'));
  const page = Number(params.get('page'));
  const from = params.get('from') ?? '';
  const to = params.get('to') ?? '';
  return {
    action: isAction(action) ? action : null,
    author: Number.isInteger(author) && author >= 1 ? author : null,
    from: isDay(from) ? from : '',
    to: isDay(to) ? to : '',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

/** Фильтры — в адрес. Пустое не пишется: адрес короче, ссылка чище. */
export function journalParams(filters: JournalFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.action !== null) params.set('action', filters.action);
  if (filters.author !== null) params.set('author', String(filters.author));
  if (filters.from !== '') params.set('from', filters.from);
  if (filters.to !== '') params.set('to', filters.to);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

/** Почему отрезок не фильтр — словами под полем. `null` — годится. */
export function rangeProblem(from: string, to: string): string | null {
  return from !== '' && to !== '' && from > to ? 'Раньше первого дня — поменяйте даты' : null;
}

/** Начало дня по часам человека — моментом с поясом. */
function startOf(day: string, shift = 0): string {
  const moment = new Date(`${day}T00:00:00`);
  moment.setDate(moment.getDate() + shift);
  return moment.toISOString();
}

/** Края отрезка для сервера: с начала первого дня — до начала дня после последнего. */
export function momentsOf(
  from: string,
  to: string,
): { since: string | null; until: string | null } {
  return {
    since: from === '' ? null : startOf(from),
    until: to === '' ? null : startOf(to, 1),
  };
}
