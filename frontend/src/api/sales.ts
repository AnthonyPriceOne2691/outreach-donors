/**
 * Продажи: гипотезы, лиды, загрузка базы, база знаний, отправитель, цепочка писем, очередь
 * писем, воронка и очистка лидов. Правка лида — `salesLeads.ts`, стоп-лист — `salesStoplist.ts`.
 *
 * Списки — обычным `request`. Загрузка — файлом или ссылкой на Google-таблицу
 * формой `multipart/form-data` (контракт сервера 1.3c): байты файла уходят как
 * есть, и файл не в UTF-8 сервер отклоняет словами, а не кракозябрами. Форма
 * уходит общим `upload` из `client.ts` — тем же разбором отказа, что у всех
 * запросов. До проверки прода 10.10.2026 у формы был свой `fetch` с копией
 * разбора (срез 1.5 ждал общего пути — он появился): копия не знала ни признака
 * отказа доступа, ни обрыва связи.
 *
 * Состояния между шагами мастера сервер не держит: источник уходит и на
 * предпросмотр, и на загрузку, таблица по ссылке читается заново.
 */

import { request, upload } from './client';
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
  SalesLeadFollowupsView,
  SalesQueueBody,
  SalesQueueView,
  SenderBody,
  SenderView,
} from './salesTypes';
import type { SalesCleanBody, SalesCleanView } from './salesCleanTypes';
import type { SalesFunnelView } from './salesFunnelTypes';
import type { BuildQueued } from './types';

/** Фильтры под колонками и страница. Размера страницы здесь нет: его называет
 *  сервер и возвращает в ответе (`limit`). Имена — те же, что в адресе экрана, кроме
 *  периода добавления: в адресе он днями (`added_from`, `added_to`), здесь — моментами ISO
 *  с поясом, как у воронки; `added_until` — не включая. */
export interface LeadsQuery {
  state?: LeadState;
  reason?: string;
  hypothesis?: number;
  search?: string;
  added_since?: string;
  added_until?: string;
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

/** Язык (код) и страна или пояс по умолчанию на эту загрузку: сервер кладёт их только туда,
 *  где колонка пуста. Пусто — без умолчания. */
export interface ImportDefaults {
  language: string | null;
  place: string;
}

export interface ImportOptions {
  /** Поле → колонка с нуля. Правка руками заменяет угаданное целиком. */
  mapping?: Partial<Record<LeadField, number>>;
  /** Первая строка — заголовок. Не задано — сервер угадывает по именам колонок. */
  header?: boolean;
  defaults?: ImportDefaults;
}

/** Умолчания — в форму, только заданные: пустое поле сервер прочёл бы как «не код языка». */
function setDefaults(form: FormData, given: ImportDefaults | undefined): void {
  const place = given?.place.trim() ?? '';
  if (given?.language) form.set('default_language', given.language);
  if (place !== '') form.set('default_place', place);
}

function formOf(source: ImportSource, options: ImportOptions, hypothesisId?: number): FormData {
  const form = new FormData();
  if (source.file !== null) form.set('file', source.file, source.file.name);
  else form.set('link', source.link.trim());
  if (options.mapping !== undefined) form.set('mapping', JSON.stringify(options.mapping));
  if (options.header !== undefined) form.set('header', String(options.header));
  setDefaults(form, options.defaults);
  if (hypothesisId !== undefined) form.set('hypothesis_id', String(hypothesisId));
  return form;
}

/** Сухой прогон: что станет лидами и что нет. В базу ничего не пишется. */
export function previewImport(source: ImportSource, options: ImportOptions): Promise<IntakeView> {
  return upload<IntakeView>('/sales/import/preview', formOf(source, options));
}

/** Записать лидов в гипотезу. Ответ — тот же предпросмотр с числом `loaded`. */
export function loadImport(
  source: ImportSource,
  options: ImportOptions,
  hypothesisId: number,
): Promise<IntakeView> {
  return upload<IntakeView>('/sales/import', formOf(source, options, hypothesisId));
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

/** Добивки лида, которые ещё уйдут, — какими они уйдут: экран очереди под строкой письма. */
export function readLeadFollowups(lead: number): Promise<SalesLeadFollowupsView> {
  return request<SalesLeadFollowupsView>(`/sales/leads/${lead}/followups`);
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
