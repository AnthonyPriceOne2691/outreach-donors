/**
 * Продажи: гипотезы, лиды и загрузка базы.
 *
 * Списки — обычным `request`. Загрузка — файлом или ссылкой на Google-таблицу
 * формой `multipart/form-data` (контракт сервера 1.3c): байты файла уходят как
 * есть, и файл не в UTF-8 сервер отклоняет словами, а не кракозябрами. Общий
 * `request` умеет только JSON, а `client.ts` — общий код вне точек среза 1.5,
 * поэтому отправка формы живёт здесь, с тем же разбором отказа: текст сервера
 * целиком, 401 — пропуск сброшен, 403 — названо действие. Перенести форму
 * в `request` — отдельным общим PR (отчёт среза 1.5).
 *
 * Состояния между шагами мастера сервер не держит: источник уходит и на
 * предпросмотр, и на загрузку, таблица по ссылке читается заново.
 */

import { clearToken, readToken } from '../auth/session';
import { ApiError, AuthError, DeniedError, request } from './client';
import type { HypothesesView, IntakeView, LeadField, LeadState, LeadsView } from './salesTypes';

/** Фильтры под колонками и страница. Размера страницы здесь нет: его называет
 *  сервер и возвращает в ответе (`limit`). Имена — те же, что в адресе экрана. */
export interface LeadsQuery {
  state?: LeadState;
  reason?: string;
  hypothesis?: number;
  search?: string;
  page?: number;
}

export function listHypotheses(): Promise<HypothesesView> {
  return request<HypothesesView>('/sales/hypotheses');
}

/** Запрос → строка адреса: незаданные условия не пишутся вовсе — `?reason=`
 *  сервер прочёл бы как пустой код, а не как «без причины». */
function searchOf(query: LeadsQuery): string {
  const given = Object.entries(query).filter(([, value]) => value !== undefined && value !== '');
  return new URLSearchParams(given.map(([key, value]) => [key, String(value)])).toString();
}

export function listLeads(query: LeadsQuery): Promise<LeadsView> {
  const asked = searchOf(query);
  return request<LeadsView>(asked === '' ? '/sales/leads' : `/sales/leads?${asked}`);
}

/** Откуда база: файл с диска или ссылка на Google-таблицу — одно из двух. */
export interface ImportSource {
  file: File | null;
  link: string;
}

export interface ImportOptions {
  /** Поле → колонка с нуля. Правка руками заменяет угаданное целиком. */
  mapping?: Partial<Record<LeadField, number>>;
  /** Первая строка — заголовок. Не задано — сервер угадывает по именам колонок. */
  header?: boolean;
}

function formOf(source: ImportSource, options: ImportOptions, hypothesisId?: number): FormData {
  const form = new FormData();
  if (source.file !== null) form.set('file', source.file, source.file.name);
  else form.set('link', source.link.trim());
  if (options.mapping !== undefined) form.set('mapping', JSON.stringify(options.mapping));
  if (options.header !== undefined) form.set('header', String(options.header));
  if (hypothesisId !== undefined) form.set('hypothesis_id', String(hypothesisId));
  return form;
}

/** Отказ сервера на форму — тем же исключением и с тем же текстом, что у `request`. */
async function refused(response: Response): Promise<ApiError> {
  let detail = `Сервер ответил ${response.status}`;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      const found: unknown = body.detail;
      if (typeof found === 'string') detail = found;
      else if (Array.isArray(found) && found.length > 0) {
        // Отказ разбора формы приходит списком — человеку нужна причина, а не схема.
        const first: unknown = found[0];
        if (first && typeof first === 'object' && 'msg' in first) detail = String(first.msg);
      }
    }
  } catch {
    // Тело не разобралось — отвечал не наш обработчик ошибок.
  }
  if (response.status === 401) {
    clearToken();
    return new AuthError(detail);
  }
  if (response.status === 403) return new DeniedError(detail);
  return new ApiError(response.status, detail);
}

async function sendForm(path: string, form: FormData): Promise<IntakeView> {
  // Тип содержимого не задаётся: границу частей формы браузер пишет сам.
  const headers: Record<string, string> = {};
  const token = readToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const response = await fetch(`/api${path}`, { method: 'POST', headers, body: form });
  if (!response.ok) throw await refused(response);
  return (await response.json()) as IntakeView;
}

/** Сухой прогон: что станет лидами и что нет. В базу ничего не пишется. */
export function previewImport(source: ImportSource, options: ImportOptions): Promise<IntakeView> {
  return sendForm('/sales/import/preview', formOf(source, options));
}

/** Записать лидов в гипотезу. Ответ — тот же предпросмотр с числом `loaded`. */
export function loadImport(
  source: ImportSource,
  options: ImportOptions,
  hypothesisId: number,
): Promise<IntakeView> {
  return sendForm('/sales/import', formOf(source, options, hypothesisId));
}
