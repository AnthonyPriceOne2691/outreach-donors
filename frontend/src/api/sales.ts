/**
 * Продажи: гипотезы, лиды, загрузка базы, база знаний, отправитель, цепочка писем,
 * очередь писем, воронка и очистка лидов.
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
import type {
  AgentView,
  ChainPreviewBody,
  ChainPreviewView,
  ChainStepBody,
  ChainStepCard,
  ChainView,
  HypothesesView,
  HypothesisBody,
  HypothesisCard,
  IntakeView,
  KbEntryBody,
  KbEntryCard,
  KbView,
  LeadField,
  LeadState,
  LeadsView,
  SalesCleanBody,
  SalesCleanView,
  SalesFunnelView,
  SalesQueueBody,
  SalesQueueView,
  SenderBody,
  SenderView,
} from './salesTypes';
import type { BuildQueued } from './types';

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

/** Завести гипотезу из окна «Новая гипотеза». Ответ — карточка той же формы, что в списке. */
export function addHypothesis(body: HypothesisBody): Promise<HypothesisCard> {
  return request<HypothesisCard>('/sales/hypotheses', { method: 'POST', body });
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

/** Текст отказа из тела ответа: строкой — как есть; списком (отказ разбора
 *  формы) — первая причина: человеку нужна причина, а не схема. Нет ни того,
 *  ни другого — `null`, и остаётся код ответа. */
function detailFrom(body: unknown): string | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null;
  const { detail } = body;
  if (typeof detail === 'string') return detail;
  const first: unknown = Array.isArray(detail) ? detail[0] : null;
  return first && typeof first === 'object' && 'msg' in first ? String(first.msg) : null;
}

/** Отказ сервера на форму — тем же исключением и с тем же текстом, что у `request`. */
async function refused(response: Response): Promise<ApiError> {
  // Тело не разобралось — отвечал не наш обработчик ошибок: остаётся код ответа.
  const body: unknown = await response.json().catch(() => null);
  const detail = detailFrom(body) ?? `Сервер ответил ${response.status}`;
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

/** База знаний: все записи — включённые и нет — и версия, которую видит агент. */
export function listKb(): Promise<KbView> {
  return request<KbView>('/sales/kb');
}

/** Что увидит агент: только включённые записи, той же выборкой, что у агента. */
export function previewKb(): Promise<AgentView> {
  return request<AgentView>('/sales/kb/preview');
}

export function addKbEntry(body: KbEntryBody): Promise<KbEntryCard> {
  return request<KbEntryCard>('/sales/kb', { method: 'POST', body });
}

/** Правка записи: включение и выключение — тоже правка (`active`). Журнал сервер
 *  пишет только по полям, которые действительно изменились. */
export function changeKbEntry(id: number, body: Partial<KbEntryBody>): Promise<KbEntryCard> {
  return request<KbEntryCard>(`/sales/kb/${id}`, { method: 'PATCH', body });
}

export function readSender(): Promise<SenderView> {
  return request<SenderView>('/sales/sender');
}

/** Отправитель целиком: пустое поле уходит `null` — «не задано». */
export function saveSender(body: SenderBody): Promise<SenderView> {
  return request<SenderView>('/sales/sender', { method: 'POST', body });
}

/** Набор шаблонов цепочки и цепочки по языкам: общий (`null`) или гипотезы. */
export function readChain(hypothesis: number | null): Promise<ChainView> {
  return request<ChainView>(
    hypothesis === null ? '/sales/chain' : `/sales/chain?hypothesis=${hypothesis}`,
  );
}

/** Записать шаг набора: завести или поправить — ключ «набор, шаг, язык» знает сервер. */
export function saveChainStep(body: ChainStepBody): Promise<ChainStepCard> {
  return request<ChainStepCard>('/sales/chain', { method: 'POST', body });
}

/** Письмо глазами адресата: выдуманные значения, подпись и адрес из настроек. Без записи. */
export function previewChainStep(body: ChainPreviewBody): Promise<ChainPreviewView> {
  return request<ChainPreviewView>('/sales/chain/preview', { method: 'POST', body });
}

/** Очередь писем продаж гипотезы: подключены ли продажи, цепочки, сколько ждёт. */
export function readSalesQueue(hypothesis: number): Promise<SalesQueueView> {
  return request<SalesQueueView>(`/sales/queue?hypothesis=${hypothesis}`);
}

/** Поставить сборку очереди в очередь задач. Ничего не отправляет. */
export function buildSalesQueue(body: SalesQueueBody): Promise<BuildQueued> {
  return request<BuildQueued>('/sales/queue', { method: 'POST', body });
}

/** Перед очисткой: сколько лидов гипотезы ждут и платная ли проверка адресов — на момент
 *  нажатия: окно подтверждения называет расход по свежему числу. Без записи. */
export function readSalesClean(hypothesis: number): Promise<SalesCleanView> {
  return request<SalesCleanView>(`/sales/clean?hypothesis=${hypothesis}`);
}

/** Поставить очистку лидов гипотезы в очередь задач. Писем не пишет и не отправляет. */
export function cleanSalesLeads(body: SalesCleanBody): Promise<BuildQueued> {
  return request<BuildQueued>('/sales/clean', { method: 'POST', body });
}

/** Запрос воронки: гипотеза и период — моменты ISO с поясом, `until` не включая.
 *  Незаданное условие в адрес не пишется: без него — все гипотезы и всё время. */
export interface FunnelQuery {
  hypothesis?: number;
  since?: string;
  until?: string;
}

/** Воронка продаж: лиды на каждом шаге по гипотезам и итог — за период. Без записи. */
export function readSalesFunnel(query: FunnelQuery): Promise<SalesFunnelView> {
  const params = new URLSearchParams();
  if (query.hypothesis !== undefined) params.set('hypothesis', String(query.hypothesis));
  if (query.since !== undefined) params.set('since', query.since);
  if (query.until !== undefined) params.set('until', query.until);
  const search = params.toString();
  return request<SalesFunnelView>(search === '' ? '/sales/funnel' : `/sales/funnel?${search}`);
}
